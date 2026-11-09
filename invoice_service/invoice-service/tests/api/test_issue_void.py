from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from app.clients.financial_data_client import financial_data_client
from app.clients.user_service_client import user_service_client
from app.core.database import async_session_factory
from app.core.errors import DependencyException
from app.main import app
from app.models.invoice import AuditLog, Invoice, PaymentAllocation
from tests.jwt_test_helpers import create_test_jwt, register_test_jwt_keys

register_test_jwt_keys()


@pytest.mark.asyncio
async def test_issue_flow_complete(monkeypatch):
    biz_id = uuid4()
    biz_str = str(biz_id)
    owner_id = uuid4()
    customer_id = uuid4()

    token_owner = create_test_jwt(user_id=owner_id, memberships={biz_str: "OWNER"})

    # Setup User Service client metadata & live authz
    user_service_client.set_cached_business_metadata(biz_id, currency="USD", timezone="UTC")
    user_service_client.set_authz_override(owner_id, biz_id, "invoices.issue", True)

    # Setup Financial Data Service customer
    customer_data = {
        "customer_id": str(customer_id),
        "business_id": biz_str,
        "name": "Acme Corp",
        "email": "billing@acme.com",
        "billing_address": {"street": "123 Main St", "city": "Metropolis"},
        "tax_id": "TAX-12345",
        "is_active": True,
    }
    financial_data_client.set_cached_customer(customer_id, customer_data)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create a draft invoice
        due = (date.today() + timedelta(days=30)).isoformat()
        draft_resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(customer_id),
                "due_date": due,
                "currency": "USD",
                "items": [
                    {
                        "description": "Consulting Services",
                        "quantity": "5.0000",
                        "unit_price": "200.00",
                        "tax_rate": "0.0000",
                    }
                ],
                "notes": "Thank you for your business",
            },
        )
        assert draft_resp.status_code == 201
        invoice_id = draft_resp.json()["data"]["invoice_id"]

        # 2. Test Dependency Failure (Contract C1): if FDS raises DependencyException -> 503
        async def mock_get_customer_fail(business_id, customer_id):
            raise DependencyException("Financial Data Service is down")

        monkeypatch.setattr(financial_data_client, "get_customer", mock_get_customer_fail)
        fail_resp = await client.post(
            f"/api/v1/invoices/{invoice_id}/issue",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )
        assert fail_resp.status_code == 503
        assert fail_resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

        # Restore normal get_customer
        monkeypatch.undo()
        financial_data_client.set_cached_customer(customer_id, customer_data)

        # 3. Test Inactive Customer rejection
        inactive_cust_id = uuid4()
        financial_data_client.set_cached_customer(
            inactive_cust_id,
            {"customer_id": str(inactive_cust_id), "business_id": biz_str, "is_active": False},
        )
        # Create draft with inactive customer
        draft2_resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(inactive_cust_id),
                "due_date": due,
                "currency": "USD",
                "items": [{"description": "Item", "quantity": "1.0000", "unit_price": "10.00", "tax_rate": "0.0000"}],
            },
        )
        inv2_id = draft2_resp.json()["data"]["invoice_id"]
        inactive_issue_resp = await client.post(
            f"/api/v1/invoices/{inv2_id}/issue",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )
        assert inactive_issue_resp.status_code == 422
        assert "inactive customer" in inactive_issue_resp.json()["error"]["message"]

        # 4. Successfully issue the original invoice
        issue_resp = await client.post(
            f"/api/v1/invoices/{invoice_id}/issue",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
        )
        assert issue_resp.status_code == 200
        issue_data = issue_resp.json()["data"]

        assert issue_data["status"] == "ISSUED"
        assert issue_data["invoice_number"] == "INV-000001"
        assert issue_data["customer_snapshot"]["name"] == "Acme Corp"
        assert issue_data["customer_snapshot"]["tax_id"] == "TAX-12345"
        raw_token = issue_data["pay_token"]
        assert raw_token is not None and len(raw_token) > 20

        # 5. Snapshot Immutability: Even if customer changes in FDS, snapshot remains intact
        financial_data_client.set_cached_customer(
            customer_id,
            {"customer_id": str(customer_id), "business_id": biz_str, "name": "Changed Name", "is_active": True},
        )
        get_resp = await client.get(
            f"/api/v1/invoices/{invoice_id}",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["customer_snapshot"]["name"] == "Acme Corp"

        # 6. Verify Audit Log does NOT contain raw token or pay token hash
        async with async_session_factory() as session:
            stmt = select(AuditLog).where(
                AuditLog.business_id == biz_id,
                AuditLog.action == "INVOICE_ISSUED",
            )
            res = await session.execute(stmt)
            audit = res.scalar_one()
            assert audit is not None
            # Ensure raw_token is nowhere in audit
            assert raw_token not in str(audit.after)
            assert raw_token not in str(audit.before)

        # 7. Re-issuing an already issued invoice returns 409
        reissue_resp = await client.post(
            f"/api/v1/invoices/{invoice_id}/issue",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )
        assert reissue_resp.status_code == 409
        assert reissue_resp.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


@pytest.mark.asyncio
async def test_void_flow_and_restrictions():
    biz_id = uuid4()
    biz_str = str(biz_id)
    owner_id = uuid4()
    cust_id = uuid4()

    token_owner = create_test_jwt(user_id=owner_id, memberships={biz_str: "OWNER"})

    user_service_client.set_cached_business_metadata(biz_id, currency="USD", timezone="UTC")
    user_service_client.set_authz_override(owner_id, biz_id, "invoices.issue", True)
    user_service_client.set_authz_override(owner_id, biz_id, "invoices.void", True)
    financial_data_client.set_cached_customer(
        cust_id,
        {"customer_id": str(cust_id), "business_id": biz_str, "name": "Client", "is_active": True},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Voiding a DRAFT invoice directly
        due = (date.today() + timedelta(days=30)).isoformat()
        draft_resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(cust_id),
                "due_date": due,
                "currency": "USD",
                "items": [{"description": "Item", "quantity": "1.0000", "unit_price": "50.00", "tax_rate": "0.0000"}],
            },
        )
        draft_id = draft_resp.json()["data"]["invoice_id"]

        void_draft_resp = await client.post(
            f"/api/v1/invoices/{draft_id}/void",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
            json={"void_reason": "Created by mistake"},
        )
        assert void_draft_resp.status_code == 200
        assert void_draft_resp.json()["data"]["status"] == "VOID"
        assert void_draft_resp.json()["data"]["void_reason"] == "Created by mistake"

        # 2. Voiding an ISSUED invoice with NO payments succeeds
        draft2_resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(cust_id),
                "due_date": due,
                "currency": "USD",
                "items": [{"description": "Item", "quantity": "1.0000", "unit_price": "50.00", "tax_rate": "0.0000"}],
            },
        )
        inv2_id = draft2_resp.json()["data"]["invoice_id"]
        await client.post(
            f"/api/v1/invoices/{inv2_id}/issue",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )

        void_issued_resp = await client.post(
            f"/api/v1/invoices/{inv2_id}/void",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
            json={"void_reason": "Order cancelled by client"},
        )
        assert void_issued_resp.status_code == 200
        assert void_issued_resp.json()["data"]["status"] == "VOID"

        # 3. Voiding an invoice with payments/allocations is strictly forbidden (409)
        draft3_resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(cust_id),
                "due_date": due,
                "currency": "USD",
                "items": [{"description": "Item", "quantity": "1.0000", "unit_price": "100.00", "tax_rate": "0.0000"}],
            },
        )
        inv3_id = UUID(draft3_resp.json()["data"]["invoice_id"])
        await client.post(
            f"/api/v1/invoices/{inv3_id}/issue",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )

        # Insert a simulated payment allocation
        async with async_session_factory() as session:
            alloc = PaymentAllocation(
                allocation_id=uuid4(),
                invoice_id=inv3_id,
                payment_id=uuid4(),
                event_id=uuid4(),
                allocated_amount=Decimal("50.0000"),
            )
            session.add(alloc)
            stmt = select(Invoice).where(Invoice.invoice_id == inv3_id)
            res = await session.execute(stmt)
            inv_obj = res.scalar_one()
            inv_obj.amount_paid = Decimal("50.0000")
            inv_obj.status = "PARTIALLY_PAID"
            await session.commit()

        void_paid_resp = await client.post(
            f"/api/v1/invoices/{inv3_id}/void",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
            json={"void_reason": "Attempting to void partially paid"},
        )
        assert void_paid_resp.status_code == 409
        assert void_paid_resp.json()["error"]["code"] == "INVOICE_HAS_PAYMENTS"


