from uuid import UUID, uuid4
import pytest
from httpx import ASGITransport, AsyncClient
from app.clients.user_service_client import user_service_client
from app.main import app
from tests.jwt_test_helpers import create_test_jwt, register_test_jwt_keys

register_test_jwt_keys()


@pytest.mark.asyncio
async def test_draft_invoice_crud_and_idempotency():
    biz_a = str(uuid4())
    user_owner_id = uuid4()
    user_emp1_id = uuid4()
    user_emp2_id = uuid4()

    token_owner = create_test_jwt(user_id=user_owner_id, memberships={biz_a: "OWNER"})
    token_emp1 = create_test_jwt(user_id=user_emp1_id, memberships={biz_a: "EMPLOYEE"})
    token_emp2 = create_test_jwt(user_id=user_emp2_id, memberships={biz_a: "EMPLOYEE"})

    # Setup base currency for Business A in user_service_client cache
    user_service_client.set_cached_business_metadata(UUID(biz_a), currency="USD", timezone="UTC")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. First, create an active tax rate 18% in Business A
        resp = await client.post(
            "/api/v1/tax-rates",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_a},
            json={"name": "VAT 18%", "rate": "0.1800"},
        )
        assert resp.status_code == 201

        # 2. Employee 1 creates a draft invoice with line items
        idempotency_key = str(uuid4())
        draft_payload = {
            "customer_id": str(uuid4()),
            "due_date": "2026-12-15",
            "currency": "USD",
            "items": [
                {
                    "description": "Web Development",
                    "quantity": "10.0000",
                    "unit_price": "100.00",
                    "tax_rate": "0.1800",
                },
                {
                    "description": "Hosting Setup",
                    "quantity": "1.0000",
                    "unit_price": "50.00",
                    "tax_rate": "0.0000",
                },
            ],
            "notes": "Payment due within 30 days",
            "min_installment_amount": "200.00",
        }

        resp = await client.post(
            "/api/v1/invoices",
            headers={
                "Authorization": f"Bearer {token_emp1}",
                "X-Business-ID": biz_a,
                "Idempotency-Key": idempotency_key,
            },
            json=draft_payload,
        )
        assert resp.status_code == 201
        inv_data = resp.json()["data"]
        inv_id = inv_data["invoice_id"]
        assert inv_data["status"] == "DRAFT"
        # Subtotal = 10*100 + 1*50 = 1050.00. Tax = 1000*0.18 + 0 = 180.00. Total = 1230.00
        assert inv_data["subtotal"] == "1050.00"
        assert inv_data["tax_amount"] == "180.00"
        assert inv_data["total_amount"] == "1230.00"
        assert inv_data["outstanding_balance"] == "1230.00"
        assert len(inv_data["items"]) == 2

        # 3. Idempotency test: Replaying with identical key and payload replays response
        resp_replay = await client.post(
            "/api/v1/invoices",
            headers={
                "Authorization": f"Bearer {token_emp1}",
                "X-Business-ID": biz_a,
                "Idempotency-Key": idempotency_key,
            },
            json=draft_payload,
        )
        assert resp_replay.status_code == 201
        assert resp_replay.headers.get("Idempotent-Replayed") == "true"
        assert resp_replay.json()["data"]["invoice_id"] == inv_id

        # 4. Idempotency test: Reusing same key with different payload returns 422 IDEMPOTENCY_KEY_REUSED
        different_payload = dict(draft_payload)
        different_payload["notes"] = "Changed notes"
        resp_conflict = await client.post(
            "/api/v1/invoices",
            headers={
                "Authorization": f"Bearer {token_emp1}",
                "X-Business-ID": biz_a,
                "Idempotency-Key": idempotency_key,
            },
            json=different_payload,
        )
        assert resp_conflict.status_code == 422
        assert resp_conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

        # 5. Get invoice by ID
        resp_get = await client.get(
            f"/api/v1/invoices/{inv_id}",
            headers={"Authorization": f"Bearer {token_emp1}", "X-Business-ID": biz_a},
        )
        assert resp_get.status_code == 200
        assert resp_get.json()["data"]["invoice_id"] == inv_id

        # 6. Multi-tenancy isolation: Business B cannot get Business A's invoice (returns 404)
        biz_b = str(uuid4())
        token_b = create_test_jwt(memberships={biz_b: "OWNER"})
        resp_foreign = await client.get(
            f"/api/v1/invoices/{inv_id}",
            headers={"Authorization": f"Bearer {token_b}", "X-Business-ID": biz_b},
        )
        assert resp_foreign.status_code == 404

        # 7. Employee 2 tries to edit Employee 1's draft: forbidden (403)
        resp_emp2_edit = await client.patch(
            f"/api/v1/invoices/{inv_id}",
            headers={"Authorization": f"Bearer {token_emp2}", "X-Business-ID": biz_a},
            json={"notes": "Hacked by Emp 2"},
        )
        assert resp_emp2_edit.status_code == 403

        # 8. Employee 1 edits their own draft items: updates items and recalculates totals
        resp_edit = await client.patch(
            f"/api/v1/invoices/{inv_id}",
            headers={"Authorization": f"Bearer {token_emp1}", "X-Business-ID": biz_a},
            json={
                "items": [
                    {
                        "description": "Web Development Extended",
                        "quantity": "20.0000",
                        "unit_price": "100.00",
                        "tax_rate": "0.1800",
                    }
                ]
            },
        )
        assert resp_edit.status_code == 200
        edited_data = resp_edit.json()["data"]
        # Subtotal = 2000.00. Tax = 360.00. Total = 2360.00
        assert edited_data["subtotal"] == "2000.00"
        assert edited_data["tax_amount"] == "360.00"
        assert edited_data["total_amount"] == "2360.00"
        assert len(edited_data["items"]) == 1


@pytest.mark.asyncio
async def test_draft_invoice_validations_and_rejections():
    biz_a = str(uuid4())
    token_owner = create_test_jwt(memberships={biz_a: "OWNER"})
    user_service_client.set_cached_business_metadata(UUID(biz_a), currency="USD", timezone="UTC")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Reject client-supplied totals (422)
        resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_a, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(uuid4()),
                "due_date": "2026-12-15",
                "currency": "USD",
                "subtotal": "100.00",  # FORBIDDEN
                "items": [{"description": "Item", "quantity": "1", "unit_price": "100", "tax_rate": "0"}],
            },
        )
        assert resp.status_code == 422
        assert "subtotal" in resp.json()["error"]["message"]

        # 2. Reject floats (422)
        resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_a, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(uuid4()),
                "due_date": "2026-12-15",
                "currency": "USD",
                "items": [{"description": "Item", "quantity": 1.5, "unit_price": 100.0, "tax_rate": 0.0}],
            },
        )
        assert resp.status_code == 422

        # 3. Reject currency mismatch against base_currency (USD vs EUR)
        resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_a, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(uuid4()),
                "due_date": "2026-12-15",
                "currency": "EUR",  # Mismatch
                "items": [{"description": "Item", "quantity": "1", "unit_price": "100", "tax_rate": "0"}],
            },
        )
        assert resp.status_code == 422
        assert "base currency" in resp.json()["error"]["message"]

        # 4. Reject unconfigured tax rate
        resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_a, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(uuid4()),
                "due_date": "2026-12-15",
                "currency": "USD",
                "items": [{"description": "Item", "quantity": "1", "unit_price": "100", "tax_rate": "0.2800"}],  # 28% not configured
            },
        )
        assert resp.status_code == 422
        assert "tax rate" in resp.json()["error"]["message"]
