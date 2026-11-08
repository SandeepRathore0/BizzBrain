import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from app.models.models import AuditLog, ExpenseCategory


@pytest.mark.asyncio
async def test_create_category_success(client: AsyncClient, create_token, db_session):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
    }

    resp = await client.post(
        "/api/v1/expense-categories",
        json={"category_name": "Office Supplies"},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["category_name"] == "Office Supplies"
    assert data["is_active"] is True
    assert data["business_id"] == str(business_id)

    # Audit log
    audit_stmt = select(AuditLog).where(
        AuditLog.entity_id == uuid.UUID(data["category_id"]),
        AuditLog.action == "CATEGORY_CREATED",
    )
    audit_result = await db_session.execute(audit_stmt)
    audit_entry = audit_result.scalar_one_or_none()
    assert audit_entry is not None
    assert audit_entry.actor_id == str(user_id)
    assert audit_entry.after["category_name"] == "Office Supplies"


@pytest.mark.asyncio
async def test_create_category_duplicate_name_conflict(client: AsyncClient, create_token):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
    }

    resp1 = await client.post(
        "/api/v1/expense-categories",
        json={"category_name": "Marketing"},
        headers=headers,
    )
    assert resp1.status_code == 201

    # Case-insensitive duplicate
    resp2 = await client.post(
        "/api/v1/expense-categories",
        json={"category_name": "marketing"},
        headers=headers,
    )
    assert resp2.status_code == 409
    body = resp2.json()
    assert body["success"] is False
    assert body["error"]["code"] == "DUPLICATE_CATEGORY"


@pytest.mark.asyncio
async def test_list_categories_lazy_default_seeding(client: AsyncClient, create_token, db_session):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
    }

    # First GET: should lazy-seed default categories
    resp = await client.get("/api/v1/expense-categories", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    items = body["data"]
    assert len(items) == 7  # 7 default categories

    expected_names = {
        "Marketing", "Office supplies", "Rent",
        "Salaries", "Software subscriptions", "Travel", "Utilities",
    }
    actual_names = {item["category_name"] for item in items}
    assert actual_names == expected_names

    # Second GET: should still be 7, no double-seeding
    resp2 = await client.get("/api/v1/expense-categories", headers=headers)
    items2 = resp2.json()["data"]
    assert len(items2) == 7


@pytest.mark.asyncio
async def test_patch_rename_category(client: AsyncClient, create_token, db_session):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "ADMIN"})
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
    }

    # Create
    resp = await client.post(
        "/api/v1/expense-categories",
        json={"category_name": "Old Name"},
        headers=headers,
    )
    assert resp.status_code == 201
    cat_id = resp.json()["data"]["category_id"]

    # Rename
    resp_patch = await client.patch(
        f"/api/v1/expense-categories/{cat_id}",
        json={"category_name": "New Name"},
        headers=headers,
    )
    assert resp_patch.status_code == 200
    assert resp_patch.json()["data"]["category_name"] == "New Name"

    # Audit log
    audit_stmt = select(AuditLog).where(
        AuditLog.entity_id == uuid.UUID(cat_id),
        AuditLog.action == "CATEGORY_UPDATED",
    )
    audit_result = await db_session.execute(audit_stmt)
    audit_entry = audit_result.scalar_one_or_none()
    assert audit_entry is not None
    assert audit_entry.before["category_name"] == "Old Name"
    assert audit_entry.after["category_name"] == "New Name"


@pytest.mark.asyncio
async def test_patch_deactivate_category(client: AsyncClient, create_token):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
    }

    # Create
    resp = await client.post(
        "/api/v1/expense-categories",
        json={"category_name": "To Deactivate"},
        headers=headers,
    )
    cat_id = resp.json()["data"]["category_id"]

    # Deactivate
    resp_patch = await client.patch(
        f"/api/v1/expense-categories/{cat_id}",
        json={"is_active": False},
        headers=headers,
    )
    assert resp_patch.status_code == 200
    assert resp_patch.json()["data"]["is_active"] is False


@pytest.mark.asyncio
async def test_employee_cannot_create_or_update_category(client: AsyncClient, create_token):
    emp_id = uuid.uuid4()
    bus_id = uuid.uuid4()
    emp_token = create_token(user_id=emp_id, memberships={str(bus_id): "EMPLOYEE"})
    emp_headers = {"Authorization": f"Bearer {emp_token}", "X-Business-ID": str(bus_id)}

    # Employee CANNOT create category (no categories.write)
    resp_create = await client.post(
        "/api/v1/expense-categories",
        json={"category_name": "By Employee"},
        headers=emp_headers,
    )
    assert resp_create.status_code == 403

    # Employee CAN list categories (has categories.read)
    resp_list = await client.get("/api/v1/expense-categories", headers=emp_headers)
    assert resp_list.status_code == 200


@pytest.mark.asyncio
async def test_category_multi_tenancy_isolation(client: AsyncClient, create_token):
    user1 = uuid.uuid4()
    bus1 = uuid.uuid4()
    token1 = create_token(user_id=user1, memberships={str(bus1): "OWNER"})
    headers1 = {"Authorization": f"Bearer {token1}", "X-Business-ID": str(bus1)}

    user2 = uuid.uuid4()
    bus2 = uuid.uuid4()
    token2 = create_token(user_id=user2, memberships={str(bus2): "OWNER"})
    headers2 = {"Authorization": f"Bearer {token2}", "X-Business-ID": str(bus2)}

    # Business 1 creates a category
    resp = await client.post(
        "/api/v1/expense-categories",
        json={"category_name": "Bus1 Only"},
        headers=headers1,
    )
    assert resp.status_code == 201
    cat_id = resp.json()["data"]["category_id"]

    # Business 2 should NOT see it in their list
    resp_list = await client.get("/api/v1/expense-categories", headers=headers2)
    bus2_names = {item["category_name"] for item in resp_list.json()["data"]}
    assert "Bus1 Only" not in bus2_names

    # Business 2 tries to PATCH it → 404
    resp_patch = await client.patch(
        f"/api/v1/expense-categories/{cat_id}",
        json={"category_name": "Hacked"},
        headers=headers2,
    )
    assert resp_patch.status_code == 404
