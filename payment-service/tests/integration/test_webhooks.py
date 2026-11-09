import asyncio
import json
import uuid
from decimal import Decimal
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.outbox import OutboxEvent
from app.models.payment import Payment
from app.models.provider_reference import PaymentProviderReference
from app.models.webhook_event import PaymentWebhookEvent
from fake_paypal.main import app as fake_paypal_app
from tests.integration.test_capture_flow import setup_order_for_capture
from tests.integration.test_order_creation import seed_invoice
from tests.test_keys import create_test_jwt


def make_webhook_headers():
    return {
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-cert-url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-123",
        "paypal-transmission-id": str(uuid.uuid4()),
        "paypal-transmission-sig": "fake_signature_abc",
        "paypal-transmission-time": "2026-11-08T12:00:00Z",
        "Content-Type": "application/json",
    }


@pytest.mark.asyncio
async def test_webhook_invalid_signature_rejected_and_audited(client: AsyncClient, db_session: AsyncSession):
    # Configure Fake PayPal to fail signature verification
    transport = ASGITransport(app=fake_paypal_app)
    async with AsyncClient(transport=transport, base_url="http://fake-paypal") as fp:
        await fp.post("/test/config", json={"force_signature_verification_status": "FAILURE"})

    headers = make_webhook_headers()
    fake_event_id = f"WH-FORGED-{uuid.uuid4().hex[:8]}"
    payload = {
        "id": fake_event_id,
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {"id": "CAP-123"},
    }

    resp = await client.post("/api/v1/webhooks/paypal", json=payload, headers=headers)

    # Reset signature verification mock
    async with AsyncClient(transport=transport, base_url="http://fake-paypal") as fp:
        await fp.post("/test/reset")

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"

    # Event must NOT be in payment_webhook_events
    stmt_wh = select(PaymentWebhookEvent).where(PaymentWebhookEvent.provider_event_id == fake_event_id)
    res_wh = await db_session.execute(stmt_wh)
    assert res_wh.scalar_one_or_none() is None

    # Audit log must contain WEBHOOK_SIGNATURE_VERIFICATION_FAILED
    stmt_audit = select(AuditLog).where(AuditLog.action == "WEBHOOK_SIGNATURE_VERIFICATION_FAILED")
    res_audit = await db_session.execute(stmt_audit)
    audit = res_audit.scalars().all()
    assert len(audit) >= 1

    # Now verify genuine event with SAME event_id can be accepted later
    valid_resp = await client.post("/api/v1/webhooks/paypal", json=payload, headers=headers)
    assert valid_resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_replay_ignored(client: AsyncClient, db_session: AsyncSession):
    event_id = f"WH-REPLAY-{uuid.uuid4().hex[:8]}"
    headers = make_webhook_headers()
    payload = {
        "id": event_id,
        "event_type": "UNKNOWN.TYPE",
        "resource": {"id": "TEST-123"},
    }

    # First send -> processed
    resp1 = await client.post("/api/v1/webhooks/paypal", json=payload, headers=headers)
    assert resp1.status_code == 200
    assert resp1.json()["data"]["status"] in ("IGNORED", "IGNORED_UNKNOWN_PAYMENT")

    # Second send with same ID -> ALREADY_PROCESSED
    resp2 = await client.post("/api/v1/webhooks/paypal", json=payload, headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["data"]["status"] == "ALREADY_PROCESSED"


@pytest.mark.asyncio
async def test_webhook_order_approved_moves_created_to_payer_approved(client: AsyncClient, db_session: AsyncSession):
    business_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    user_id = uuid.uuid4()

    await seed_invoice(invoice_id, business_id, payable=True, balance="80.00")
    token = create_test_jwt(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": str(uuid.uuid4()),
    }

    order_resp = await client.post("/api/v1/payments/orders", json={"invoice_id": str(invoice_id)}, headers=headers)
    payment_id = uuid.UUID(order_resp.json()["data"]["payment_id"])

    # Lookup provider order ID
    stmt_ref = select(PaymentProviderReference).where(PaymentProviderReference.payment_id == payment_id)
    res_ref = await db_session.execute(stmt_ref)
    ref = res_ref.scalar_one()

    # Deliver CHECKOUT.ORDER.APPROVED webhook
    event_id = f"WH-APPROVE-{uuid.uuid4().hex[:8]}"
    wh_payload = {
        "id": event_id,
        "event_type": "CHECKOUT.ORDER.APPROVED",
        "resource": {"id": ref.provider_order_id},
    }

    wh_resp = await client.post("/api/v1/webhooks/paypal", json=wh_payload, headers=make_webhook_headers())
    assert wh_resp.status_code == 200
    assert wh_resp.json()["data"]["status"] == "PROCESSED"

    # Payment status must now be PAYER_APPROVED
    stmt_p = select(Payment).where(Payment.payment_id == payment_id)
    res_p = await db_session.execute(stmt_p)
    p = res_p.scalar_one()
    assert p.status == "PAYER_APPROVED"


@pytest.mark.asyncio
async def test_webhook_capture_completed_creates_outbox_event(client: AsyncClient, db_session: AsyncSession):
    business_id, invoice_id, user_id, token, payment_id = await setup_order_for_capture(client, balance="120.00")

    # Find provider order ID
    stmt_ref = select(PaymentProviderReference).where(PaymentProviderReference.payment_id == payment_id)
    res_ref = await db_session.execute(stmt_ref)
    ref = res_ref.scalar_one()

    capture_id = f"CAP-{uuid.uuid4().hex[:8]}"
    event_id = f"WH-CAP-{uuid.uuid4().hex[:8]}"

    wh_payload = {
        "id": event_id,
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": capture_id,
            "amount": {"value": "120.00", "currency_code": "USD"},
            "supplementary_data": {
                "related_ids": {"order_id": ref.provider_order_id}
            },
        },
    }

    wh_resp = await client.post("/api/v1/webhooks/paypal", json=wh_payload, headers=make_webhook_headers())
    assert wh_resp.status_code == 200
    assert wh_resp.json()["data"]["status"] == "PROCESSED"

    # Payment must be COMPLETED, allocation PENDING
    stmt_p = select(Payment).where(Payment.payment_id == payment_id)
    res_p = await db_session.execute(stmt_p)
    p = res_p.scalar_one()
    assert p.status == "COMPLETED"
    assert p.allocation_status == "PENDING"

    # Exactly ONE PaymentCaptured outbox row created
    stmt_out = select(OutboxEvent).where(
        OutboxEvent.aggregate_id == payment_id,
        OutboxEvent.event_type == "PaymentCaptured",
    )
    res_out = await db_session.execute(stmt_out)
    outbox_events = res_out.scalars().all()
    assert len(outbox_events) == 1
    assert outbox_events[0].payload["provider_capture_id"] == capture_id


