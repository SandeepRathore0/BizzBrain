from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from app.clients.financial_data_client import financial_data_client
from app.clients.user_service_client import user_service_client
from app.core.database import async_session_factory
from app.main import app
from app.models.invoice import Invoice, PaymentAllocation
from tests.jwt_test_helpers import create_test_jwt, register_test_jwt_keys

register_test_jwt_keys()


@pytest.mark.asyncio
async def test_invoice_queries_and_pagination():
    biz_a = uuid4()
    biz_b = uuid4()
    biz_a_str = str(biz_a)
    biz_b_str = str(biz_b)

    owner_a = uuid4()
    owner_b = uuid4()
    cust_1 = uuid4()
    cust_2 = uuid4()

    token_a = create_test_jwt(user_id=owner_a, memberships={biz_a_str: "OWNER"})
    token_b = create_test_jwt(user_id=owner_b, memberships={biz_b_str: "OWNER"})

    user_service_client.set_cached_business_metadata(biz_a, currency="USD", timezone="UTC")
    user_service_client.set_cached_business_metadata(biz_b, currency="USD", timezone="UTC")
    user_service_client.set_authz_override(owner_a, biz_a, "invoices.issue", True)

    financial_data_client.set_cached_customer(
        cust_1,
        {"customer_id": str(cust_1), "business_id": biz_a_str, "name": "Customer 1", "is_active": True},
    )
    financial_data_client.set_cached_customer(
        cust_2,
        {"customer_id": str(cust_2), "business_id": biz_a_str, "name": "Customer 2", "is_active": True},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create 5 draft invoices in Business A
        invoice_ids = []
        due = (date.today() + timedelta(days=15)).isoformat()
        for i in range(5):
            cid = cust_1 if i < 3 else cust_2
            resp = await client.post(
                "/api/v1/invoices",
                headers={"Authorization": f"Bearer {token_a}", "X-Business-ID": biz_a_str, "Idempotency-Key": str(uuid4())},
                json={
                    "customer_id": str(cid),
                    "due_date": due,
                    "currency": "USD",
                    "items": [{"description": f"Item {i}", "quantity": "1.0000", "unit_price": f"{(i+1)*10}.00", "tax_rate": "0.0000"}],
                },
            )
            assert resp.status_code == 201
            invoice_ids.append(resp.json()["data"]["invoice_id"])

        # Also create 1 draft invoice in Business B to test isolation
        resp_b = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_b}", "X-Business-ID": biz_b_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(uuid4()),
                "due_date": due,
                "currency": "USD",
                "items": [{"description": "Biz B Item", "quantity": "1.0000", "unit_price": "99.00", "tax_rate": "0.0000"}],
            },
        )
        assert resp_b.status_code == 201

        # 1. Test Multi-Tenant Isolation
        # Listing invoices for Business B should return only 1 item
        list_b = await client.get(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_b}", "X-Business-ID": biz_b_str},
        )
        assert list_b.status_code == 200
        assert len(list_b.json()["data"]["items"]) == 1

        # Trying to access Business A's invoice with Business B token returns 404
        access_foreign = await client.get(
            f"/api/v1/invoices/{invoice_ids[0]}",
            headers={"Authorization": f"Bearer {token_b}", "X-Business-ID": biz_b_str},
        )
        assert access_foreign.status_code == 404

        # 2. Test Cursor Pagination (Page size = 2)
        page1 = await client.get(
            "/api/v1/invoices?limit=2",
            headers={"Authorization": f"Bearer {token_a}", "X-Business-ID": biz_a_str},
        )
        assert page1.status_code == 200
        items_p1 = page1.json()["data"]["items"]
        cursor1 = page1.json()["data"]["next_cursor"]
        assert len(items_p1) == 2
        assert cursor1 is not None

        # Fetch page 2 using cursor
        page2 = await client.get(
            f"/api/v1/invoices?limit=2&cursor={cursor1}",
            headers={"Authorization": f"Bearer {token_a}", "X-Business-ID": biz_a_str},
        )
        assert page2.status_code == 200
        items_p2 = page2.json()["data"]["items"]
        cursor2 = page2.json()["data"]["next_cursor"]
        assert len(items_p2) == 2
        assert cursor2 is not None
        # Disjoint sets across pages
        p1_ids = {it["invoice_id"] for it in items_p1}
        p2_ids = {it["invoice_id"] for it in items_p2}
        assert p1_ids.isdisjoint(p2_ids)

        # 3. Test Customer Filter
        cust1_resp = await client.get(
            f"/api/v1/invoices?customer_id={cust_1}",
            headers={"Authorization": f"Bearer {token_a}", "X-Business-ID": biz_a_str},
        )
        assert cust1_resp.status_code == 200
        assert len(cust1_resp.json()["data"]["items"]) == 3

        # 4. Issue first invoice and test status filter & search
        issue_resp = await client.post(
            f"/api/v1/invoices/{invoice_ids[0]}/issue",
            headers={"Authorization": f"Bearer {token_a}", "X-Business-ID": biz_a_str},
        )
        assert issue_resp.status_code == 200
        inv_num = issue_resp.json()["data"]["invoice_number"]

        issued_filter = await client.get(
            "/api/v1/invoices?status=ISSUED",
            headers={"Authorization": f"Bearer {token_a}", "X-Business-ID": biz_a_str},
        )
        assert issued_filter.status_code == 200
        assert len(issued_filter.json()["data"]["items"]) == 1
        assert issued_filter.json()["data"]["items"][0]["invoice_number"] == inv_num

        search_resp = await client.get(
            f"/api/v1/invoices?search={inv_num}",
            headers={"Authorization": f"Bearer {token_a}", "X-Business-ID": biz_a_str},
        )
        assert search_resp.status_code == 200
        assert len(search_resp.json()["data"]["items"]) == 1


