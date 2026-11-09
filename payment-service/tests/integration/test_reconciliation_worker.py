"""Integration tests for ReconciliationWorker and ReconciliationService on real PostgreSQL.

Covers all Section 4 & Section 6 requirements:
- Every scenario in Table 4.3 (matching completion, amount mismatch, declined, pending, no capture after window, order not found, paypal outage)
- Idempotency with webhook (webhook first, recon first, 20 concurrent trials)
- Zero PayPal create or capture calls assertion
- Expiry job releases partial unique index allowing new checkout
- Two concurrent reconciliation workers SKIP LOCKED safety
- Late webhook after reconciliation FAILED produces RECON_LATE_CAPTURE_AFTER_FAILED without throwing
- Job C monitoring alerts (ALLOCATION_STUCK, REVIEW_STUCK, OUTBOX_DEAD_PRESENT rate limiting)
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
import httpx
import pytest
from sqlalchemy import select, update

from app.clients.paypal_client import FakePayPalClient, RealPayPalClient
from app.core.alerts import register_alert_listener, unregister_alert_listener
from app.core.clock import TestClock
from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.core.money import format_amount
from app.models.outbox import OutboxEvent
from app.models.payment import Payment
from app.models.provider_reference import PaymentProviderReference
from app.services.completion_service import complete_capture
from app.services.payment_service import get_payment_service
from app.services.reconciliation_service import ReconciliationService
from app.workers.reconciliation_worker import ReconciliationWorker
from fake_paypal.main import CALL_COUNTS, CONFIG, ORDERS_STORE, CAPTURES_STORE, app as fake_paypal_app


@pytest.fixture(autouse=True)
def reset_fake_paypal_stores():
    ORDERS_STORE.clear()
    CAPTURES_STORE.clear()
    for k in CALL_COUNTS:
        CALL_COUNTS[k] = 0
    CONFIG["force_status"] = None
    CONFIG["force_delay"] = 0.0
    CONFIG["force_capture_status"] = "COMPLETED"
    CONFIG["force_capture_500"] = False
    CONFIG["force_amount_mismatch"] = False
    CONFIG["force_malformed_capture"] = False
    CONFIG["force_signature_verification_status"] = "SUCCESS"


async def _create_recon_test_payment(
    business_id: uuid.UUID,
    status: str = "CAPTURE_PENDING",
    allocation_status: str = "NOT_APPLICABLE",
    amount: Decimal = Decimal("100.00"),
    currency: str = "USD",
    age_seconds: int = 150,
    expires_in_seconds: int = 1800,
    provider_capture_id: Optional[str] = None,
) -> tuple[Payment, str]:
    session_factory = get_sessionmaker()
    now_utc = datetime.now(timezone.utc)
    updated_at = now_utc - timedelta(seconds=age_seconds)
    expires_at = now_utc + timedelta(seconds=expires_in_seconds)

    payment_id = uuid.uuid4()
    order_id = f"ORDER-{uuid.uuid4().hex[:12].upper()}"

    async with session_factory() as session:
        payment = Payment(
            payment_id=payment_id,
            business_id=business_id,
            invoice_id=uuid.uuid4(),
            invoice_number="INV-RECON-001",
            amount=amount,
            currency=currency,
            provider="PAYPAL",
            status=status,
            allocation_status=allocation_status,
            idempotency_key=uuid.uuid4(),
            initiated_by_type="USER",
            expires_at=expires_at,
            created_at=updated_at,
            updated_at=updated_at,
        )
        session.add(payment)

        ref = PaymentProviderReference(
            id=uuid.uuid4(),
            payment_id=payment_id,
            provider_order_id=order_id,
            provider_capture_id=provider_capture_id,
            provider_status=status,
            paypal_request_id=uuid.uuid4(),
            capture_request_id=uuid.uuid4(),
        )
        session.add(ref)
        await session.commit()

        return payment, order_id


class StubPayPalClient(FakePayPalClient):
    """Directly wraps fake_paypal ASGI app for test assertions."""
    def __init__(self):
        super().__init__()
        self.transport = httpx.ASGITransport(app=fake_paypal_app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://test")

    async def get_order(self, order_id: str) -> dict[str, Any]:
        resp = await self.client.get(f"/v2/checkout/orders/{order_id}")
        if resp.status_code == 404:
            raise Exception("Order 404 NOT_FOUND")
        if resp.status_code >= 400:
            raise Exception(f"HTTP {resp.status_code}")
        return resp.json()

    async def get_capture(self, capture_id: str) -> dict[str, Any]:
        resp = await self.client.get(f"/v2/payments/captures/{capture_id}")
        if resp.status_code == 404:
            raise Exception("Capture 404 NOT_FOUND")
        if resp.status_code >= 400:
            raise Exception(f"HTTP {resp.status_code}")
        return resp.json()


@pytest.mark.asyncio
async def test_reconciliation_matching_completed_capture():
    """PayPal shows completed capture matching amount/currency -> COMPLETED, allocation PENDING, 1 outbox row."""
    business_id = uuid.uuid4()
    payment, order_id = await _create_recon_test_payment(business_id, status="CAPTURE_PENDING", amount=Decimal("150.00"), currency="USD")
    cap_id = f"CAP-RECON-{uuid.uuid4().hex[:8]}"

    # Seed order and completed capture in Fake PayPal
    ORDERS_STORE[order_id] = {
        "id": order_id,
        "status": "COMPLETED",
        "purchase_units": [
            {
                "payments": {
                    "captures": [
                        {
                            "id": cap_id,
                            "status": "COMPLETED",
                            "amount": {"value": "150.00", "currency_code": "USD"},
                        }
                    ]
                }
            }
        ],
    }

    paypal_client = StubPayPalClient()
    recon_svc = ReconciliationService(paypal_client=paypal_client)

    results = await recon_svc.reconcile_uncertain_captures(batch_size=10, business_id=business_id)
    assert results["completed"] == 1

    # Verify Database state
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        p = await session.get(Payment, payment.payment_id)
        assert p.status == "COMPLETED"
        assert p.allocation_status == "PENDING"

        # Exactly ONE outbox event
        stmt_outbox = select(OutboxEvent).where(
            (OutboxEvent.aggregate_id == payment.payment_id)
            & (OutboxEvent.event_type == "PaymentCaptured")
        )
        outbox_rows = (await session.execute(stmt_outbox)).scalars().all()
        assert len(outbox_rows) == 1
        assert outbox_rows[0].payload["provider_capture_id"] == cap_id

    # Verify reconciliation NEVER called create or capture
    assert CALL_COUNTS["create_order"] == 0
    assert CALL_COUNTS["capture_order"] == 0
    assert CALL_COUNTS["get_order"] >= 1


@pytest.mark.asyncio
async def test_reconciliation_amount_mismatch_alerts_and_requires_review():
    """PayPal completed capture with amount mismatch moves to REQUIRES_REVIEW, writes no outbox row, alerts."""
    business_id = uuid.uuid4()
    payment, order_id = await _create_recon_test_payment(business_id, status="CAPTURE_PENDING", amount=Decimal("100.00"), currency="USD")
    cap_id = f"CAP-MISMATCH-{uuid.uuid4().hex[:8]}"

    # Seed capture with mismatched amount ($99.00 instead of $100.00)
    ORDERS_STORE[order_id] = {
        "id": order_id,
        "status": "COMPLETED",
        "purchase_units": [
            {
                "payments": {
                    "captures": [
                        {
                            "id": cap_id,
                            "status": "COMPLETED",
                            "amount": {"value": "99.00", "currency_code": "USD"},
                        }
                    ]
                }
            }
        ],
    }

    alerts = []
    register_alert_listener(lambda code, f: alerts.append((code, f)))

    try:
        paypal_client = StubPayPalClient()
        recon_svc = ReconciliationService(paypal_client=paypal_client)
        results = await recon_svc.reconcile_uncertain_captures(batch_size=10, business_id=business_id)
        assert results["requires_review"] == 1

        session_factory = get_sessionmaker()
        async with session_factory() as session:
            p = await session.get(Payment, payment.payment_id)
            assert p.status == "REQUIRES_REVIEW"
            assert "AMOUNT_MISMATCH" in p.status_reason

            # NO outbox row created
            stmt = select(OutboxEvent).where(OutboxEvent.aggregate_id == payment.payment_id)
            rows = (await session.execute(stmt)).scalars().all()
            assert len(rows) == 0

        assert any(c == "RECON_AMOUNT_MISMATCH" for c, _ in alerts)
    finally:
        unregister_alert_listener(lambda code, f: alerts.append((code, f)))


@pytest.mark.asyncio
async def test_reconciliation_declined_capture_marks_failed():
    """PayPal capture DECLINED/FAILED moves payment to FAILED."""
    business_id = uuid.uuid4()
    payment, order_id = await _create_recon_test_payment(business_id, status="CAPTURE_PENDING")
    cap_id = f"CAP-DECLINED-{uuid.uuid4().hex[:8]}"

    ORDERS_STORE[order_id] = {
        "id": order_id,
        "status": "COMPLETED",
        "purchase_units": [
            {
                "payments": {
                    "captures": [
                        {
                            "id": cap_id,
                            "status": "DECLINED",
                            "amount": {"value": "100.00", "currency_code": "USD"},
                        }
                    ]
                }
            }
        ],
    }

    paypal_client = StubPayPalClient()
    recon_svc = ReconciliationService(paypal_client=paypal_client)
    results = await recon_svc.reconcile_uncertain_captures(batch_size=10, business_id=business_id)
    assert results["failed"] == 1

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        p = await session.get(Payment, payment.payment_id)
        assert p.status == "FAILED"
        assert p.status_reason == "PAYPAL_CAPTURE_DECLINED"


@pytest.mark.asyncio
async def test_reconciliation_capture_still_pending_leaves_unchanged_and_alerts_when_old():
    """Capture PENDING left unchanged; alerts RECON_CAPTURE_STILL_PENDING if > 30 minutes."""
    business_id = uuid.uuid4()
    # Age 1900s (> 30 min / 1800s)
    payment, order_id = await _create_recon_test_payment(business_id, status="CAPTURE_PENDING", age_seconds=1900)
    cap_id = f"CAP-PEND-{uuid.uuid4().hex[:8]}"

    ORDERS_STORE[order_id] = {
        "id": order_id,
        "status": "APPROVED",
        "purchase_units": [
            {
                "payments": {
                    "captures": [
                        {
                            "id": cap_id,
                            "status": "PENDING",
                            "amount": {"value": "100.00", "currency_code": "USD"},
                        }
                    ]
                }
            }
        ],
    }

    alerts = []
    register_alert_listener(lambda code, f: alerts.append((code, f)))

    try:
        paypal_client = StubPayPalClient()
        recon_svc = ReconciliationService(paypal_client=paypal_client)
        results = await recon_svc.reconcile_uncertain_captures(batch_size=10, business_id=business_id)
        assert results["unchanged"] == 1

        session_factory = get_sessionmaker()
        async with session_factory() as session:
            p = await session.get(Payment, payment.payment_id)
            assert p.status == "CAPTURE_PENDING"

        assert any(c == "RECON_CAPTURE_STILL_PENDING" for c, _ in alerts)
    finally:
        unregister_alert_listener(lambda code, f: alerts.append((code, f)))


@pytest.mark.asyncio
async def test_reconciliation_no_capture_after_window_marks_failed():
    """Order exists, no capture, age > 15 minutes (900s) -> FAILED with NO_CAPTURE_AFTER_WINDOW."""
    business_id = uuid.uuid4()
    # Age 950s (> 900s)
    payment, order_id = await _create_recon_test_payment(business_id, status="CAPTURE_PENDING", age_seconds=950)

    # Order exists but no captures in purchase units
    ORDERS_STORE[order_id] = {
        "id": order_id,
        "status": "APPROVED",
        "purchase_units": [{"payments": {"captures": []}}],
    }

    paypal_client = StubPayPalClient()
    recon_svc = ReconciliationService(paypal_client=paypal_client)
    results = await recon_svc.reconcile_uncertain_captures(batch_size=10, business_id=business_id)
    assert results["failed"] == 1

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        p = await session.get(Payment, payment.payment_id)
        assert p.status == "FAILED"
        assert p.status_reason == "NO_CAPTURE_AFTER_WINDOW"


@pytest.mark.asyncio
async def test_reconciliation_no_capture_window_not_yet_passed():
    """Order exists, no capture, but age < 15 minutes -> unchanged."""
    business_id = uuid.uuid4()
    # Age 150s (between 120s min age and 900s window)
    payment, order_id = await _create_recon_test_payment(business_id, status="CAPTURE_PENDING", age_seconds=150)

    ORDERS_STORE[order_id] = {
        "id": order_id,
        "status": "APPROVED",
        "purchase_units": [{"payments": {"captures": []}}],
    }

    paypal_client = StubPayPalClient()
    recon_svc = ReconciliationService(paypal_client=paypal_client)
    results = await recon_svc.reconcile_uncertain_captures(batch_size=10, business_id=business_id)
    assert results["unchanged"] == 1

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        p = await session.get(Payment, payment.payment_id)
        assert p.status == "CAPTURE_PENDING"


@pytest.mark.asyncio
async def test_reconciliation_order_not_found_leaves_requires_review_and_alerts():
    """Order not found at PayPal (404) leaves payment in REQUIRES_REVIEW and raises RECON_ORDER_NOT_FOUND."""
    business_id = uuid.uuid4()
    payment, order_id = await _create_recon_test_payment(business_id, status="CAPTURE_PENDING")
    # Order not in ORDERS_STORE -> returns 404

    alerts = []
    register_alert_listener(lambda code, f: alerts.append((code, f)))

    try:
        paypal_client = StubPayPalClient()
        recon_svc = ReconciliationService(paypal_client=paypal_client)
        results = await recon_svc.reconcile_uncertain_captures(batch_size=10, business_id=business_id)
        assert results["requires_review"] == 1

        session_factory = get_sessionmaker()
        async with session_factory() as session:
            p = await session.get(Payment, payment.payment_id)
            assert p.status == "REQUIRES_REVIEW"

        assert any(c == "RECON_ORDER_NOT_FOUND" for c, _ in alerts)
    finally:
        unregister_alert_listener(lambda code, f: alerts.append((code, f)))


@pytest.mark.asyncio
async def test_reconciliation_paypal_outage_changes_nothing_and_alerts_after_5():
    """PayPal unreachable/5xx changes no payment state; raises alert after 5 consecutive failures."""
    business_id = uuid.uuid4()
    payment, order_id = await _create_recon_test_payment(business_id, status="CAPTURE_PENDING")

    CONFIG["force_status"] = 500

    alerts = []
    register_alert_listener(lambda code, f: alerts.append((code, f)))

    try:
        paypal_client = StubPayPalClient()
        recon_svc = ReconciliationService(paypal_client=paypal_client)

        for i in range(5):
            results = await recon_svc.reconcile_uncertain_captures(batch_size=10, business_id=business_id)
            assert results["unchanged"] == 1

        session_factory = get_sessionmaker()
        async with session_factory() as session:
            p = await session.get(Payment, payment.payment_id)
            assert p.status == "CAPTURE_PENDING"

        assert any(c == "RECON_PAYPAL_UNAVAILABLE" for c, _ in alerts)
    finally:
        unregister_alert_listener(lambda code, f: alerts.append((code, f)))


@pytest.mark.asyncio
async def test_reconciliation_expiry_job_and_partial_unique_index_release():
    """Job A expires payments where expires_at <= now, releasing partial unique index for new checkout."""
    business_id = uuid.uuid4()
    invoice_id = uuid.uuid4()

    # 1. Create a payment whose expires_at is already in the past (-10 seconds)
    payment, _ = await _create_recon_test_payment(
        business_id,
        status="CREATED",
        age_seconds=100,
        expires_in_seconds=-10,
    )
    # Point it to specific invoice_id
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        await session.execute(
            update(Payment)
            .where(Payment.payment_id == payment.payment_id)
            .values(invoice_id=invoice_id)
        )
        await session.commit()

    recon_svc = ReconciliationService(paypal_client=StubPayPalClient())
    expired_count = await recon_svc.reconcile_expired_payments(business_id=business_id)
    assert expired_count == 1

    async with session_factory() as session:
        p = await session.get(Payment, payment.payment_id)
        assert p.status == "EXPIRED"
        assert p.status_reason == "CHECKOUT_EXPIRED"

    # Now verify partial unique index was released: new checkout for same invoice succeeds!
    payment_svc = get_payment_service()
    # Fake paypal order creation
    new_pay, _ = await _create_recon_test_payment(business_id, status="CREATED", expires_in_seconds=1800)
    async with session_factory() as session:
        await session.execute(
            update(Payment)
            .where(Payment.payment_id == new_pay.payment_id)
            .values(invoice_id=invoice_id)
        )
        await session.commit()
        # Succeeds without CHECKOUT_ALREADY_ACTIVE conflict!
        assert new_pay.status == "CREATED"


@pytest.mark.asyncio
async def test_reconciliation_expiry_skips_capture_pending():
    """A payment that moved to CAPTURE_PENDING between selection and update is NOT expired."""
    business_id = uuid.uuid4()
    payment, _ = await _create_recon_test_payment(
        business_id,
        status="CREATED",
        expires_in_seconds=-10,
    )

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        await session.execute(
            update(Payment)
            .where(Payment.payment_id == payment.payment_id)
            .values(status="CAPTURE_PENDING")
        )
        await session.commit()

    recon_svc = ReconciliationService(paypal_client=StubPayPalClient())
    expired_count = await recon_svc.reconcile_expired_payments(business_id=business_id)
    assert expired_count == 0

    async with session_factory() as session:
        p = await session.get(Payment, payment.payment_id)
        assert p.status == "CAPTURE_PENDING"


@pytest.mark.asyncio
async def test_late_webhook_after_reconciliation_failed():
    """Late webhook arriving after reconciliation marked payment FAILED produces RECON_LATE_CAPTURE_AFTER_FAILED without throwing."""
    business_id = uuid.uuid4()
    payment, _ = await _create_recon_test_payment(business_id, status="FAILED")

    alerts = []
    register_alert_listener(lambda code, f: alerts.append((code, f)))

    try:
        res = await complete_capture(
            business_id=business_id,
            payment_id=payment.payment_id,
            captured_amount="100.00",
            captured_currency="USD",
            provider_capture_id=f"CAP-LATE-{uuid.uuid4().hex[:8]}",
            actor_type="WEBHOOK",
        )
        assert res.is_late_capture_after_failed is True
        assert res.status == "FAILED"

        assert any(c == "RECON_LATE_CAPTURE_AFTER_FAILED" for c, _ in alerts)
    finally:
        unregister_alert_listener(lambda code, f: alerts.append((code, f)))


@pytest.mark.asyncio
async def test_monitoring_job_alerts_and_rate_limiting():
    """Job C alerts on stuck allocation, stuck review, and rate-limits dead outbox alerts."""
    business_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(seconds=4000)  # > 1 hour ago

    session_factory = get_sessionmaker()

    # 1. Stuck allocation payment (COMPLETED, allocation PENDING, updated_at > 15m ago)
    p_alloc, _ = await _create_recon_test_payment(business_id, status="COMPLETED", allocation_status="PENDING", age_seconds=1000)

    # 2. Stuck review payment (REQUIRES_REVIEW, updated_at > 1h ago)
    p_rev, _ = await _create_recon_test_payment(business_id, status="REQUIRES_REVIEW", age_seconds=3700)

    # 3. Dead outbox row
    event_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            OutboxEvent(
                event_id=event_id,
                aggregate_id=p_alloc.payment_id,
                event_type="PaymentCaptured",
                event_version=1,
                payload={"event_id": str(event_id)},
                status="DEAD",
                attempts=10,
                next_retry_at=old_time,
                created_at=old_time,
                last_error="Max retries reached",
            )
        )
        await session.commit()

    alerts = []
    register_alert_listener(lambda code, f: alerts.append((code, f)))

    try:
        recon_svc = ReconciliationService(paypal_client=StubPayPalClient())

        # First run: all three alerts fire
        counts1 = await recon_svc.monitor_system_health()
        assert counts1["allocation_stuck"] >= 1
        assert counts1["review_stuck"] >= 1
        assert counts1["dead_outbox"] >= 1

        alert_codes = [c for c, _ in alerts]
        assert "ALLOCATION_STUCK" in alert_codes
        assert "REVIEW_STUCK" in alert_codes
        assert "OUTBOX_DEAD_PRESENT" in alert_codes

        # Second run immediately after: dead outbox alert is rate-limited (at most once per hour)
        alerts.clear()
        counts2 = await recon_svc.monitor_system_health()
        assert counts2["dead_outbox"] == 0  # Rate limited!
    finally:
        unregister_alert_listener(lambda code, f: alerts.append((code, f)))


@pytest.mark.asyncio
async def test_reconciliation_and_webhook_idempotency_both_orders_and_concurrent():
    """Matching-completion case produces exactly one outbox row across both orders and 20 concurrent trials."""
    session_factory = get_sessionmaker()
    paypal_client = StubPayPalClient()
    recon_svc = ReconciliationService(paypal_client=paypal_client)

    # 1. Order A: Webhook first, then reconciliation
    biz_a = uuid.uuid4()
    pay_a, ord_a = await _create_recon_test_payment(biz_a, status="CAPTURE_PENDING", amount=Decimal("50.00"), currency="USD")
    cap_a = f"CAP-ORD-A-{uuid.uuid4().hex[:8]}"
    ORDERS_STORE[ord_a] = {
        "id": ord_a,
        "status": "COMPLETED",
        "purchase_units": [{"payments": {"captures": [{"id": cap_a, "status": "COMPLETED", "amount": {"value": "50.00", "currency_code": "USD"}}]}}],
    }
    # Webhook first
    res_wh = await complete_capture(biz_a, pay_a.payment_id, "50.00", "USD", cap_a, actor_type="WEBHOOK")
    assert res_wh.status == "COMPLETED"
    # Recon second
    recon_res = await recon_svc.reconcile_uncertain_captures(business_id=biz_a)
    # Check exactly ONE outbox row
    async with session_factory() as session:
        rows = (await session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == pay_a.payment_id))).scalars().all()
        assert len(rows) == 1

    # 2. Order B: Reconciliation first, then webhook
    biz_b = uuid.uuid4()
    pay_b, ord_b = await _create_recon_test_payment(biz_b, status="CAPTURE_PENDING", amount=Decimal("75.00"), currency="USD")
    cap_b = f"CAP-ORD-B-{uuid.uuid4().hex[:8]}"
    ORDERS_STORE[ord_b] = {
        "id": ord_b,
        "status": "COMPLETED",
        "purchase_units": [{"payments": {"captures": [{"id": cap_b, "status": "COMPLETED", "amount": {"value": "75.00", "currency_code": "USD"}}]}}],
    }
    # Recon first
    recon_res_b = await recon_svc.reconcile_uncertain_captures(business_id=biz_b)
    assert recon_res_b["completed"] == 1
    # Webhook second
    res_wh_b = await complete_capture(biz_b, pay_b.payment_id, "75.00", "USD", cap_b, actor_type="WEBHOOK")
    assert res_wh_b.status == "COMPLETED"
    assert res_wh_b.already_completed is True
    # Check exactly ONE outbox row
    async with session_factory() as session:
        rows = (await session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == pay_b.payment_id))).scalars().all()
        assert len(rows) == 1

    # 3. Concurrently: 20 trials
    for i in range(20):
        biz_c = uuid.uuid4()
        pay_c, ord_c = await _create_recon_test_payment(biz_c, status="CAPTURE_PENDING", amount=Decimal("100.00"), currency="USD")
        cap_c = f"CAP-RACE-RECON-{i}-{uuid.uuid4().hex[:6]}"
        ORDERS_STORE[ord_c] = {
            "id": ord_c,
            "status": "COMPLETED",
            "purchase_units": [{"payments": {"captures": [{"id": cap_c, "status": "COMPLETED", "amount": {"value": "100.00", "currency_code": "USD"}}]}}],
        }

        # Compete concurrently
        await asyncio.gather(
            recon_svc.reconcile_uncertain_captures(business_id=biz_c),
            complete_capture(biz_c, pay_c.payment_id, "100.00", "USD", cap_c, actor_type="WEBHOOK"),
            return_exceptions=True,
        )

        async with session_factory() as session:
            p = await session.get(Payment, pay_c.payment_id)
            assert p.status == "COMPLETED"
            rows = (await session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == pay_c.payment_id))).scalars().all()
            assert len(rows) == 1, f"Trial {i} produced {len(rows)} outbox events instead of 1"


@pytest.mark.asyncio
async def test_two_reconciliation_workers_concurrency_skip_locked():
    """Two reconciliation workers processing the same rows never collide or duplicate work (SKIP LOCKED)."""
    biz = uuid.uuid4()
    paypal_client = StubPayPalClient()
    recon1 = ReconciliationService(paypal_client=paypal_client)
    recon2 = ReconciliationService(paypal_client=paypal_client)

    run_tag = uuid.uuid4().hex[:6]
    payments = []
    for i in range(10):
        p, oid = await _create_recon_test_payment(biz, status="CAPTURE_PENDING", amount=Decimal("20.00"), currency="USD")
        cap = f"CAP-MULTI-RECON-{run_tag}-{i}"
        ORDERS_STORE[oid] = {
            "id": oid,
            "status": "COMPLETED",
            "purchase_units": [{"payments": {"captures": [{"id": cap, "status": "COMPLETED", "amount": {"value": "20.00", "currency_code": "USD"}}]}}],
        }
        payments.append(p)

    # Run two reconciliation cycles concurrently on the same business
    res1, res2 = await asyncio.gather(
        recon1.reconcile_uncertain_captures(business_id=biz),
        recon2.reconcile_uncertain_captures(business_id=biz),
    )

    total_completed = res1["completed"] + res2["completed"]
    assert total_completed == 10

    # Assert zero create or capture calls
    assert CALL_COUNTS["create_order"] == 0
    assert CALL_COUNTS["capture_order"] == 0
