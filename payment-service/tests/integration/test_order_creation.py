import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.invoice_client import InvoicePayableDetails, ResolvedPayToken, get_invoice_client
from app.clients.paypal_client import get_paypal_client, set_paypal_client, FakePayPalClient
from app.core.database import get_db
from app.main import app
from app.models.payment import Payment
from tests.stubs.invoice_service_stub import app as fake_invoice_app
from fake_paypal.main import app as fake_paypal_app
from tests.test_keys import create_test_jwt


async def seed_invoice(
    invoice_id: uuid.UUID,
    business_id: uuid.UUID,
    payable: bool = True,
    balance: str = "100.00",
    min_installment: str = None,
    currency: str = "USD",
):
    inv_transport = ASGITransport(app=fake_invoice_app)
    async with AsyncClient(transport=inv_transport, base_url="http://fake-invoice") as client:
        await client.post(
            "/test/seed_invoice",
            json={
                "invoice_id": str(invoice_id),
                "business_id": str(business_id),
                "invoice_number": f"INV-{invoice_id.hex[:6]}",
                "business_name": "Acme Corp",
                "payable": payable,
                "currency": currency,
                "outstanding_balance": balance,
                "min_installment_amount": min_installment,
            },
        )


async def seed_token(token: str, invoice_id: uuid.UUID, business_id: uuid.UUID):
    inv_transport = ASGITransport(app=fake_invoice_app)
    async with AsyncClient(transport=inv_transport, base_url="http://fake-invoice") as client:
        await client.post(
            "/test/seed_pay_token",
            json={
                "token": token,
                "invoice_id": str(invoice_id),
                "business_id": str(business_id),
            },
        )


@pytest.mark.asyncio
async def test_staff_create_order_happy_path(client: AsyncClient, db_session: AsyncSession):
    business_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    user_id = uuid.uuid4()

    await seed_invoice(invoice_id, business_id, payable=True, balance="150.00")

    token = create_test_jwt(user_id=user_id, memberships={str(business_id): "OWNER"})
    ikey = str(uuid.uuid4())

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": ikey,
    }
    payload = {"invoice_id": str(invoice_id)}

    resp = await client.post("/api/v1/payments/orders", json=payload, headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["status"] == "CREATED"
    assert data["amount"] == "150.00"
    assert data["currency"] == "USD"
    assert "approve_url" in data
    assert "payment_id" in data

    # Replay with same Idempotency-Key returns cached response with Idempotent-Replayed: true
    replayed_resp = await client.post("/api/v1/payments/orders", json=payload, headers=headers)
    assert replayed_resp.status_code == 201
    assert replayed_resp.headers.get("Idempotent-Replayed") == "true"
    assert replayed_resp.json()["data"]["payment_id"] == data["payment_id"]


@pytest.mark.asyncio
async def test_order_creation_expires_stale_payment(client: AsyncClient, db_session: AsyncSession):
    business_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    user_id = uuid.uuid4()

    await seed_invoice(invoice_id, business_id, payable=True, balance="100.00")

    # Insert a stale CREATED payment with expires_at in the past
    stale_id = uuid.uuid4()
    past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    await db_session.execute(
        text("""
            INSERT INTO payments (
                payment_id, business_id, invoice_id, amount, currency, status,
                allocation_status, idempotency_key, initiated_by_type, expires_at
            ) VALUES (
                :pid, :bid, :iid, 100.00, 'USD', 'CREATED',
                'NOT_APPLICABLE', :ikey, 'USER', :exp
            )
        """),
        {"pid": stale_id, "bid": business_id, "iid": invoice_id, "ikey": uuid.uuid4(), "exp": past_time},
    )
    await db_session.commit()

    # Create new order -> should expire the stale one and succeed!
    token = create_test_jwt(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": str(uuid.uuid4()),
    }
    resp = await client.post("/api/v1/payments/orders", json={"invoice_id": str(invoice_id)}, headers=headers)
    assert resp.status_code == 201

    # Verify stale payment is now EXPIRED
    stmt = select(Payment).where(Payment.payment_id == stale_id)
    res = await db_session.execute(stmt)
    stale_p = res.scalar_one()
    assert stale_p.status == "EXPIRED"


@pytest.mark.asyncio
async def test_order_creation_conflict_active_checkout(client: AsyncClient, db_session: AsyncSession):
    business_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    user_id = uuid.uuid4()

    await seed_invoice(invoice_id, business_id, payable=True, balance="100.00")

    token = create_test_jwt(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers1 = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": str(uuid.uuid4()),
    }

    # 1. First order succeeds
    resp1 = await client.post("/api/v1/payments/orders", json={"invoice_id": str(invoice_id)}, headers=headers1)
    assert resp1.status_code == 201

    # 2. Second order for same invoice with different idempotency key returns 409 CHECKOUT_ALREADY_ACTIVE
    headers2 = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": str(uuid.uuid4()),
    }
    resp2 = await client.post("/api/v1/payments/orders", json={"invoice_id": str(invoice_id)}, headers=headers2)
    assert resp2.status_code == 409
    assert resp2.json()["error"]["code"] == "CHECKOUT_ALREADY_ACTIVE"