@pytest.mark.asyncio
async def test_pay_link_regeneration():
    biz_id = uuid4()
    biz_str = str(biz_id)
    owner_id = uuid4()
    cust_id = uuid4()

    token_owner = create_test_jwt(user_id=owner_id, memberships={biz_str: "OWNER"})

    user_service_client.set_cached_business_metadata(biz_id, currency="USD", timezone="UTC")
    user_service_client.set_authz_override(owner_id, biz_id, "invoices.issue", True)
    financial_data_client.set_cached_customer(
        cust_id,
        {"customer_id": str(cust_id), "business_id": biz_str, "name": "Client", "is_active": True},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create and issue invoice
        due = (date.today() + timedelta(days=30)).isoformat()
        draft_resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(cust_id),
                "due_date": due,
                "currency": "USD",
                "items": [{"description": "Item", "quantity": "1.0000", "unit_price": "50.00", "tax_rate": "0.0000"}],
            },
        )
        inv_id = draft_resp.json()["data"]["invoice_id"]

        # Regenerating pay-link on DRAFT returns 422
        bad_link_resp = await client.post(
            f"/api/v1/invoices/{inv_id}/pay-link",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )
        assert bad_link_resp.status_code == 422

        # Now issue
        issue_resp = await client.post(
            f"/api/v1/invoices/{inv_id}/issue",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )
        first_token = issue_resp.json()["data"]["pay_token"]

        # Regenerate pay-link
        regen_resp = await client.post(
            f"/api/v1/invoices/{inv_id}/pay-link",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )
        assert regen_resp.status_code == 200
        new_token = regen_resp.json()["data"]["pay_token"]
        assert new_token != first_token