@pytest.mark.asyncio
async def test_webhook_capture_declined_marks_failed(client: AsyncClient, db_session: AsyncSession):
    business_id, invoice_id, user_id, token, payment_id = await setup_order_for_capture(client)

    # Set status to CAPTURE_PENDING
    stmt_p = select(Payment).where(Payment.payment_id == payment_id)
    res_p = await db_session.execute(stmt_p)
    p = res_p.scalar_one()
    p.status = "CAPTURE_PENDING"
    await db_session.commit()

    stmt_ref = select(PaymentProviderReference).where(PaymentProviderReference.payment_id == payment_id)
    res_ref = await db_session.execute(stmt_ref)
    ref = res_ref.scalar_one()

    # Deliver decline webhook
    wh_payload = {
        "id": f"WH-DEC-{uuid.uuid4().hex[:8]}",
        "event_type": "PAYMENT.CAPTURE.DECLINED",
        "resource": {
            "id": f"CAP-{uuid.uuid4().hex[:8]}",
            "supplementary_data": {"related_ids": {"order_id": ref.provider_order_id}},
        },
    }
    resp = await client.post("/api/v1/webhooks/paypal", json=wh_payload, headers=make_webhook_headers())
    print("DECLINE RESP:", resp.json())
    assert resp.status_code == 200

    # Payment must be FAILED
    await db_session.refresh(p)
    assert p.status == "FAILED"


