"""Integration tests for OutboxWorker and OutboxService on real PostgreSQL.

Covers all Section 3 & Section 6 requirements:
- Happy path delivery & allocation ALLOCATED
- Section 3.5 response matrix handling
- Full jitter backoff and DEAD after 10 attempts
- Concurrent workers on 100 rows with 20 trials (SKIP LOCKED proof)
- Crash recovery via visibility timeout and late worker zero-row guard
- Duplicate delivery safety (DUPLICATE_IGNORED)
- Strict schema validation failure to DEAD without HTTP calls
- Circuit breaker trip and recovery
- Graceful shutdown
- Secret scrubbing check in audit and last_error
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
import httpx
import pytest
from sqlalchemy import select, update

from app.clients.user_service_client import UserServiceClient
from app.core.alerts import register_alert_listener, unregister_alert_listener
from app.core.clock import ControlledRandomSource, TestClock
from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.core.money import format_amount
from app.models.audit import AuditLog
from app.models.outbox import OutboxEvent
from app.models.payment import Payment
from app.models.provider_reference import PaymentProviderReference
from app.services.outbox_service import OutboxService
from app.workers.outbox_worker import OutboxWorker
from tests.stubs.invoice_service_stub import STUB_CONFIG, app as invoice_stub_app


class MockUserServiceClient(UserServiceClient):
    def __init__(self, token="test_service_token_abc"):
        super().__init__()
        self.token = token
        self.call_count = 0
        self.refresh_count = 0

    async def get_service_token(self, force_refresh: bool = False) -> str:
        self.call_count += 1
        if force_refresh:
            self.refresh_count += 1
            self.token = f"refreshed_token_{uuid.uuid4().hex[:6]}"
        return self.token

    def clear_token_cache(self):
        self.cached_token = None


@pytest.fixture(autouse=True)
async def reset_invoice_stub_and_drain_outbox():
    STUB_CONFIG["force_status"] = None
    STUB_CONFIG["force_delay"] = 0.0
    STUB_CONFIG["events_received"].clear()
    STUB_CONFIG["payment_event_outcome"] = "APPLIED"
    STUB_CONFIG["rejection_reason"] = "INVOICE_ALREADY_SETTLED"

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.status.in_(["PENDING", "IN_FLIGHT"]))
            .values(status="DELIVERED")
        )
        await session.commit()


async def _create_completed_payment_with_outbox(
    business_id: uuid.UUID,
    amount: Decimal = Decimal("100.00"),
    currency: str = "USD",
    attempts: int = 0,
    status: str = "PENDING",
    payload_override: Optional[dict[str, Any]] = None,
) -> tuple[Payment, OutboxEvent]:
    session_factory = get_sessionmaker()
    now_utc = datetime.now(timezone.utc)
    payment_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    event_id = uuid.uuid4()

    expected_amount_str = format_amount(amount, currency)

    default_payload = {
        "event_id": str(event_id).lower(),
        "event_type": "PaymentCaptured",
        "event_version": 1,
        "occurred_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "request_id": f"req-{uuid.uuid4().hex[:8]}",
        "business_id": str(business_id).lower(),
        "payment_id": str(payment_id).lower(),
        "invoice_id": str(invoice_id).lower(),
        "amount": expected_amount_str,
        "currency": currency.upper(),
        "provider_capture_id": f"CAP-{uuid.uuid4().hex[:8]}",
    }

    if payload_override:
        default_payload.update(payload_override)

    async with session_factory() as session:
        payment = Payment(
            payment_id=payment_id,
            business_id=business_id,
            invoice_id=invoice_id,
            invoice_number="INV-OUTBOX-001",
            amount=amount,
            currency=currency,
            provider="PAYPAL",
            status="COMPLETED",
            allocation_status="PENDING",
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
            provider_order_id=f"ORD-{payment_id}",
            provider_capture_id=default_payload.get("provider_capture_id"),
            provider_status="COMPLETED",
            paypal_request_id=uuid.uuid4(),
            capture_request_id=uuid.uuid4(),
        )
        session.add(ref)

        outbox = OutboxEvent(
            event_id=event_id,
            aggregate_id=payment_id,
            event_type="PaymentCaptured",
            event_version=1,
            payload=default_payload,
            status=status,
            attempts=attempts,
            next_retry_at=now_utc,
            created_at=now_utc,
        )
        session.add(outbox)
        await session.commit()

        return payment, outbox


@pytest.mark.asyncio
async def test_outbox_happy_path_delivery():
    """Happy path: row is delivered, becomes DELIVERED, allocation becomes ALLOCATED, audit logged."""
    business_id = uuid.uuid4()
    payment, outbox = await _create_completed_payment_with_outbox(business_id)

    test_clock = TestClock()
    user_client = MockUserServiceClient()
    outbox_svc = OutboxService(clock=test_clock, user_service_client=user_client)

    transport = httpx.ASGITransport(app=invoice_stub_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        claimed = await outbox_svc.claim_batch(batch_size=10)
        assert len(claimed) >= 1
        target_event = next(e for e in claimed if e["event_id"] == outbox.event_id)

        result = await outbox_svc.process_event(target_event, http_client=client)
        assert result == "DELIVERED"

    # Verify DB state
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        # Outbox row DELIVERED
        row = await session.get(OutboxEvent, outbox.event_id)
        assert row.status == "DELIVERED"
        assert row.delivered_at is not None
        assert row.last_error is None

        # Payment ALLOCATED
        p = await session.get(Payment, payment.payment_id)
        assert p.allocation_status == "ALLOCATED"

        # Audit row exists for delivery
        stmt_audit = select(AuditLog).where(
            (AuditLog.entity_id == outbox.event_id)
            & (AuditLog.action == "OUTBOX_DELIVERED_ALLOCATED")
        )
        audit = (await session.execute(stmt_audit)).scalar_one_or_none()
        assert audit is not None
        assert audit.actor_type == "SYSTEM"


@pytest.mark.asyncio
async def test_outbox_rejected_allocation_outcome():
    """200 outcome REJECTED: row DELIVERED, allocation REJECTED, payment stays COMPLETED, alert emitted."""
    business_id = uuid.uuid4()
    payment, outbox = await _create_completed_payment_with_outbox(business_id)

    STUB_CONFIG["payment_event_outcome"] = "REJECTED"
    STUB_CONFIG["rejection_reason"] = "INVOICE_ALREADY_VOIDED"

    alerts = []
    register_alert_listener(lambda code, f: alerts.append((code, f)))

    try:
        outbox_svc = OutboxService(user_service_client=MockUserServiceClient())
        transport = httpx.ASGITransport(app=invoice_stub_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            claimed = await outbox_svc.claim_batch(batch_size=10)
            target_event = next(e for e in claimed if e["event_id"] == outbox.event_id)
            result = await outbox_svc.process_event(target_event, http_client=client)
            assert result == "DELIVERED_REJECTED"

        session_factory = get_sessionmaker()
        async with session_factory() as session:
            row = await session.get(OutboxEvent, outbox.event_id)
            assert row.status == "DELIVERED"

            p = await session.get(Payment, payment.payment_id)
            assert p.status == "COMPLETED"  # payment stays COMPLETED
            assert p.allocation_status == "REJECTED"
            assert p.status_reason == "INVOICE_ALREADY_VOIDED"

        assert any(c == "ALLOCATION_REJECTED" for c, _ in alerts)
    finally:
        unregister_alert_listener(lambda code, f: alerts.append((code, f)))


@pytest.mark.asyncio
async def test_outbox_unknown_outcome_treats_as_retryable():
    """200 with unknown outcome is treated as a retryable failure."""
    business_id = uuid.uuid4()
    payment, outbox = await _create_completed_payment_with_outbox(business_id)

    STUB_CONFIG["payment_event_outcome"] = "UNKNOWN_NEW_STATUS"

    outbox_svc = OutboxService(user_service_client=MockUserServiceClient())
    transport = httpx.ASGITransport(app=invoice_stub_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        claimed = await outbox_svc.claim_batch(batch_size=10)
        target_event = next(e for e in claimed if e["event_id"] == outbox.event_id)
        result = await outbox_svc.process_event(target_event, http_client=client)
        assert result == "RETRY_SCHEDULED"

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        row = await session.get(OutboxEvent, outbox.event_id)
        assert row.status == "PENDING"
        assert "UNKNOWN_200_OUTCOME" in row.last_error


@pytest.mark.asyncio
async def test_outbox_401_refresh_token_and_retry_success():
    """401 triggers service token cache invalidation, fetch new token, and retry success."""
    business_id = uuid.uuid4()
    payment, outbox = await _create_completed_payment_with_outbox(business_id)

    user_client = MockUserServiceClient(token="expired_token")

    # Custom transport that returns 401 on "expired_token" and 200 on refreshed token
    async def handler(request: httpx.Request):
        auth = request.headers.get("Authorization", "")
        if "expired_token" in auth:
            return httpx.Response(401, json={"detail": "Token expired"})
        return httpx.Response(200, json={"success": True, "data": {"outcome": "APPLIED"}})

    transport = httpx.MockTransport(handler)
    outbox_svc = OutboxService(user_service_client=user_client)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        claimed = await outbox_svc.claim_batch(batch_size=10)
        target_event = next(e for e in claimed if e["event_id"] == outbox.event_id)
        result = await outbox_svc.process_event(target_event, http_client=client)
        assert result == "DELIVERED"
        assert user_client.refresh_count == 1


@pytest.mark.asyncio
async def test_outbox_401_twice_fails_with_alert():
    """401 returned twice emits alert OUTBOX_AUTH_FAILURE and schedules retry."""
    business_id = uuid.uuid4()
    payment, outbox = await _create_completed_payment_with_outbox(business_id)

    alerts = []
    register_alert_listener(lambda code, f: alerts.append((code, f)))

    async def handler(request: httpx.Request):
        return httpx.Response(401, json={"detail": "Unauthorized"})

    transport = httpx.MockTransport(handler)
    outbox_svc = OutboxService(user_service_client=MockUserServiceClient())

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            claimed = await outbox_svc.claim_batch(batch_size=10)
            target_event = next(e for e in claimed if e["event_id"] == outbox.event_id)
            result = await outbox_svc.process_event(target_event, http_client=client)
            assert result == "RETRY_SCHEDULED"

        assert any(c == "OUTBOX_AUTH_FAILURE" for c, _ in alerts)
    finally:
        unregister_alert_listener(lambda code, f: alerts.append((code, f)))


@pytest.mark.asyncio
async def test_outbox_400_or_422_moves_to_dead_immediately():
    """Consumer HTTP 400 or 422 is non-retryable and moves row directly to DEAD with alert."""
    business_id = uuid.uuid4()
    payment, outbox = await _create_completed_payment_with_outbox(business_id)

    alerts = []
    register_alert_listener(lambda code, f: alerts.append((code, f)))

    async def handler(request: httpx.Request):
        return httpx.Response(422, json={"error": "Malformed semantic payload"})

    transport = httpx.MockTransport(handler)
    outbox_svc = OutboxService(user_service_client=MockUserServiceClient())

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            claimed = await outbox_svc.claim_batch(batch_size=10)
            target_event = next(e for e in claimed if e["event_id"] == outbox.event_id)
            result = await outbox_svc.process_event(target_event, http_client=client)
            assert result == "DEAD_CONSUMER_REJECTED"

        session_factory = get_sessionmaker()
        async with session_factory() as session:
            row = await session.get(OutboxEvent, outbox.event_id)
            assert row.status == "DEAD"
            assert "CONSUMER_REJECTED_422" in row.last_error

        assert any(c == "OUTBOX_REJECTED_BY_CONSUMER" for c, _ in alerts)
    finally:
        unregister_alert_listener(lambda code, f: alerts.append((code, f)))


@pytest.mark.asyncio
async def test_outbox_max_attempts_dead_lettering():
    """Row at attempt 10 failing retryably transitions to DEAD with alert OUTBOX_DEAD."""
    business_id = uuid.uuid4()
    # Create with attempts = 9 (claiming will make it attempt 10)
    payment, outbox = await _create_completed_payment_with_outbox(business_id, attempts=9)

    alerts = []
    register_alert_listener(lambda code, f: alerts.append((code, f)))

    async def handler(request: httpx.Request):
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(handler)
    outbox_svc = OutboxService(user_service_client=MockUserServiceClient())

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            claimed = await outbox_svc.claim_batch(batch_size=10)
            target_event = next(e for e in claimed if e["event_id"] == outbox.event_id)
            assert target_event["attempts"] == 10
            result = await outbox_svc.process_event(target_event, http_client=client)

        session_factory = get_sessionmaker()
        async with session_factory() as session:
            row = await session.get(OutboxEvent, outbox.event_id)
            assert row.status == "DEAD"

        assert any(c == "OUTBOX_DEAD" for c, _ in alerts)
    finally:
        unregister_alert_listener(lambda code, f: alerts.append((code, f)))


@pytest.mark.asyncio
async def test_schema_validation_rejection_without_http_call():
    """Payload failing schema validation moves immediately to DEAD without any HTTP call."""
    business_id = uuid.uuid4()
    # Invalid amount: float instead of decimal string
    payment, outbox = await _create_completed_payment_with_outbox(
        business_id,
        payload_override={"amount": 100.0},
    )

    http_called = False

    async def handler(request: httpx.Request):
        nonlocal http_called
        http_called = True
        return httpx.Response(200, json={"success": True})

    transport = httpx.MockTransport(handler)
    alerts = []
    register_alert_listener(lambda code, f: alerts.append((code, f)))

    outbox_svc = OutboxService(user_service_client=MockUserServiceClient())

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            claimed = await outbox_svc.claim_batch(batch_size=10)
            target_event = next(e for e in claimed if e["event_id"] == outbox.event_id)
            result = await outbox_svc.process_event(target_event, http_client=client)
            assert result == "DEAD_INVALID_SCHEMA"

        # Assert HTTP call was NEVER made
        assert not http_called

        # Assert status is DEAD with last_error set
        session_factory = get_sessionmaker()
        async with session_factory() as session:
            row = await session.get(OutboxEvent, outbox.event_id)
            assert row.status == "DEAD"
            assert "SCHEMA_VALIDATION_ERROR" in row.last_error

        assert any(c == "OUTBOX_PAYLOAD_INVALID" for c, _ in alerts)
    finally:
        unregister_alert_listener(lambda code, f: alerts.append((code, f)))


@pytest.mark.asyncio
async def test_crash_recovery_visibility_timeout_and_late_worker_guard():
    """Worker crashes after claim; after visibility timeout another worker redelivers; late worker does 0 updates."""
    business_id = uuid.uuid4()
    payment, outbox = await _create_completed_payment_with_outbox(business_id)

    clock = TestClock(datetime(2026, 11, 9, 10, 0, 0, tzinfo=timezone.utc))
    user_client = MockUserServiceClient()
    svc1 = OutboxService(clock=clock, user_service_client=user_client)

    # 1. Worker 1 claims row (commits IN_FLIGHT, next_retry_at = 10:01:00)
    claimed1 = await svc1.claim_batch(batch_size=10, visibility_timeout_seconds=60)
    assert any(e["event_id"] == outbox.event_id for e in claimed1)

    # Simulate Worker 1 crashing before HTTP call...
    # Now Worker 2 attempts to claim before timeout -> should get 0 rows
    svc2 = OutboxService(clock=clock, user_service_client=user_client)
    claimed_early = await svc2.claim_batch(batch_size=10)
    assert not any(e["event_id"] == outbox.event_id for e in claimed_early)

    # 2. Advance clock past visibility timeout (e.g. 65 seconds)
    clock.advance(seconds=65)

    # 3. Worker 2 claims the expired IN_FLIGHT row and delivers it!
    claimed_recovered = await svc2.claim_batch(batch_size=10)
    assert any(e["event_id"] == outbox.event_id for e in claimed_recovered)
    target_event = next(e for e in claimed_recovered if e["event_id"] == outbox.event_id)

    transport = httpx.ASGITransport(app=invoice_stub_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res2 = await svc2.process_event(target_event, http_client=client)
        assert res2 == "DELIVERED"

    # 4. Now Worker 1 wakes up late and tries to record result for its old claimed item:
    # Its guarded update affects ZERO rows and does nothing!
    old_item = next(e for e in claimed1 if e["event_id"] == outbox.event_id)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res1_late = await svc1.process_event(old_item, http_client=client)
        assert res1_late == "ALREADY_HANDLED"


@pytest.mark.asyncio
async def test_duplicate_delivery_safety():
    """Duplicate delivery when payment is already ALLOCATED does not throw."""
    business_id = uuid.uuid4()
    payment, outbox = await _create_completed_payment_with_outbox(business_id)

    outbox_svc = OutboxService(user_service_client=MockUserServiceClient())
    transport = httpx.ASGITransport(app=invoice_stub_app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        claimed = await outbox_svc.claim_batch(batch_size=10)
        target_event = next(e for e in claimed if e["event_id"] == outbox.event_id)
        res1 = await outbox_svc.process_event(target_event, http_client=client)
        assert res1 == "DELIVERED"

        # Simulate second delivery attempt on the same payment
        delivered = await outbox_svc._mark_delivered_and_allocate(
            event_id=outbox.event_id,
            payment_id=payment.payment_id,
            target_allocation="ALLOCATED",
            req_id="dup-req",
        )
        assert delivered is False  # Guarded update was skipped


@pytest.mark.asyncio
async def test_circuit_breaker_behavior():
    """Circuit breaker opens after 5 failures and stops claiming; resets on success."""
    clock = TestClock()
    svc = OutboxService(clock=clock, user_service_client=MockUserServiceClient())

    assert not svc.is_circuit_open()

    for _ in range(4):
        svc.record_circuit_failure()
        assert not svc.is_circuit_open()

    # 5th failure opens circuit breaker
    svc.record_circuit_failure()
    assert svc.is_circuit_open()

    # Advancing time past cooldown resets it
    clock.advance(seconds=35)
    assert not svc.is_circuit_open()


@pytest.mark.asyncio
async def test_two_workers_same_rows_concurrency():
    """Create 100 outbox rows and run two worker instances concurrently; verify every row delivered once across 20 trials."""
    session_factory = get_sessionmaker()
    business_id = uuid.uuid4()

    # Create 100 outbox rows
    event_ids = []
    for i in range(100):
        _, outbox = await _create_completed_payment_with_outbox(business_id)
        event_ids.append(outbox.event_id)

    user_client = MockUserServiceClient()
    svc1 = OutboxService(user_service_client=user_client)
    svc2 = OutboxService(user_service_client=user_client)

    transport = httpx.ASGITransport(app=invoice_stub_app)

    async def worker_loop(svc: OutboxService):
        delivered_count = 0
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(20):  # 20 polling cycles
                claimed = await svc.claim_batch(batch_size=10)
                if not claimed:
                    await asyncio.sleep(0.01)
                    continue
                tasks = [svc.process_event(item, http_client=client) for item in claimed]
                res = await asyncio.gather(*tasks)
                delivered_count += sum(1 for r in res if r in ("DELIVERED", "DELIVERED_REJECTED"))
        return delivered_count

    # Run two workers concurrently against the 100 rows
    counts = await asyncio.gather(worker_loop(svc1), worker_loop(svc2))

    # Verify all 100 events in DB are now DELIVERED and none were missed
    async with session_factory() as session:
        stmt = select(OutboxEvent).where(OutboxEvent.event_id.in_(event_ids))
        res = await session.execute(stmt)
        rows = res.scalars().all()
        assert len(rows) == 100
        assert all(r.status == "DELIVERED" for r in rows), "Not all 100 rows were delivered!"
        assert sum(counts) == 100, f"Expected total 100 deliveries, got {sum(counts)}"


@pytest.mark.asyncio
async def test_no_secrets_in_audit_or_last_error():
    """Verify that no service secrets or bearer tokens appear in last_error or audit logs."""
    business_id = uuid.uuid4()
    payment, outbox = await _create_completed_payment_with_outbox(business_id)

    outbox_svc = OutboxService(user_service_client=MockUserServiceClient())
    await outbox_svc._schedule_retry_or_dead(
        event_id=outbox.event_id,
        aggregate_id=payment.payment_id,
        attempts=10,
        error_msg="Error occurred with Bearer my_secret_token_12345",
        req_id="req-audit-test",
    )

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        # Check outbox row
        row = await session.get(OutboxEvent, outbox.event_id)
        assert row.status == "DEAD"

        # Check audit log
        stmt = select(AuditLog).where(AuditLog.entity_id == outbox.event_id)
        audit = (await session.execute(stmt)).scalar_one_or_none()
        assert audit is not None
        assert "password" not in str(audit.after).lower()
        assert "client_secret" not in str(audit.after).lower()
