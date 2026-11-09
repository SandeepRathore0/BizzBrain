import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from app.models.models import Supplier, AuditLog


@pytest.mark.asyncio
async def test_create_supplier_success(client: AsyncClient, create_token, db_session):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
    }

    payload = {
        "name": "Global Supplies LLC",
        "email": "supplies@example.com",
        "phone": "+1987654321",
        "address": {"city": "Chicago", "street": "Michigan Ave"},
    }

    resp = await client.post("/api/v1/suppliers", json=payload, headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["name"] == "Global Supplies LLC"
    assert data["is_active"] is True
    assert data["business_id"] == str(business_id)

    # Check Audit Log in DB
    audit_stmt = select(AuditLog).where(AuditLog.entity_id == uuid.UUID(data["supplier_id"]))
    audit_result = await db_session.execute(audit_stmt)
    audit_entry = audit_result.scalar_one_or_none()
    assert audit_entry is not None
    assert audit_entry.action == "SUPPLIER_CREATED"
    assert audit_entry.entity_type == "supplier"


@pytest.mark.asyncio
async def test_employee_cannot_manage_suppliers(client: AsyncClient, create_token):
    user_id = uuid.uuid4()
    bus_id = uuid.uuid4()
    owner_token = create_token(user_id=user_id, memberships={str(bus_id): "OWNER"})
    emp_token = create_token(user_id=uuid.uuid4(), memberships={str(bus_id): "EMPLOYEE"})

    owner_headers = {"Authorization": f"Bearer {owner_token}", "X-Business-ID": str(bus_id)}
    emp_headers = {"Authorization": f"Bearer {emp_token}", "X-Business-ID": str(bus_id)}

    # Owner creates supplier
    resp = await client.post(
        "/api/v1/suppliers",
        json={"name": "Owner Supplier"},
        headers=owner_headers,
    )
    assert resp.status_code == 201
    supplier_id = resp.json()["data"]["supplier_id"]

    # Employee CAN read
    resp_read = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=emp_headers)
    assert resp_read.status_code == 200

    # Employee CANNOT create supplier -> 403
    resp_create = await client.post(
        "/api/v1/suppliers",
        json={"name": "Emp Supplier"},
        headers=emp_headers,
    )
    assert resp_create.status_code == 403

    # Employee CANNOT update supplier -> 403
    resp_update = await client.put(
        f"/api/v1/suppliers/{supplier_id}",
        json={"name": "Updated by Emp"},
        headers=emp_headers,
    )
    assert resp_update.status_code == 403

    # Employee CANNOT deactivate supplier -> 403
    resp_del = await client.delete(f"/api/v1/suppliers/{supplier_id}", headers=emp_headers)
    assert resp_del.status_code == 403


@pytest.mark.asyncio
async def test_supplier_multi_tenancy_isolation(client: AsyncClient, create_token):
    bus1_id = uuid.uuid4()
    bus2_id = uuid.uuid4()

    token1 = create_token(user_id=uuid.uuid4(), memberships={str(bus1_id): "OWNER"})
    token2 = create_token(user_id=uuid.uuid4(), memberships={str(bus2_id): "OWNER"})

    # Bus 1 creates a supplier
    resp = await client.post(
        "/api/v1/suppliers",
        json={"name": "Bus1 Supplier"},
        headers={"Authorization": f"Bearer {token1}", "X-Business-ID": str(bus1_id)},
    )
    assert resp.status_code == 201
    supplier_id = resp.json()["data"]["supplier_id"]

    # Bus 2 gets 404 on GET
    resp_get = await client.get(
        f"/api/v1/suppliers/{supplier_id}",
        headers={"Authorization": f"Bearer {token2}", "X-Business-ID": str(bus2_id)},
    )
    assert resp_get.status_code == 404

    # Bus 2 gets 404 on PUT
    resp_put = await client.put(
        f"/api/v1/suppliers/{supplier_id}",
        json={"name": "Cross Tenant Edit"},
        headers={"Authorization": f"Bearer {token2}", "X-Business-ID": str(bus2_id)},
    )
    assert resp_put.status_code == 404

    # Bus 2 gets 404 on DELETE
    resp_del = await client.delete(
        f"/api/v1/suppliers/{supplier_id}",
        headers={"Authorization": f"Bearer {token2}", "X-Business-ID": str(bus2_id)},
    )
    assert resp_del.status_code == 404


@pytest.mark.asyncio
async def test_supplier_soft_delete(client: AsyncClient, create_token, db_session):
    bus_id = uuid.uuid4()
    token = create_token(user_id=uuid.uuid4(), memberships={str(bus_id): "OWNER"})
    headers = {"Authorization": f"Bearer {token}", "X-Business-ID": str(bus_id)}

    # Create supplier
    resp = await client.post(
        "/api/v1/suppliers",
        json={"name": "To Deactivate Supplier"},
        headers=headers,
    )
    assert resp.status_code == 201
    supplier_id = uuid.UUID(resp.json()["data"]["supplier_id"])

    # Deactivate
    resp_del = await client.delete(f"/api/v1/suppliers/{supplier_id}", headers=headers)
    assert resp_del.status_code == 200
    assert resp_del.json()["data"]["is_active"] is False

    # Verify supplier still exists in DB
    stmt = select(Supplier).where(Supplier.supplier_id == supplier_id)
    res = await db_session.execute(stmt)
    supplier_row = res.scalar_one_or_none()
    assert supplier_row is not None
    assert supplier_row.is_active is False