@pytest.mark.asyncio
async def test_webhook_refunded_marks_completed_payment_refunded(client: AsyncClient, db_session: AsyncSession):
    business_id, invoice_id, user_id, token, payment_id = await setup_order_for_capture(client)

    # First capture legitimately to COMPLETED
    cap_resp = await client.post(
        f"/api/v1/payments/{payment_id}/capture",
        headers={"Authorization": f"Bearer {token}", "X-Business-ID": str(business_id), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert cap_resp.status_code == 200

    stmt_ref = select(PaymentProviderReference).where(PaymentProviderReference.payment_id == payment_id)
    res_ref = await db_session.execute(stmt_ref)
    ref = res_ref.scalar_one()
    cap_id = ref.provider_capture_id

    # Deliver REFUNDED webhook
    wh_payload = {
        "id": f"WH-REFUND-{uuid.uuid4().hex[:8]}",
        "event_type": "PAYMENT.CAPTURE.REFUNDED",
        "resource": {
            "id": f"REFUND-{uuid.uuid4().hex[:8]}",
            "supplementary_data": {"related_ids": {"order_id": ref.provider_order_id}},
        },
    }
    resp = await client.post("/api/v1/webhooks/paypal", json=wh_payload, headers=make_webhook_headers())
    print("DECLINE RESP:", resp.json())
    assert resp.status_code == 200

    # Payment must now be REFUNDED
    stmt_check = select(Payment).where(Payment.payment_id == payment_id)
    res_check = await db_session.execute(stmt_check)
    p_check = res_check.scalar_one()
    assert p_check.status == "REFUNDED"


@pytest.mark.asyncio
async def test_webhook_unknown_payment_ignored(client: AsyncClient, db_session: AsyncSession):
    wh_payload = {
        "id": f"WH-UNKNOWN-{uuid.uuid4().hex[:8]}",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": "CAP-NON-EXISTENT",
            "amount": {"value": "50.00", "currency_code": "USD"},
        },
    }
    resp = await client.post("/api/v1/webhooks/paypal", json=wh_payload, headers=make_webhook_headers())
    print("DECLINE RESP:", resp.json())
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "IGNORED_UNKNOWN_PAYMENT"


@pytest.mark.asyncio
async def test_webhook_and_capture_racing_concurrency(client: AsyncClient, db_session: AsyncSession):
    """Racing webhook and capture endpoint to complete the same payment produce exactly ONE PaymentCaptured event."""
    business_id, invoice_id, user_id, token, payment_id = await setup_order_for_capture(client, balance="100.00")

    stmt_ref = select(PaymentProviderReference).where(PaymentProviderReference.payment_id == payment_id)
    res_ref = await db_session.execute(stmt_ref)
    ref = res_ref.scalar_one()

    capture_id = f"CAP-RACE-{uuid.uuid4().hex[:8]}"
    wh_payload = {
        "id": f"WH-RACE-{uuid.uuid4().hex[:8]}",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": capture_id,
            "amount": {"value": "100.00", "currency_code": "USD"},
            "supplementary_data": {"related_ids": {"order_id": ref.provider_order_id}},
        },
    }

    async def call_capture():
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Business-ID": str(business_id),
            "Idempotency-Key": str(uuid.uuid4()),
        }
        return await client.post(f"/api/v1/payments/{payment_id}/capture", headers=headers)

    async def call_webhook():
        return await client.post("/api/v1/webhooks/paypal", json=wh_payload, headers=make_webhook_headers())

    # Race both simultaneously
    cap_resp, wh_resp = await asyncio.gather(call_capture(), call_webhook())

    # Handled cleanly without server error
    assert cap_resp.status_code in (200, 409)
    assert wh_resp.status_code == 200

    # Verify database has exactly ONE PaymentCaptured outbox event!
    stmt_out = select(OutboxEvent).where(
        OutboxEvent.aggregate_id == payment_id,
        OutboxEvent.event_type == "PaymentCaptured",
    )
    res_out = await db_session.execute(stmt_out)
    events = res_out.scalars().all()
    assert len(events) == 1, f"Expected exactly 1 outbox event, got {len(events)}"