@pytest.mark.asyncio
async def test_order_creation_installment_validation(client: AsyncClient):
    business_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    user_id = uuid.uuid4()
    token = create_test_jwt(user_id=user_id, memberships={str(business_id): "OWNER"})

    # Case A: Invoice has min_installment_amount=25.00, balance=100.00
    await seed_invoice(invoice_id, business_id, payable=True, balance="100.00", min_installment="25.00")

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": str(uuid.uuid4()),
    }

    # Amount too low (< 25.00)
    resp = await client.post(
        "/api/v1/payments/orders",
        json={"invoice_id": str(invoice_id), "amount": "20.00"},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "AMOUNT_NOT_ALLOWED"

    # Amount too high (> 100.00)
    headers["Idempotency-Key"] = str(uuid.uuid4())
    resp = await client.post(
        "/api/v1/payments/orders",
        json={"invoice_id": str(invoice_id), "amount": "150.00"},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "AMOUNT_NOT_ALLOWED"

    # Valid installment amount (50.00)
    headers["Idempotency-Key"] = str(uuid.uuid4())
    resp = await client.post(
        "/api/v1/payments/orders",
        json={"invoice_id": str(invoice_id), "amount": "50.00"},
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["amount"] == "50.00"


@pytest.mark.asyncio
async def test_order_creation_invoice_not_payable(client: AsyncClient):
    business_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    user_id = uuid.uuid4()

    await seed_invoice(invoice_id, business_id, payable=False, balance="100.00")

    token = create_test_jwt(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": str(uuid.uuid4()),
    }
    resp = await client.post("/api/v1/payments/orders", json={"invoice_id": str(invoice_id)}, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INVOICE_NOT_PAYABLE"


@pytest.mark.asyncio
async def test_public_pay_summary_and_order_flow(client: AsyncClient):
    business_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    pay_token = "valid_pay_token_xyz"

    await seed_invoice(invoice_id, business_id, payable=True, balance="200.00")
    await seed_token(pay_token, invoice_id, business_id)

    # 1. Summary
    sum_resp = await client.get(f"/api/v1/public/pay/{pay_token}/summary")
    assert sum_resp.status_code == 200
    sum_data = sum_resp.json()["data"]
    assert sum_data["business_name"] == "Acme Corp"
    assert sum_data["amount_due"] == "200.00"
    assert sum_data["payable"] is True
    assert "payment_id" not in sum_data  # Restricted fields not leaked

    # 2. Public Order Creation
    ikey = str(uuid.uuid4())
    order_resp = await client.post(
        "/api/v1/public/pay/orders",
        json={"token": pay_token},
        headers={"Idempotency-Key": ikey},
    )
    assert order_resp.status_code == 201
    assert order_resp.json()["data"]["status"] == "CREATED"
    assert order_resp.json()["data"]["amount"] == "200.00"

    # 3. Bad token gives 404
    bad_resp = await client.get("/api/v1/public/pay/invalid_token_123/summary")
    assert bad_resp.status_code == 404
    assert bad_resp.json()["error"]["code"] == "INVALID_PAY_TOKEN"


@pytest.mark.asyncio
async def test_concurrency_20_parallel_order_creations(client: AsyncClient):
    """20 simultaneous order creations for one invoice produce exactly ONE active payment (others 409)."""
    business_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    user_id = uuid.uuid4()

    await seed_invoice(invoice_id, business_id, payable=True, balance="500.00")
    token = create_test_jwt(user_id=user_id, memberships={str(business_id): "OWNER"})

    async def make_order_request():
        ikey = str(uuid.uuid4())
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Business-ID": str(business_id),
            "Idempotency-Key": ikey,
        }
        return await client.post(
            "/api/v1/payments/orders",
            json={"invoice_id": str(invoice_id)},
            headers=headers,
        )

    # Launch 20 concurrent requests
    responses = await asyncio.gather(*[make_order_request() for _ in range(20)])

    statuses = [r.status_code for r in responses]
    success_count = statuses.count(201)
    conflict_count = statuses.count(409)

    assert success_count == 1, f"Expected exactly 1 success (201), got {success_count}. Statuses: {statuses}"
    assert conflict_count == 19, f"Expected 19 conflicts (409), got {conflict_count}. Statuses: {statuses}"
