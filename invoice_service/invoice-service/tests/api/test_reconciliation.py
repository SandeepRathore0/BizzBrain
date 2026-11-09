import pytest
from datetime import date, timedelta, timezone, datetime
from uuid import uuid4
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.clients.user_service_client import user_service_client
from app.clients.financial_data_client import financial_data_client
from tests.unit.test_auth import create_test_jwt, create_test_service_token


@pytest.mark.asyncio
async def test_reconciliation_exceptions_full_lifecycle():
    biz_id_a = uuid4()
    biz_str_a = str(biz_id_a)
    owner_id_a = uuid4()
    admin_id_a = uuid4()
    employee_id_a = uuid4()

    biz_id_b = uuid4()
    biz_str_b = str(biz_id_b)
    owner_id_b = uuid4()

    token_owner_a = create_test_jwt(user_id=owner_id_a, memberships={biz_str_a: "OWNER"})
    token_admin_a = create_test_jwt(user_id=admin_id_a, memberships={biz_str_a: "ADMIN"})
    token_emp_a = create_test_jwt(user_id=employee_id_a, memberships={biz_str_a: "EMPLOYEE"})
    token_owner_b = create_test_jwt(user_id=owner_id_b, memberships={biz_str_b: "OWNER"})
    token_payment_service = create_test_service_token(client_id="payment-service")

    user_service_client.set_cached_business_metadata(biz_id_a, currency="USD", timezone="UTC")
    user_service_client.set_cached_business_metadata(biz_id_b, currency="USD", timezone="UTC")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Trigger an exception for Business A (e.g. CURRENCY_MISMATCH)
        # Create an invoice for Biz A
        cust_id = uuid4()
        user_service_client.set_authz_override(owner_id_a, biz_id_a, "invoices.issue", True)
        financial_data_client.set_cached_customer(
            cust_id,
            {"customer_id": str(cust_id), "business_id": biz_str_a, "name": "Recon Cust", "is_active": True},
        )
        due = (date.today() + timedelta(days=30)).isoformat()
        draft_resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_owner_a}", "X-Business-ID": biz_str_a, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(cust_id),
                "due_date": due,
                "currency": "USD",
                "items": [{"description": "Item", "quantity": "1.0000", "unit_price": "100.00", "tax_rate": "0.0000"}],
            },
        )
        inv_id = draft_resp.json()["data"]["invoice_id"]
        await client.post(
            f"/api/v1/invoices/{inv_id}/issue",
            headers={"Authorization": f"Bearer {token_owner_a}", "X-Business-ID": biz_str_a},
        )

        # Send invalid payment (currency mismatch EUR != USD)
        now_str = datetime.now(timezone.utc).isoformat()
        event_resp = await client.post(
            "/internal/v1/payment-events",
            headers={"Authorization": f"Bearer {token_payment_service}"},
            json={
                "event_id": str(uuid4()),
                "event_type": "PaymentCaptured",
                "event_version": 1,
                "occurred_at": now_str,
                "request_id": str(uuid4()),
                "business_id": biz_str_a,
                "payment_id": str(uuid4()),
                "invoice_id": inv_id,
                "amount": "100.00",
                "currency": "EUR",
            },
        )
        assert event_resp.status_code == 200
        assert event_resp.json()["data"]["outcome"] == "REJECTED"

        # 1. Employee cannot list reconciliation exceptions (403)
        emp_list = await client.get(
            "/api/v1/invoices/reconciliation-exceptions",
            headers={"Authorization": f"Bearer {token_emp_a}", "X-Business-ID": biz_str_a},
        )
        assert emp_list.status_code == 403

        # 2. Owner A lists reconciliation exceptions
        list_resp = await client.get(
            "/api/v1/invoices/reconciliation-exceptions",
            headers={"Authorization": f"Bearer {token_owner_a}", "X-Business-ID": biz_str_a},
        )
        assert list_resp.status_code == 200
        data = list_resp.json()["data"]
        assert len(data["items"]) >= 1
        exc_item = data["items"][0]
        exc_id = exc_item["exception_id"]
        assert exc_item["reason_code"] == "CURRENCY_MISMATCH"
        assert exc_item["status"] == "OPEN"

        # 3. Filter by status=OPEN
        open_list = await client.get(
            "/api/v1/invoices/reconciliation-exceptions?status=OPEN",
            headers={"Authorization": f"Bearer {token_owner_a}", "X-Business-ID": biz_str_a},
        )
        assert open_list.status_code == 200
        assert len(open_list.json()["data"]["items"]) >= 1

        # 4. Filter by status=RESOLVED (currently empty)
        res_list = await client.get(
            "/api/v1/invoices/reconciliation-exceptions?status=RESOLVED",
            headers={"Authorization": f"Bearer {token_owner_a}", "X-Business-ID": biz_str_a},
        )
        assert res_list.status_code == 200
        assert len(res_list.json()["data"]["items"]) == 0

        # 5. Isolation: Owner B cannot see Biz A's exceptions
        b_list = await client.get(
            "/api/v1/invoices/reconciliation-exceptions",
            headers={"Authorization": f"Bearer {token_owner_b}", "X-Business-ID": biz_str_b},
        )
        assert b_list.status_code == 200
        assert len(b_list.json()["data"]["items"]) == 0

        # 6. Isolation: Owner B cannot resolve Biz A's exception (404)
        b_resolve = await client.post(
            f"/api/v1/invoices/reconciliation-exceptions/{exc_id}/resolve",
            headers={"Authorization": f"Bearer {token_owner_b}", "X-Business-ID": biz_str_b},
            json={"resolution_note": "Attempt by other business"},
        )
        assert b_resolve.status_code == 404

        # 7. Employee cannot resolve (403)
        emp_resolve = await client.post(
            f"/api/v1/invoices/reconciliation-exceptions/{exc_id}/resolve",
            headers={"Authorization": f"Bearer {token_emp_a}", "X-Business-ID": biz_str_a},
            json={"resolution_note": "Attempt by employee"},
        )
        assert emp_resolve.status_code == 403

        # 8. Admin A successfully resolves the exception
        resolve_resp = await client.post(
            f"/api/v1/invoices/reconciliation-exceptions/{exc_id}/resolve",
            headers={"Authorization": f"Bearer {token_admin_a}", "X-Business-ID": biz_str_a},
            json={"resolution_note": "Customer instructed to re-pay in USD."},
        )
        assert resolve_resp.status_code == 200
        resolved_data = resolve_resp.json()["data"]
        assert resolved_data["status"] == "RESOLVED"
        assert resolved_data["resolved_by"] == str(admin_id_a)
        assert resolved_data["resolution_note"] == "Customer instructed to re-pay in USD."
        assert resolved_data["resolved_at"] is not None

        # 9. Second resolve returns 422 (already resolved)
        repeat_resolve = await client.post(
            f"/api/v1/invoices/reconciliation-exceptions/{exc_id}/resolve",
            headers={"Authorization": f"Bearer {token_owner_a}", "X-Business-ID": biz_str_a},
            json={"resolution_note": "Second attempt"},
        )
        assert repeat_resolve.status_code == 422

        # 10. Filter by status=RESOLVED now returns the exception
        res_list_after = await client.get(
            "/api/v1/invoices/reconciliation-exceptions?status=RESOLVED",
            headers={"Authorization": f"Bearer {token_owner_a}", "X-Business-ID": biz_str_a},
        )
        assert res_list_after.status_code == 200
        assert len(res_list_after.json()["data"]["items"]) == 1
