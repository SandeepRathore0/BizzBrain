from datetime import date, timedelta
from decimal import Decimal
import uuid
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from app.clients.user_service_client import BusinessDetails, user_service_client
from app.models.models import AuditLog, Expense


@pytest.fixture(autouse=True)
def mock_user_service():
    async def fake_get_business(business_id: uuid.UUID, request_id: str):
        return BusinessDetails(
            business_id=business_id,
            base_currency="USD",
            timezone="UTC",
            is_active=True,
        )

    async def fake_live_authz(user_id: uuid.UUID, business_id: uuid.UUID, permission: str, request_id: str):
        return True

    with patch.object(user_service_client, "get_business_details", new=AsyncMock(side_effect=fake_get_business)), \
         patch.object(user_service_client, "check_live_authz", new=AsyncMock(side_effect=fake_live_authz)):
        yield


@pytest.mark.asyncio
async def test_record_expense_success_and_audit(client: AsyncClient, create_token, db_session):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": str(uuid.uuid4()),
    }

    payload = {
        "description": "Team lunch",
        "amount": "45.50",
        "currency": "USD",
        "expense_date": date.today().isoformat(),
    }

    resp = await client.post("/api/v1/expenses", json=payload, headers=headers)
    assert resp.status_code == 201
    assert resp.headers.get("Idempotent-Replayed") == "false"

    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["description"] == "Team lunch"
    assert data["amount"] == "45.50"
    assert data["currency"] == "USD"
    assert data["status"] == "POSTED"
    assert data["business_id"] == str(business_id)

    # Check audit log in DB
    audit_stmt = select(AuditLog).where(
        AuditLog.entity_id == uuid.UUID(data["expense_id"]),
        AuditLog.action == "EXPENSE_RECORDED",
    )
    audit_result = await db_session.execute(audit_stmt)
    audit_entry = audit_result.scalar_one_or_none()
    assert audit_entry is not None
    assert audit_entry.actor_id == str(user_id)
    assert audit_entry.after["amount"] == "45.5000" or audit_entry.after["amount"] == "45.50"


@pytest.mark.asyncio
async def test_record_expense_idempotency_replay_and_conflict(client: AsyncClient, create_token):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})
    idempotency_key = str(uuid.uuid4())
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": idempotency_key,
    }

    payload = {
        "description": "Server hosting",
        "amount": "120.00",
        "currency": "USD",
        "expense_date": date.today().isoformat(),
    }

    # 1. Initial request -> 201
    resp1 = await client.post("/api/v1/expenses", json=payload, headers=headers)
    assert resp1.status_code == 201
    assert resp1.headers.get("Idempotent-Replayed") == "false"
    exp_id_1 = resp1.json()["data"]["expense_id"]

    # 2. Replay with identical key & payload -> 200 with Idempotent-Replayed: true
    resp2 = await client.post("/api/v1/expenses", json=payload, headers=headers)
    assert resp2.status_code == 200
    assert resp2.headers.get("Idempotent-Replayed") == "true"
    exp_id_2 = resp2.json()["data"]["expense_id"]
    assert exp_id_1 == exp_id_2

    # 3. Same key with different payload -> 422 IDEMPOTENCY_KEY_REUSED
    payload_diff = {
        "description": "Different payload",
        "amount": "150.00",
        "currency": "USD",
        "expense_date": date.today().isoformat(),
    }
    resp3 = await client.post("/api/v1/expenses", json=payload_diff, headers=headers)
    assert resp3.status_code == 422
    assert resp3.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.asyncio
