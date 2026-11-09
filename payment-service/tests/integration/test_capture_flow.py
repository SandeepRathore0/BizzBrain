import asyncio
import uuid
from decimal import Decimal
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.invoice_client import get_invoice_client
from app.models.outbox import OutboxEvent
from app.models.payment import Payment
from app.models.provider_reference import PaymentProviderReference
from fake_paypal.main import app as fake_paypal_app
from tests.integration.test_order_creation import seed_invoice, seed_token
from tests.test_keys import create_test_jwt


async def setup_order_for_capture(client: AsyncClient, balance: str = "100.00") -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, str, str]:
    business_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    user_id = uuid.uuid4()

    await seed_invoice(invoice_id, business_id, payable=True, balance=balance)

    token = create_test_jwt(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": str(uuid.uuid4()),
    }
    resp = await client.post("/api/v1/payments/orders", json={"invoice_id": str(invoice_id)}, headers=headers)
    assert resp.status_code == 201
    payment_id = uuid.UUID(resp.json()["data"]["payment_id"])

    # Simulate PayPal approval on fake_paypal
    # Find provider order ID
    transport = ASGITransport(app=fake_paypal_app)
    async with AsyncClient(transport=transport, base_url="http://fake-paypal") as fp:
        from fake_paypal.main import ORDERS_STORE
        order_id = list(ORDERS_STORE.keys())[-1]
        await fp.post(f"/test/orders/{order_id}/set_status", json={"status": "APPROVED"})

    return business_id, invoice_id, user_id, token, payment_id