@pytest.mark.asyncio
async def test_overdue_and_balance_subresources():
    biz_id = uuid4()
    biz_str = str(biz_id)
    owner_id = uuid4()
    cust_id = uuid4()

    token_owner = create_test_jwt(user_id=owner_id, memberships={biz_str: "OWNER"})

    user_service_client.set_cached_business_metadata(biz_id, currency="USD", timezone="UTC")
    user_service_client.set_authz_override(owner_id, biz_id, "invoices.issue", True)
    financial_data_client.set_cached_customer(
        cust_id,
        {"customer_id": str(cust_id), "business_id": biz_str, "name": "Cust", "is_active": True},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create invoice due today
        today = date.today().isoformat()
        draft_resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(cust_id),
                "due_date": today,
                "currency": "USD",
                "items": [{"description": "Item", "quantity": "1.0000", "unit_price": "200.00", "tax_rate": "0.0000"}],
            },
        )
        inv_id = draft_resp.json()["data"]["invoice_id"]

        # Issue it
        await client.post(
            f"/api/v1/invoices/{inv_id}/issue",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )

        # 1. Test Balance subresource
        bal_resp = await client.get(
            f"/api/v1/invoices/{inv_id}/balance",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )
        assert bal_resp.status_code == 200
        bal_data = bal_resp.json()["data"]
        assert bal_data["total_amount"] == "200.00"
        assert bal_data["amount_paid"] == "0.00"
        assert bal_data["outstanding_balance"] == "200.00"
        assert bal_data["currency"] == "USD"
        assert bal_data["is_overdue"] is False  # due today, not overdue yet

        # 2. Test Allocations subresource (initially empty)
        alloc_resp = await client.get(
            f"/api/v1/invoices/{inv_id}/allocations",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )
        assert alloc_resp.status_code == 200
        assert alloc_resp.json()["data"] == []

        # Add an allocation directly in DB to simulate payment
        async with async_session_factory() as session:
            alloc = PaymentAllocation(
                allocation_id=uuid4(),
                invoice_id=UUID(inv_id),
                payment_id=uuid4(),
                event_id=uuid4(),
                allocated_amount=Decimal("50.0000"),
            )
            session.add(alloc)
            await session.commit()

        alloc_resp2 = await client.get(
            f"/api/v1/invoices/{inv_id}/allocations",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )
        assert alloc_resp2.status_code == 200
        assert len(alloc_resp2.json()["data"]) == 1
        assert alloc_resp2.json()["data"][0]["allocated_amount"] == "50.00"

        # 3. Simulate overdue by setting due_date to yesterday in DB
        async with async_session_factory() as session:
            stmt = select(Invoice).where(Invoice.invoice_id == UUID(inv_id))
            res = await session.execute(stmt)
            inv_obj = res.scalar_one()
            inv_obj.issue_date = date.today() - timedelta(days=10)
            inv_obj.due_date = date.today() - timedelta(days=2)
            await session.commit()

        # Check /invoices/overdue dedicated endpoint (declared before /{invoice_id})
        overdue_resp = await client.get(
            "/api/v1/invoices/overdue",
            headers={"Authorization": f"Bearer {token_owner}", "X-Business-ID": biz_str},
        )
        assert overdue_resp.status_code == 200
        overdue_items = overdue_resp.json()["data"]["items"]
        assert len(overdue_items) == 1
        assert overdue_items[0]["invoice_id"] == inv_id
        assert overdue_items[0]["is_overdue"] is True