async def test_record_expense_validation_errors(client: AsyncClient, create_token):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
    }

    # 1. Missing Idempotency-Key -> 400
    resp_no_key = await client.post(
        "/api/v1/expenses",
        json={"description": "Test", "amount": "10.00", "currency": "USD", "expense_date": date.today().isoformat()},
        headers=headers,
    )
    assert resp_no_key.status_code == 400
    assert resp_no_key.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    # Add key for further tests
    headers["Idempotency-Key"] = str(uuid.uuid4())

    # 2. Float amount rejected -> 422
    resp_float = await client.post(
        "/api/v1/expenses",
        json={"description": "Test", "amount": 10.55, "currency": "USD", "expense_date": date.today().isoformat()},
        headers=headers,
    )
    assert resp_float.status_code == 422

    # 3. Excess decimal precision (> 2 decimals for USD) -> 422
    headers["Idempotency-Key"] = str(uuid.uuid4())
    resp_prec = await client.post(
        "/api/v1/expenses",
        json={"description": "Test", "amount": "10.555", "currency": "USD", "expense_date": date.today().isoformat()},
        headers=headers,
    )
    assert resp_prec.status_code == 422
    assert resp_prec.json()["error"]["code"] == "INVALID_PRECISION"

    # 4. Currency mismatch -> 422
    headers["Idempotency-Key"] = str(uuid.uuid4())
    resp_curr = await client.post(
        "/api/v1/expenses",
        json={"description": "Test", "amount": "10.00", "currency": "EUR", "expense_date": date.today().isoformat()},
        headers=headers,
    )
    assert resp_curr.status_code == 422
    assert resp_curr.json()["error"]["code"] == "CURRENCY_MISMATCH"

    # 5. Future date > 1 day -> 422
    headers["Idempotency-Key"] = str(uuid.uuid4())
    future_date = (date.today() + timedelta(days=5)).isoformat()
    resp_future = await client.post(
        "/api/v1/expenses",
        json={"description": "Test", "amount": "10.00", "currency": "USD", "expense_date": future_date},
        headers=headers,
    )
    assert resp_future.status_code == 422
    assert resp_future.json()["error"]["code"] == "INVALID_DATE"

    # 6. Invalid non-existent supplier or category -> 422
    headers["Idempotency-Key"] = str(uuid.uuid4())
    resp_bad_supp = await client.post(
        "/api/v1/expenses",
        json={
            "description": "Test",
            "amount": "10.00",
            "currency": "USD",
            "expense_date": date.today().isoformat(),
            "supplier_id": str(uuid.uuid4()),
        },
        headers=headers,
    )
    assert resp_bad_supp.status_code == 422
    assert resp_bad_supp.json()["error"]["code"] == "INVALID_SUPPLIER"


@pytest.mark.asyncio
async def test_patch_expense_allowed_and_forbidden_fields(client: AsyncClient, create_token):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
    }

    # First record an expense
    headers["Idempotency-Key"] = str(uuid.uuid4())
    resp_create = await client.post(
        "/api/v1/expenses",
        json={"description": "Original", "amount": "100.00", "currency": "USD", "expense_date": date.today().isoformat()},
        headers=headers,
    )
    assert resp_create.status_code == 201
    exp_id = resp_create.json()["data"]["expense_id"]

    # Attempt to PATCH forbidden field (amount) -> 422 FIELD_NOT_EDITABLE
    resp_patch_bad = await client.patch(
        f"/api/v1/expenses/{exp_id}",
        json={"amount": "200.00"},
        headers=headers,
    )
    assert resp_patch_bad.status_code == 422
    assert resp_patch_bad.json()["error"]["code"] == "FIELD_NOT_EDITABLE"

    # Attempt to PATCH forbidden field (currency) -> 422
    resp_patch_curr = await client.patch(
        f"/api/v1/expenses/{exp_id}",
        json={"currency": "USD"},
        headers=headers,
    )
    assert resp_patch_curr.status_code == 422
    assert resp_patch_curr.json()["error"]["code"] == "FIELD_NOT_EDITABLE"

    # Successfully PATCH description
    resp_patch_ok = await client.patch(
        f"/api/v1/expenses/{exp_id}",
        json={"description": "Updated Description"},
        headers=headers,
    )
    assert resp_patch_ok.status_code == 200
    assert resp_patch_ok.json()["data"]["description"] == "Updated Description"


@pytest.mark.asyncio
async def test_void_expense_flow_and_audit(client: AsyncClient, create_token, db_session):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Business-ID": str(business_id),
        "Idempotency-Key": str(uuid.uuid4()),
    }

    # Record expense
    resp = await client.post(
        "/api/v1/expenses",
        json={"description": "To Void", "amount": "50.00", "currency": "USD", "expense_date": date.today().isoformat()},
        headers=headers,
    )
    exp_id = resp.json()["data"]["expense_id"]

    # Void expense
    resp_void = await client.post(
        f"/api/v1/expenses/{exp_id}/void",
        json={"void_reason": "Duplicate transaction entered in error"},
        headers=headers,
    )
    assert resp_void.status_code == 200
    data = resp_void.json()["data"]
    assert data["status"] == "VOID"
    assert data["void_reason"] == "Duplicate transaction entered in error"
    assert data["voided_by"] == str(user_id)
    assert data["voided_at"] is not None

    # Verify Audit log
    audit_stmt = select(AuditLog).where(
        AuditLog.entity_id == uuid.UUID(exp_id),
        AuditLog.action == "EXPENSE_VOIDED",
    )
    audit_result = await db_session.execute(audit_stmt)
    audit_entry = audit_result.scalar_one_or_none()
    assert audit_entry is not None
    assert audit_entry.before["status"] == "POSTED"
    assert audit_entry.after["status"] == "VOID"

    # Second void -> 409 Conflict
    resp_void_again = await client.post(
        f"/api/v1/expenses/{exp_id}/void",
        json={"void_reason": "Voiding again"},
        headers=headers,
    )
    assert resp_void_again.status_code == 409
    assert resp_void_again.json()["error"]["code"] == "ALREADY_VOID"

    # Patching a voided expense -> 409 Conflict
    resp_patch_void = await client.patch(
        f"/api/v1/expenses/{exp_id}",
        json={"description": "Try editing voided"},
        headers=headers,
    )
    assert resp_patch_void.status_code == 409
    assert resp_patch_void.json()["error"]["code"] == "CANNOT_EDIT_VOID"


