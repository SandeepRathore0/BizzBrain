"""Integration tests for the unified completion service (complete_capture).

Verifies:
1. Payload built from a real completed payment validates against contracts/events/PaymentCaptured.v1.json.
2. Capture then webhook yields exactly one outbox row.
3. Webhook then capture yields exactly one outbox row.
4. Concurrent completion attempts (20 trials) yield exactly one outbox row.
5. Amount mismatch moves payment to REQUIRES_REVIEW, produces NO outbox row, and raises alert AMOUNT_MISMATCH.
"""
import asyncio
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import select

from app.core.alerts import register_alert_listener, unregister_alert_listener
from app.core.database import get_sessionmaker
from app.models.outbox import OutboxEvent
from app.models.payment import Payment
from app.models.provider_reference import PaymentProviderReference
from app.services.completion_service import complete_capture

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "events" / "PaymentCaptured.v1.json"


@pytest.fixture
def contract_validator():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


async def _create_test_payment(business_id: uuid.UUID, amount: Decimal = Decimal("100.00"), currency: str = "USD") -> Payment:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        payment_id = uuid.uuid4()
        now_utc = datetime.now(timezone.utc)
        payment = Payment(
            payment_id=payment_id,
            business_id=business_id,
            invoice_id=uuid.uuid4(),
            invoice_number="INV-COMPL-001",
            amount=amount,
            currency=currency,
            provider="PAYPAL",
            status="CREATED",
            allocation_status="NOT_APPLICABLE",
            idempotency_key=uuid.uuid4(),
            initiated_by_type="USER",
            expires_at=now_utc,
            created_at=now_utc,
            updated_at=now_utc,
        )
        session.add(payment)
        ref = PaymentProviderReference(
            id=uuid.uuid4(),
            payment_id=payment_id,
            provider_order_id=f"ORDER-{payment_id}",
            paypal_request_id=uuid.uuid4(),
            capture_request_id=uuid.uuid4(),
        )
        session.add(ref)
        await session.commit()
        return payment


@pytest.mark.asyncio
async def test_real_completed_payment_payload_validates_against_contract(contract_validator):
    """Payload built from a real completed payment validates cleanly against the JSON schema."""
    business_id = uuid.uuid4()
    payment = await _create_test_payment(business_id, amount=Decimal("250.50"), currency="EUR")
    cap_id = f"CAP-REAL-{uuid.uuid4().hex[:10]}"

    res = await complete_capture(
        business_id=business_id,
        payment_id=payment.payment_id,
        captured_amount="250.50",
        captured_currency="EUR",
        provider_capture_id=cap_id,
        raw_response={"id": cap_id, "status": "COMPLETED"},
        actor_type="STAFF",
        request_id="req-test-real-payload",
    )

    assert res.status == "COMPLETED"
    assert res.allocation_status == "PENDING"
    assert res.outbox_event_id is not None

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = select(OutboxEvent).where(
            OutboxEvent.aggregate_id == payment.payment_id,
            OutboxEvent.event_type == "PaymentCaptured",
        )
        outbox_res = await session.execute(stmt)
        outbox_row = outbox_res.scalar_one_or_none()
        assert outbox_row is not None
        assert outbox_row.status == "PENDING"

        # Validate against schema
        errors = list(contract_validator.iter_errors(outbox_row.payload))
        assert errors == [], f"Validation errors on generated payload: {errors}"
        assert outbox_row.payload["amount"] == "250.50"
        assert outbox_row.payload["currency"] == "EUR"
        assert outbox_row.payload["provider_capture_id"] == cap_id


