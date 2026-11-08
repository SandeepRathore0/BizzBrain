import asyncio
from datetime import date, timedelta
from uuid import UUID, uuid4
import pytest
from httpx import ASGITransport, AsyncClient
from app.clients.financial_data_client import financial_data_client
from app.clients.user_service_client import user_service_client
from app.main import app
from tests.jwt_test_helpers import create_test_jwt, register_test_jwt_keys

register_test_jwt_keys()


@pytest.mark.asyncio
async def test_twenty_concurrent_invoice_issues_gap_free():
    biz_id = uuid4()
    biz_str = str(biz_id)
    owner_id = uuid4()
    cust_id = uuid4()

    token_owner = create_test_jwt(user_id=owner_id, memberships={biz_str: "OWNER"})

    user_service_client.set_cached_business_metadata(biz_id, currency="USD", timezone="UTC")
    user_service_client.set_authz_override(owner_id, biz_id, "invoices.issue", True)
    financial_data_client.set_cached_customer(
        cust_id,
        {"customer_id": str(cust_id), "business_id": biz_str, "name": "Bulk Corp", "is_active": True},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create 20 drafts
        due = (date.today() + timedelta(days=30)).isoformat()
        draft_ids = []
        for i in range(20):
            resp = await client.post(
                "/api/v1/invoices",
                headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
                json={
                    "customer_id": str(cust_id),
                    "due_date": due,
                    "currency": "USD",
                    "items": [{"description": f"Item {i}", "quantity": "1.0000", "unit_price": "100.00", "tax_rate": "0.0000"}],
                },
            )
            assert resp.status_code == 201
            draft_ids.append(resp.json()["data"]["invoice_id"])

        # Now issue all 20 drafts concurrently
        async def _issue_draft(inv_id: str):
            resp = await client.post(
                f"/api/v1/invoices/{inv_id}/issue",
                headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
            )
            return resp

        responses = await asyncio.gather(*[_issue_draft(inv_id) for inv_id in draft_ids])

        # All 20 must have succeeded with 200 OK
        numbers = []
        for r in responses:
            assert r.status_code == 200, f"Issue failed: {r.text}"
            numbers.append(r.json()["data"]["invoice_number"])

        # Check for exactly 20 unique numbers
        assert len(set(numbers)) == 20

        # Parse numeric parts: must be strictly 1 to 20 with NO gaps
        num_ints = sorted([int(n.replace("INV-", "")) for n in numbers])
        assert num_ints == list(range(1, 21))
