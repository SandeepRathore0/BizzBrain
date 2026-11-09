from datetime import date, timedelta
from decimal import Decimal
import uuid
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch
from app.clients.user_service_client import BusinessDetails, user_service_client


@pytest.fixture(autouse=True)
def mock_user_service():
    async def fake_get_business(business_id: uuid.UUID, request_id: str):
        return BusinessDetails(
            business_id=business_id,
            base_currency="USD",
            timezone="UTC",
            is_active=True,
        )

    with patch.object(user_service_client, "get_business_details", new=AsyncMock(side_effect=fake_get_business)):
        yield


@pytest.mark.asyncio
async def test_expense_summary_aggregation_and_void_exclusion(client: AsyncClient, create_token):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {"Authorization": f"Bearer {token}", "X-Business-ID": str(business_id)}

    # 1. Create two categories
    resp_cat1 = await client.post("/api/v1/expense-categories", json={"category_name": "Travel Expenses"}, headers=headers)
    cat1_id = resp_cat1.json()["data"]["category_id"]

    resp_cat2 = await client.post("/api/v1/expense-categories", json={"category_name": "Meals"}, headers=headers)
    cat2_id = resp_cat2.json()["data"]["category_id"]

    # 2. Record expenses
    # Exp 1: Travel - $100.00 in May 2026
    await client.post(
        "/api/v1/expenses",
        json={"description": "Flight", "amount": "100.00", "currency": "USD", "expense_date": "2026-05-10", "category_id": cat1_id},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    # Exp 2: Travel - $50.00 in May 2026
    await client.post(
        "/api/v1/expenses",
        json={"description": "Train", "amount": "50.00", "currency": "USD", "expense_date": "2026-05-15", "category_id": cat1_id},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    # Exp 3: Meals - $30.00 in June 2026
    await client.post(
        "/api/v1/expenses",
        json={"description": "Dinner", "amount": "30.00", "currency": "USD", "expense_date": "2026-06-01", "category_id": cat2_id},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    # Exp 4: Travel - $200.00 to VOID
    resp_void_exp = await client.post(
        "/api/v1/expenses",
        json={"description": "Hotel cancelled", "amount": "200.00", "currency": "USD", "expense_date": "2026-05-20", "category_id": cat1_id},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    void_exp_id = resp_void_exp.json()["data"]["expense_id"]

    with patch.object(user_service_client, "check_live_authz", new=AsyncMock(return_value=True)):
        await client.post(
            f"/api/v1/expenses/{void_exp_id}/void",
            json={"void_reason": "Refunded reservation"},
            headers=headers,
        )

    # 3. Query financial summary for 2026-05-01 to 2026-06-30
    resp_summary = await client.get("/api/v1/financial-summary/expenses?from=2026-05-01&to=2026-06-30", headers=headers)
    assert resp_summary.status_code == 200
    data = resp_summary.json()["data"]

    # Overall total should be $100 + $50 + $30 = $180.00 (VOID $200.00 excluded!)
    assert data["total_amount"] == "180.00"
    assert data["total_count"] == 3
    assert data["currency"] == "USD"

    # Category breakdown
    cat_totals = {c["category_name"]: c["total_amount"] for c in data["by_category"]}
    assert cat_totals["Travel Expenses"] == "150.00"
    assert cat_totals["Meals"] == "30.00"

    # Monthly breakdown
    month_totals = {m["month"]: m["total_amount"] for m in data["by_month"]}
    assert month_totals["2026-05"] == "150.00"
    assert month_totals["2026-06"] == "30.00"

    # 4. Also check top-level overview endpoint
    resp_overview = await client.get("/api/v1/financial-summary?from=2026-05-01&to=2026-06-30", headers=headers)
    assert resp_overview.status_code == 200
    overview_data = resp_overview.json()["data"]
    assert overview_data["total_expenses"] == "180.00"
    assert overview_data["expense_count"] == 3


@pytest.mark.asyncio
async def test_summary_date_validation_rules(client: AsyncClient, create_token):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {"Authorization": f"Bearer {token}", "X-Business-ID": str(business_id)}

    # 1. from > to -> 422 INVALID_DATE_RANGE
    resp_inverted = await client.get("/api/v1/financial-summary/expenses?from=2026-06-01&to=2026-05-01", headers=headers)
    assert resp_inverted.status_code == 422
    assert resp_inverted.json()["error"]["code"] == "INVALID_DATE_RANGE"

    # 2. Period > 366 days -> 422 PERIOD_EXCEEDS_MAXIMUM
    resp_too_long = await client.get("/api/v1/financial-summary/expenses?from=2024-01-01&to=2025-01-03", headers=headers)
    assert resp_too_long.status_code == 422
    assert resp_too_long.json()["error"]["code"] == "PERIOD_EXCEEDS_MAXIMUM"


@pytest.mark.asyncio
async def test_summary_multi_tenancy_isolation(client: AsyncClient, create_token):
    bus1 = uuid.uuid4()
    bus2 = uuid.uuid4()
    token1 = create_token(user_id=uuid.uuid4(), memberships={str(bus1): "OWNER"})
    token2 = create_token(user_id=uuid.uuid4(), memberships={str(bus2): "OWNER"})

    headers1 = {"Authorization": f"Bearer {token1}", "X-Business-ID": str(bus1)}
    headers2 = {"Authorization": f"Bearer {token2}", "X-Business-ID": str(bus2)}

    # Business 1 records expense
    await client.post(
        "/api/v1/expenses",
        json={"description": "Bus1 only", "amount": "99.00", "currency": "USD", "expense_date": date.today().isoformat()},
        headers={**headers1, "Idempotency-Key": str(uuid.uuid4())},
    )

    # Business 2 queries summary for today
    today_str = date.today().isoformat()
    resp_bus2 = await client.get(f"/api/v1/financial-summary/expenses?from={today_str}&to={today_str}", headers=headers2)
    assert resp_bus2.status_code == 200
    data = resp_bus2.json()["data"]
    assert data["total_amount"] == "0.00"
    assert data["total_count"] == 0
    assert len(data["by_category"]) == 0
