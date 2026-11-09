import uuid
from decimal import Decimal
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.paypal_client import get_paypal_client, FakePayPalClient
from app.models.payment import Payment
from fake_paypal.main import app as fake_paypal_app
from tests.integration.test_order_creation import seed_invoice
from tests.test_keys import create_test_jwt


@pytest.mark.asyncio
async def test_order_creation_paypal_error_marks_payment_failed(client: AsyncClient, db_session: AsyncSession):
    business_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    user_id = uuid.uuid4()

    await seed_invoice(invoice_id, business_id, payable=True, balance="100.00")

    # Configure Fake PayPal to return 500 error
    async with AsyncClient(transport=pytest.importorskip("httpx").ASGITransport(app=fake_paypal_app), base_url="http://fake-paypal") as fp_client:
        await fp_client.post("/test/config", json={"force_status": 500})

    token = create_test_jwt(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": str(uuid.uuid4()),
    }

    resp = await client.post(
        "/api/v1/payments/orders",
        json={"invoice_id": str(invoice_id)},
        headers=headers,
    )
    # Reset PayPal mock
    async with AsyncClient(transport=pytest.importorskip("httpx").ASGITransport(app=fake_paypal_app), base_url="http://fake-paypal") as fp_client:
        await fp_client.post("/test/reset")

    # Must return 502/503
    assert resp.status_code in (502, 503)
    assert resp.json()["success"] is False

    # Check database: payment must be in FAILED status, NOT CREATED
    stmt = select(Payment).where(Payment.invoice_id == invoice_id)
    result = await db_session.execute(stmt)
    p = result.scalar_one_or_none()
    assert p is not None
    assert p.status == "FAILED"
    assert "PAYPAL_ORDER_CREATION_FAILED" in (p.status_reason or "")
