import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_internal_customer_contract_c1_success(client: AsyncClient, create_token, create_service_token):
    business_id = uuid.uuid4()
    owner_token = create_token(user_id=uuid.uuid4(), memberships={str(business_id): "OWNER"})

    # 1. Create a customer using public endpoint
    resp_create = await client.post(
        "/api/v1/customers",
        json={
            "name": "Invoice Client Inc",
            "email": "billing@client.com",
            "phone": "+1987654321",
            "billing_address": {"city": "Boston", "country": "USA"},
            "tax_id": "TAX-999",
        },
        headers={"Authorization": f"Bearer {owner_token}", "X-Business-ID": str(business_id)},
    )
    assert resp_create.status_code == 201
    cust_id = resp_create.json()["data"]["customer_id"]

    # 2. Invoice Service calls internal customer endpoint with service token
    service_token = create_service_token(caller_service="invoice-service", audience="financial-data-service")
    resp_internal = await client.get(
        f"/internal/v1/customers/{cust_id}?business_id={business_id}",
        headers={"Authorization": f"Bearer {service_token}"},
    )
    assert resp_internal.status_code == 200
    body = resp_internal.json()
    assert body["success"] is True
    data = body["data"]
    assert data["customer_id"] == cust_id
    assert data["name"] == "Invoice Client Inc"
    assert data["email"] == "billing@client.com"
    assert data["tax_id"] == "TAX-999"
    assert data["is_active"] is True
    assert data["business_id"] == str(business_id)


@pytest.mark.asyncio
async def test_get_internal_customer_wrong_audience_rejected(client: AsyncClient, create_service_token):
    # Service token intended for another service (e.g. "payment-service")
    token_wrong_aud = create_service_token(caller_service="invoice-service", audience="payment-service")
    resp = await client.get(
        f"/internal/v1/customers/{uuid.uuid4()}?business_id={uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token_wrong_aud}"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_get_internal_customer_unauthorized_caller_forbidden(client: AsyncClient, create_service_token):
    # Valid signature and audience, but caller is not in allow-list
    token_bad_caller = create_service_token(caller_service="unauthorized-service", audience="financial-data-service")
    resp = await client.get(
        f"/internal/v1/customers/{uuid.uuid4()}?business_id={uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token_bad_caller}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_get_internal_customer_user_token_rejected(client: AsyncClient, create_token):
    # User token has aud="bizbrain" instead of "financial-data-service"
    user_token = create_token(user_id=uuid.uuid4(), memberships={str(uuid.uuid4()): "OWNER"})
    resp = await client.get(
        f"/internal/v1/customers/{uuid.uuid4()}?business_id={uuid.uuid4()}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_internal_customer_missing_auth_header_rejected(client: AsyncClient):
    resp = await client.get(f"/internal/v1/customers/{uuid.uuid4()}?business_id={uuid.uuid4()}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_internal_customer_not_found_and_isolation(client: AsyncClient, create_token, create_service_token):
    bus1 = uuid.uuid4()
    bus2 = uuid.uuid4()
    owner_token = create_token(user_id=uuid.uuid4(), memberships={str(bus1): "OWNER"})

    # Create customer in Bus 1
    resp_create = await client.post(
        "/api/v1/customers",
        json={"name": "Bus1 Cust"},
        headers={"Authorization": f"Bearer {owner_token}", "X-Business-ID": str(bus1)},
    )
    cust_id = resp_create.json()["data"]["customer_id"]

    service_token = create_service_token(caller_service="invoice-service", audience="financial-data-service")

    # Request with Bus 2's business_id -> 404 (isolation preserved)
    resp_foreign = await client.get(
        f"/internal/v1/customers/{cust_id}?business_id={bus2}",
        headers={"Authorization": f"Bearer {service_token}"},
    )
    assert resp_foreign.status_code == 404

    # Request non-existent customer -> 404
    resp_nonexistent = await client.get(
        f"/internal/v1/customers/{uuid.uuid4()}?business_id={bus1}",
        headers={"Authorization": f"Bearer {service_token}"},
    )
    assert resp_nonexistent.status_code == 404
