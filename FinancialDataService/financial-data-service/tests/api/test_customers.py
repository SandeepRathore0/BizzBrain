import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from app.models.models import Customer, AuditLog


@pytest.mark.asyncio
async def test_create_customer_success(client: AsyncClient, create_token, db_session):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
    }

    payload = {
        "name": "Acme Corp",
        "email": "acme@example.com",
        "phone": "+1234567890",
        "billing_address": {"city": "New York", "street": "5th Ave"},
        "tax_id": "TAX-12345",
    }

    resp = await client.post("/api/v1/customers", json=payload, headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["name"] == "Acme Corp"
    assert data["email"] == "acme@example.com"
    assert data["is_active"] is True
    assert data["business_id"] == str(business_id)

    # Check Audit Log in DB
    audit_stmt = select(AuditLog).where(AuditLog.entity_id == uuid.UUID(data["customer_id"]))
    audit_result = await db_session.execute(audit_stmt)
    audit_entry = audit_result.scalar_one_or_none()
    assert audit_entry is not None
    assert audit_entry.action == "CUSTOMER_CREATED"
    assert audit_entry.entity_type == "customer"
    assert audit_entry.actor_id == str(user_id)


@pytest.mark.asyncio
async def test_create_duplicate_customer_conflict(client: AsyncClient, create_token):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
    }

    payload = {
        "name": "Duplicate Inc",
        "email": "duplicate@example.com",
    }

    # First creation -> 201
    resp1 = await client.post("/api/v1/customers", json=payload, headers=headers)
    assert resp1.status_code == 201

    # Second creation with case-insensitive name -> 409
    payload_dup = {
        "name": "duplicate inc",
        "email": "duplicate@example.com",
    }
    resp2 = await client.post("/api/v1/customers", json=payload_dup, headers=headers)
    assert resp2.status_code == 409
    body2 = resp2.json()
    assert body2["success"] is False
    assert body2["error"]["code"] == "DUPLICATE_CUSTOMER"


@pytest.mark.asyncio
async def test_customer_multi_tenancy_isolation(client: AsyncClient, create_token):
    user1_id = uuid.uuid4()
    bus1_id = uuid.uuid4()
    token1 = create_token(user_id=user1_id, memberships={str(bus1_id): "OWNER"})

    user2_id = uuid.uuid4()
    bus2_id = uuid.uuid4()
    token2 = create_token(user_id=user2_id, memberships={str(bus2_id): "OWNER"})

    # Business 1 creates a customer
    resp = await client.post(
        "/api/v1/customers",
        json={"name": "Bus1 Customer"},
        headers={"Authorization": f"Bearer {token1}", "X-Business-ID": str(bus1_id)},
    )
    assert resp.status_code == 201
    customer_id = resp.json()["data"]["customer_id"]

    # Business 2 tries to GET customer -> must return 404 (no data leak)
    resp_get = await client.get(
        f"/api/v1/customers/{customer_id}",
        headers={"Authorization": f"Bearer {token2}", "X-Business-ID": str(bus2_id)},
    )
    assert resp_get.status_code == 404

    # Business 2 tries to UPDATE customer -> must return 404
    resp_put = await client.put(
        f"/api/v1/customers/{customer_id}",
        json={"name": "Hacked Name"},
        headers={"Authorization": f"Bearer {token2}", "X-Business-ID": str(bus2_id)},
    )
    assert resp_put.status_code == 404

    # Business 2 tries to DELETE customer -> must return 404
    resp_del = await client.delete(
        f"/api/v1/customers/{customer_id}",
        headers={"Authorization": f"Bearer {token2}", "X-Business-ID": str(bus2_id)},
    )
    assert resp_del.status_code == 404


@pytest.mark.asyncio
async def test_employee_permissions_on_customers(client: AsyncClient, create_token):
    emp_id = uuid.uuid4()
    bus_id = uuid.uuid4()
    emp_token = create_token(user_id=emp_id, memberships={str(bus_id): "EMPLOYEE"})
    emp_headers = {"Authorization": f"Bearer {emp_token}", "X-Business-ID": str(bus_id)}

    # Employee CAN create customer
    resp_create = await client.post(
        "/api/v1/customers",
        json={"name": "Employee Created Customer"},
        headers=emp_headers,
    )
    assert resp_create.status_code == 201
    cust_id = resp_create.json()["data"]["customer_id"]

    # Employee CAN read customer
    resp_read = await client.get(f"/api/v1/customers/{cust_id}", headers=emp_headers)
    assert resp_read.status_code == 200

    # Employee CANNOT update customer -> 403 Forbidden
    resp_update = await client.put(
        f"/api/v1/customers/{cust_id}",
        json={"name": "Updated by Employee"},
        headers=emp_headers,
    )
    assert resp_update.status_code == 403

    # Employee CANNOT deactivate customer -> 403 Forbidden
    resp_del = await client.delete(f"/api/v1/customers/{cust_id}", headers=emp_headers)
    assert resp_del.status_code == 403


@pytest.mark.asyncio
async def test_soft_deactivation_and_no_physical_delete(client: AsyncClient, create_token, db_session):
    user_id = uuid.uuid4()
    bus_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(bus_id): "OWNER"})
    headers = {"Authorization": f"Bearer {token}", "X-Business-ID": str(bus_id)}

    # Create customer
    resp_create = await client.post(
        "/api/v1/customers",
        json={"name": "To Deactivate"},
        headers=headers,
    )
    assert resp_create.status_code == 201
    cust_id = uuid.UUID(resp_create.json()["data"]["customer_id"])

    # Deactivate
    resp_del = await client.delete(f"/api/v1/customers/{cust_id}", headers=headers)
    assert resp_del.status_code == 200
    assert resp_del.json()["data"]["is_active"] is False

    # Verify customer STILL exists physically in the database
    cust_stmt = select(Customer).where(Customer.customer_id == cust_id)
    cust_result = await db_session.execute(cust_stmt)
    cust_row = cust_result.scalar_one_or_none()
    assert cust_row is not None
    assert cust_row.is_active is False

    # Verify Audit entry for DEACTIVATED
    audit_stmt = select(AuditLog).where(
        AuditLog.entity_id == cust_id,
        AuditLog.action == "CUSTOMER_DEACTIVATED",
    )
    audit_result = await db_session.execute(audit_stmt)
    audit_entry = audit_result.scalar_one_or_none()
    assert audit_entry is not None
    assert audit_entry.before == {"is_active": True}
    assert audit_entry.after == {"is_active": False}


@pytest.mark.asyncio
async def test_customer_cursor_pagination(client: AsyncClient, create_token):
    user_id = uuid.uuid4()
    bus_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(bus_id): "ADMIN"})
    headers = {"Authorization": f"Bearer {token}", "X-Business-ID": str(bus_id)}

    # Create 3 customers
    for i in range(3):
        await client.post(
            "/api/v1/customers",
            json={"name": f"Customer {i+1}"},
            headers=headers,
        )

    # Fetch with limit=2
    resp_page1 = await client.get("/api/v1/customers?limit=2", headers=headers)
    assert resp_page1.status_code == 200
    page1_data = resp_page1.json()["data"]
    assert len(page1_data["items"]) == 2
    assert page1_data["next_cursor"] is not None

    # Fetch next page using next_cursor
    next_cursor = page1_data["next_cursor"]
    resp_page2 = await client.get(f"/api/v1/customers?limit=2&cursor={next_cursor}", headers=headers)
    assert resp_page2.status_code == 200
    page2_data = resp_page2.json()["data"]
    assert len(page2_data["items"]) == 1
    assert page2_data["next_cursor"] is None
