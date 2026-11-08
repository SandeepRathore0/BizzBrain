from uuid import uuid4
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from tests.jwt_test_helpers import create_test_jwt, register_test_jwt_keys

register_test_jwt_keys()


@pytest.mark.asyncio
async def test_settings_crud_and_permissions():
    biz_a = str(uuid4())
    owner_token = create_test_jwt(memberships={biz_a: "OWNER"})
    employee_token = create_test_jwt(memberships={biz_a: "EMPLOYEE"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Get default settings
        resp = await client.get(
            "/api/v1/invoice-settings",
            headers={"Authorization": f"Bearer {owner_token}", "X-Business-ID": biz_a},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["invoice_prefix"] == "INV-"
        assert data["default_due_days"] == 30

        # 2. Employee cannot update settings (requires settings.manage -> 403)
        resp = await client.put(
            "/api/v1/invoice-settings",
            headers={"Authorization": f"Bearer {employee_token}", "X-Business-ID": biz_a},
            json={"invoice_prefix": "ACME-", "seller_name": "Acme Employee"},
        )
        assert resp.status_code == 403

        # 3. Owner updates settings
        resp = await client.put(
            "/api/v1/invoice-settings",
            headers={"Authorization": f"Bearer {owner_token}", "X-Business-ID": biz_a},
            json={
                "invoice_prefix": "ACME-",
                "seller_name": "Acme Corporation",
                "seller_address": {"street": "100 Market St", "city": "Cityville"},
                "seller_tax_id": "TAX-998877",
                "default_due_days": 45,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["invoice_prefix"] == "ACME-"
        assert data["seller_name"] == "Acme Corporation"
        assert data["default_due_days"] == 45


@pytest.mark.asyncio
async def test_tax_rates_crud_and_isolation():
    biz_a = str(uuid4())
    biz_b = str(uuid4())
    token_a = create_test_jwt(memberships={biz_a: "OWNER"})
    token_b = create_test_jwt(memberships={biz_b: "OWNER"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create 18% GST in Business A
        resp = await client.post(
            "/api/v1/tax-rates",
            headers={"Authorization": f"Bearer {token_a}", "X-Business-ID": biz_a},
            json={"name": "GST 18%", "rate": "0.1800"},
        )
        assert resp.status_code == 201
        rate_a = resp.json()["data"]
        assert rate_a["name"] == "GST 18%"
        assert rate_a["rate"] == "0.1800"
        rate_a_id = rate_a["tax_rate_id"]

        # 2. Reject duplicate rate with same name
        resp = await client.post(
            "/api/v1/tax-rates",
            headers={"Authorization": f"Bearer {token_a}", "X-Business-ID": biz_a},
            json={"name": "GST 18%", "rate": "0.1800"},
        )
        assert resp.status_code == 409

        # 3. Business B lists tax rates: should be empty (Multi-tenancy isolation)
        resp = await client.get(
            "/api/v1/tax-rates",
            headers={"Authorization": f"Bearer {token_b}", "X-Business-ID": biz_b},
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 0

        # 4. Business B trying to patch Business A's tax rate returns 404 (No leak)
        resp = await client.patch(
            f"/api/v1/tax-rates/{rate_a_id}",
            headers={"Authorization": f"Bearer {token_b}", "X-Business-ID": biz_b},
            json={"name": "Hacked Name"},
        )
        assert resp.status_code == 404

        # 5. Business A deactivates rate
        resp = await client.patch(
            f"/api/v1/tax-rates/{rate_a_id}",
            headers={"Authorization": f"Bearer {token_a}", "X-Business-ID": biz_a},
            json={"is_active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["is_active"] is False