@pytest.mark.asyncio
async def test_employee_visibility_and_permissions(client: AsyncClient, create_token):
    bus_id = uuid.uuid4()
    emp1_id = uuid.uuid4()
    emp2_id = uuid.uuid4()

    token1 = create_token(user_id=emp1_id, memberships={str(bus_id): "EMPLOYEE"})
    token2 = create_token(user_id=emp2_id, memberships={str(bus_id): "EMPLOYEE"})

    headers1 = {"Authorization": f"Bearer {token1}", "X-Business-ID": str(bus_id), "Idempotency-Key": str(uuid.uuid4())}
    headers2 = {"Authorization": f"Bearer {token2}", "X-Business-ID": str(bus_id), "Idempotency-Key": str(uuid.uuid4())}

    # Employee 1 records expense
    resp1 = await client.post(
        "/api/v1/expenses",
        json={"description": "Emp1 expense", "amount": "25.00", "currency": "USD", "expense_date": date.today().isoformat()},
        headers=headers1,
    )
    assert resp1.status_code == 201
    exp1_id = resp1.json()["data"]["expense_id"]

    # Employee 2 cannot see Employee 1's expense -> 404 (prevent info disclosure)
    resp_get_emp2 = await client.get(f"/api/v1/expenses/{exp1_id}", headers=headers2)
    assert resp_get_emp2.status_code == 404

    # Employee 2 listing expenses sees 0 items
    resp_list_emp2 = await client.get("/api/v1/expenses", headers=headers2)
    assert resp_list_emp2.status_code == 200
    assert len(resp_list_emp2.json()["data"]["items"]) == 0

    # Employee 1 cannot void expense -> 403 Forbidden
    resp_void = await client.post(
        f"/api/v1/expenses/{exp1_id}/void",
        json={"void_reason": "Emp tried to void"},
        headers=headers1,
    )
    assert resp_void.status_code == 403


@pytest.mark.asyncio
async def test_expense_multi_tenancy_isolation(client: AsyncClient, create_token):
    bus1_id = uuid.uuid4()
    bus2_id = uuid.uuid4()
    user1 = uuid.uuid4()
    user2 = uuid.uuid4()

    token1 = create_token(user_id=user1, memberships={str(bus1_id): "OWNER"})
    token2 = create_token(user_id=user2, memberships={str(bus2_id): "OWNER"})

    headers1 = {"Authorization": f"Bearer {token1}", "X-Business-ID": str(bus1_id), "Idempotency-Key": str(uuid.uuid4())}
    headers2 = {"Authorization": f"Bearer {token2}", "X-Business-ID": str(bus2_id), "Idempotency-Key": str(uuid.uuid4())}

    # Bus1 records expense
    resp1 = await client.post(
        "/api/v1/expenses",
        json={"description": "Bus1 expense", "amount": "99.00", "currency": "USD", "expense_date": date.today().isoformat()},
        headers=headers1,
    )
    assert resp1.status_code == 201
    exp_id = resp1.json()["data"]["expense_id"]

    # Bus2 attempts to GET -> 404
    resp_get = await client.get(f"/api/v1/expenses/{exp_id}", headers=headers2)
    assert resp_get.status_code == 404

    # Bus2 attempts to PATCH -> 404
    resp_patch = await client.patch(f"/api/v1/expenses/{exp_id}", json={"description": "Hacked"}, headers=headers2)
    assert resp_patch.status_code == 404

    # Bus2 attempts to VOID -> 404
    resp_void = await client.post(f"/api/v1/expenses/{exp_id}/void", json={"void_reason": "Attack"}, headers=headers2)
    assert resp_void.status_code == 404