@pytest.mark.asyncio
async def test_issue_and_void_authz_and_edge_rejections():
    biz_id = uuid4()
    biz_str = str(biz_id)
    owner_id = uuid4()
    cust_id = uuid4()

    token_owner = create_test_jwt(user_id=owner_id, memberships={biz_str: "OWNER"})

    user_service_client.set_cached_business_metadata(biz_id, currency="USD", timezone="UTC")
    financial_data_client.set_cached_customer(
        cust_id,
        {"customer_id": str(cust_id), "business_id": biz_str, "name": "Edge Cust", "is_active": True},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create draft
        due = (date.today() + timedelta(days=30)).isoformat()
        draft_resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(cust_id),
                "due_date": due,
                "currency": "USD",
                "items": [{"description": "Item", "quantity": "1.0000", "unit_price": "50.00", "tax_rate": "0.0000"}],
            },
        )
        inv_id = draft_resp.json()["data"]["invoice_id"]

        # 1. Live Authz Denied on Issue
        user_service_client.set_authz_override(owner_id, biz_id, "invoices.issue", False)
        authz_denied_resp = await client.post(
            f"/api/v1/invoices/{inv_id}/issue",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )
        assert authz_denied_resp.status_code == 403

        # 2. Foreign business customer rejection
        user_service_client.set_authz_override(owner_id, biz_id, "invoices.issue", True)
        foreign_cust_id = uuid4()
        financial_data_client.set_cached_customer(
            foreign_cust_id,
            {"customer_id": str(foreign_cust_id), "business_id": str(uuid4()), "name": "Foreign", "is_active": True},
        )
        draft_foreign = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(foreign_cust_id),
                "due_date": due,
                "currency": "USD",
                "items": [{"description": "Item", "quantity": "1.0000", "unit_price": "50.00", "tax_rate": "0.0000"}],
            },
        )
        foreign_inv_id = draft_foreign.json()["data"]["invoice_id"]
        foreign_issue_resp = await client.post(
            f"/api/v1/invoices/{foreign_inv_id}/issue",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )
        assert foreign_issue_resp.status_code == 422
        assert "another business" in foreign_issue_resp.json()["error"]["message"]

        # 3. Missing customer rejection
        missing_cust_id = uuid4()
        financial_data_client.set_cached_customer(missing_cust_id, None)
        draft_missing = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(missing_cust_id),
                "due_date": due,
                "currency": "USD",
                "items": [{"description": "Item", "quantity": "1.0000", "unit_price": "50.00", "tax_rate": "0.0000"}],
            },
        )
        missing_inv_id = draft_missing.json()["data"]["invoice_id"]
        missing_issue_resp = await client.post(
            f"/api/v1/invoices/{missing_inv_id}/issue",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )
        assert missing_issue_resp.status_code == 422
        assert "not found" in missing_issue_resp.json()["error"]["message"].lower()

        # 4. Due date before issue date rejection
        draft_past_due = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(cust_id),
                "due_date": (date.today() - timedelta(days=5)).isoformat(),
                "currency": "USD",
                "items": [{"description": "Item", "quantity": "1.0000", "unit_price": "50.00", "tax_rate": "0.0000"}],
            },
        )
        past_inv_id = draft_past_due.json()["data"]["invoice_id"]
        past_issue_resp = await client.post(
            f"/api/v1/invoices/{past_inv_id}/issue",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )
        assert past_issue_resp.status_code == 422
        assert "cannot be before issue date" in past_issue_resp.json()["error"]["message"]

        # 5. Live Authz Denied on Void
        user_service_client.set_authz_override(owner_id, biz_id, "invoices.void", False)
        void_denied_resp = await client.post(
            f"/api/v1/invoices/{inv_id}/void",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
            json={"void_reason": "No permission"},
        )
        assert void_denied_resp.status_code == 403