@pytest.mark.asyncio
async def test_capture_then_webhook_gives_single_outbox_row():
    """Capture API followed by Webhook delivers exactly one outbox row."""
    business_id = uuid.uuid4()
    payment = await _create_test_payment(business_id, amount=Decimal("150.00"), currency="USD")
    cap_id = f"CAP-SEQ-{uuid.uuid4().hex[:10]}"

    # 1. Capture API completes
    res1 = await complete_capture(
        business_id=business_id,
        payment_id=payment.payment_id,
        captured_amount="150.00",
        captured_currency="USD",
        provider_capture_id=cap_id,
        actor_type="STAFF",
    )
    assert res1.status == "COMPLETED"
    assert not res1.already_completed

    # 2. Webhook arrives later
    res2 = await complete_capture(
        business_id=business_id,
        payment_id=payment.payment_id,
        captured_amount="150.00",
        captured_currency="USD",
        provider_capture_id=cap_id,
        actor_type="WEBHOOK",
    )
    assert res2.status == "COMPLETED"
    assert res2.already_completed

    # Assert exactly ONE outbox row exists
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = select(OutboxEvent).where(
            OutboxEvent.aggregate_id == payment.payment_id,
            OutboxEvent.event_type == "PaymentCaptured",
        )
        rows = (await session.execute(stmt)).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_webhook_then_capture_gives_single_outbox_row():
    """Webhook followed by Capture API delivers exactly one outbox row."""
    business_id = uuid.uuid4()
    payment = await _create_test_payment(business_id, amount=Decimal("75.00"), currency="GBP")
    cap_id = f"CAP-WEB-{uuid.uuid4().hex[:10]}"

    # 1. Webhook completes first
    res1 = await complete_capture(
        business_id=business_id,
        payment_id=payment.payment_id,
        captured_amount="75.00",
        captured_currency="GBP",
        provider_capture_id=cap_id,
        actor_type="WEBHOOK",
    )
    assert res1.status == "COMPLETED"
    assert not res1.already_completed

    # 2. Capture API completes second
    res2 = await complete_capture(
        business_id=business_id,
        payment_id=payment.payment_id,
        captured_amount="75.00",
        captured_currency="GBP",
        provider_capture_id=cap_id,
        actor_type="STAFF",
    )
    assert res2.status == "COMPLETED"
    assert res2.already_completed

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = select(OutboxEvent).where(
            OutboxEvent.aggregate_id == payment.payment_id,
            OutboxEvent.event_type == "PaymentCaptured",
        )
        rows = (await session.execute(stmt)).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_concurrent_completion_trials():
    """20 trials of concurrent racing completions give exactly one outbox row each."""
    business_id = uuid.uuid4()
    session_factory = get_sessionmaker()

    for i in range(20):
        payment = await _create_test_payment(business_id, amount=Decimal("10.00"), currency="USD")
        cap_id = f"CAP-RACE-{uuid.uuid4().hex[:10]}"

        # Run two competing completion tasks concurrently
        results = await asyncio.gather(
            complete_capture(
                business_id=business_id,
                payment_id=payment.payment_id,
                captured_amount="10.00",
                captured_currency="USD",
                provider_capture_id=cap_id,
                actor_type="WEBHOOK",
            ),
            complete_capture(
                business_id=business_id,
                payment_id=payment.payment_id,
                captured_amount="10.00",
                captured_currency="USD",
                provider_capture_id=cap_id,
                actor_type="STAFF",
            ),
            return_exceptions=True,
        )

        for r in results:
            assert not isinstance(r, Exception), f"Concurrent task failed with: {r}"
            assert r.status == "COMPLETED"

        # Check DB has exactly ONE outbox event
        async with session_factory() as session:
            stmt = select(OutboxEvent).where(
                OutboxEvent.aggregate_id == payment.payment_id,
                OutboxEvent.event_type == "PaymentCaptured",
            )
            rows = (await session.execute(stmt)).scalars().all()
            assert len(rows) == 1, f"Trial {i} produced {len(rows)} outbox events instead of 1"


@pytest.mark.asyncio
async def test_amount_mismatch_gives_requires_review_and_no_outbox_row():
    """Amount or currency mismatch places payment in REQUIRES_REVIEW, writes no outbox row, and alerts."""
    business_id = uuid.uuid4()
    payment = await _create_test_payment(business_id, amount=Decimal("100.00"), currency="USD")

    emitted_alerts = []

    def alert_listener(code, fields):
        emitted_alerts.append((code, fields))

    register_alert_listener(alert_listener)
    try:
        res = await complete_capture(
            business_id=business_id,
            payment_id=payment.payment_id,
            captured_amount="99.00",  # Mismatch: expected 100.00
            captured_currency="USD",
            provider_capture_id="CAP-MISMATCH-1",
            actor_type="WEBHOOK",
        )

        assert res.is_mismatch is True
        assert res.status == "REQUIRES_REVIEW"

        # Verify DB state
        session_factory = get_sessionmaker()
        async with session_factory() as session:
            # Check Payment status
            p = await session.get(Payment, payment.payment_id)
            assert p is not None
            assert p.status == "REQUIRES_REVIEW"
            assert "AMOUNT_MISMATCH" in p.status_reason

            # Check NO Outbox row created
            stmt = select(OutboxEvent).where(
                OutboxEvent.aggregate_id == payment.payment_id,
                OutboxEvent.event_type == "PaymentCaptured",
            )
            rows = (await session.execute(stmt)).scalars().all()
            assert len(rows) == 0

        # Verify alert was raised
        assert any(code == "AMOUNT_MISMATCH" for code, _ in emitted_alerts)

    finally:
        unregister_alert_listener(alert_listener)