@pytest.mark.asyncio
async def test_staff_capture_happy_path(client: AsyncClient, db_session: AsyncSession):
    business_id, invoice_id, user_id, token, payment_id = await setup_order_for_capture(client)

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": str(uuid.uuid4()),
    }

    # Execute Capture
    resp = await client.post(f"/api/v1/payments/{payment_id}/capture", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "COMPLETED"
    assert data["amount"] == "100.00"
    assert data["currency"] == "USD"
    assert data["provider_capture_id"] is not None

    # Check database state
    stmt = select(Payment).where(Payment.payment_id == payment_id)
    res = await db_session.execute(stmt)
    p = res.scalar_one()
    assert p.status == "COMPLETED"
    assert p.allocation_status == "PENDING"

    # Check Outbox row inserted in same transaction
    stmt_outbox = select(OutboxEvent).where(OutboxEvent.aggregate_id == payment_id)
    res_outbox = await db_session.execute(stmt_outbox)
    outbox_event = res_outbox.scalar_one()
    assert outbox_event.event_type == "PaymentCaptured"
    assert outbox_event.status == "PENDING"
    assert outbox_event.payload["provider_capture_id"] == data["provider_capture_id"]

    # Re-call capture: an already COMPLETED payment returns stored result (200)
    re_resp = await client.post(f"/api/v1/payments/{payment_id}/capture", headers=headers)
    assert re_resp.status_code == 200
    assert re_resp.json()["data"]["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_capture_payer_not_approved_returns_409(client: AsyncClient):
    business_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    user_id = uuid.uuid4()

    await seed_invoice(invoice_id, business_id, payable=True, balance="50.00")
    token = create_test_jwt(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": str(uuid.uuid4()),
    }

    # Create order (remains CREATED on PayPal, NOT approved)
    resp = await client.post("/api/v1/payments/orders", json={"invoice_id": str(invoice_id)}, headers=headers)
    payment_id = resp.json()["data"]["payment_id"]

    # Try to capture without approval
    cap_resp = await client.post(
        f"/api/v1/payments/{payment_id}/capture",
        headers={"Authorization": f"Bearer {token}", "X-Business-ID": str(business_id), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert cap_resp.status_code == 409
    assert cap_resp.json()["error"]["code"] == "PAYER_NOT_APPROVED"


@pytest.mark.asyncio
async def test_capture_invoice_voided_between_order_and_capture(client: AsyncClient, db_session: AsyncSession):
    business_id, invoice_id, user_id, token, payment_id = await setup_order_for_capture(client)

    # Void invoice before capture
    await seed_invoice(invoice_id, business_id, payable=False, balance="100.00")

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": str(uuid.uuid4()),
    }
    resp = await client.post(f"/api/v1/payments/{payment_id}/capture", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INVOICE_NO_LONGER_PAYABLE"

    # Payment must be marked CANCELLED
    stmt = select(Payment).where(Payment.payment_id == payment_id)
    res = await db_session.execute(stmt)
    p = res.scalar_one()
    assert p.status == "CANCELLED"
    assert "INVOICE_VOIDED" in (p.status_reason or "")


@pytest.mark.asyncio
async def test_capture_c2_down_gives_503_and_payment_stays_approvable(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    business_id, invoice_id, user_id, token, payment_id = await setup_order_for_capture(client)

    # Mock C2 failure
    from app.core.errors import ServiceUnavailableException
    inv_client = get_invoice_client()

    async def mock_down(bid, iid, req_id=None):
        raise ServiceUnavailableException(code="INVOICE_SERVICE_UNAVAILABLE", message="C2 is down")

    monkeypatch.setattr(inv_client, "get_payable_details", mock_down)

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": str(uuid.uuid4()),
    }
    resp = await client.post(f"/api/v1/payments/{payment_id}/capture", headers=headers)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "INVOICE_SERVICE_UNAVAILABLE"

    # Payment remains approvable (PAYER_APPROVED)
    stmt = select(Payment).where(Payment.payment_id == payment_id)
    res = await db_session.execute(stmt)
    p = res.scalar_one()
    assert p.status == "PAYER_APPROVED"


@pytest.mark.asyncio
async def test_capture_paypal_decline_marks_failed(client: AsyncClient, db_session: AsyncSession):
    business_id, invoice_id, user_id, token, payment_id = await setup_order_for_capture(client)

    # Configure PayPal to decline capture
    transport = ASGITransport(app=fake_paypal_app)
    async with AsyncClient(transport=transport, base_url="http://fake-paypal") as fp:
        await fp.post("/test/config", json={"force_capture_status": "DECLINED"})

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": str(uuid.uuid4()),
    }
    resp = await client.post(f"/api/v1/payments/{payment_id}/capture", headers=headers)

    # Reset PayPal mock
    async with AsyncClient(transport=transport, base_url="http://fake-paypal") as fp:
        await fp.post("/test/reset")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "PAYPAL_CAPTURE_DECLINED"

    # Verify payment status is FAILED
    stmt = select(Payment).where(Payment.payment_id == payment_id)
    res = await db_session.execute(stmt)
    p = res.scalar_one()
    assert p.status == "FAILED"


@pytest.mark.asyncio
async def test_capture_timeout_marks_requires_review(client: AsyncClient, db_session: AsyncSession):
    business_id, invoice_id, user_id, token, payment_id = await setup_order_for_capture(client)

    # Configure PayPal to throw 500 error on capture
    transport = ASGITransport(app=fake_paypal_app)
    async with AsyncClient(transport=transport, base_url="http://fake-paypal") as fp:
        await fp.post("/test/config", json={"force_capture_500": True})

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": str(uuid.uuid4()),
    }
    resp = await client.post(f"/api/v1/payments/{payment_id}/capture", headers=headers)

    # Reset PayPal mock
    async with AsyncClient(transport=transport, base_url="http://fake-paypal") as fp:
        await fp.post("/test/reset")

    assert resp.status_code in (502, 503)
    assert resp.json()["error"]["code"] == "PAYMENT_REQUIRES_REVIEW"

    # Verify payment is in REQUIRES_REVIEW
    stmt = select(Payment).where(Payment.payment_id == payment_id)
    res = await db_session.execute(stmt)
    p = res.scalar_one()
    assert p.status == "REQUIRES_REVIEW"


@pytest.mark.asyncio
async def test_public_capture_happy_path_and_cross_invoice_protection(client: AsyncClient):
    business_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    pay_token = "valid_pay_token_for_capture"

    await seed_invoice(invoice_id, business_id, payable=True, balance="75.00")
    await seed_token(pay_token, invoice_id, business_id)

    # Public Order Creation
    resp = await client.post(
        "/api/v1/public/pay/orders",
        json={"token": pay_token},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    payment_id = resp.json()["data"]["payment_id"]

    # Approve on Fake PayPal
    transport = ASGITransport(app=fake_paypal_app)
    async with AsyncClient(transport=transport, base_url="http://fake-paypal") as fp:
        from fake_paypal.main import ORDERS_STORE
        order_id = list(ORDERS_STORE.keys())[-1]
        await fp.post(f"/test/orders/{order_id}/set_status", json={"status": "APPROVED"})

    # 1. Attempt capture with a DIFFERENT pay token for another invoice
    other_invoice_id = uuid.uuid4()
    other_token = "other_token_different_invoice"
    await seed_invoice(other_invoice_id, business_id, payable=True, balance="50.00")
    await seed_token(other_token, other_invoice_id, business_id)

    cross_resp = await client.post(
        f"/api/v1/public/pay/{payment_id}/capture",
        json={"token": other_token},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert cross_resp.status_code == 404
    assert cross_resp.json()["error"]["code"] == "PAYMENT_NOT_FOUND"

    # 2. Legitimate capture with correct token succeeds
    legit_resp = await client.post(
        f"/api/v1/public/pay/{payment_id}/capture",
        json={"token": pay_token},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert legit_resp.status_code == 200
    assert legit_resp.json()["data"]["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_concurrency_20_parallel_captures(client: AsyncClient, db_session: AsyncSession):
    """20 parallel captures of one payment produce exactly ONE capture completion and ONE outbox row."""
    business_id, invoice_id, user_id, token, payment_id = await setup_order_for_capture(client, balance="300.00")

    async def make_capture_call():
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Business-ID": str(business_id),
            "Idempotency-Key": str(uuid.uuid4()),
        }
        return await client.post(f"/api/v1/payments/{payment_id}/capture", headers=headers)

    # 20 parallel requests
    responses = await asyncio.gather(*[make_capture_call() for _ in range(20)])

    # All responses must be valid HTTP responses
    statuses = [r.status_code for r in responses]
    # At least one request completed (200), and after completion subsequent ones return 200 (stored result) or 409
    assert 200 in statuses

    # Check Database: exactly ONE PaymentCaptured outbox event exists for this payment!
    stmt = select(OutboxEvent).where(
        OutboxEvent.aggregate_id == payment_id,
        OutboxEvent.event_type == "PaymentCaptured",
    )
    result = await db_session.execute(stmt)
    outbox_events = result.scalars().all()
    assert len(outbox_events) == 1, f"Expected exactly 1 outbox event, got {len(outbox_events)}"
