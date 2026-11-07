"""API tests for Health, JWKS discovery, and middleware envelopes."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["status"] == "healthy"
    assert data["data"]["service"] == "bizbrain-user-service"
    assert "request_id" in data
    assert response.headers["X-Request-ID"] == data["request_id"]


@pytest.mark.asyncio
async def test_health_check_with_custom_request_id(client: AsyncClient):
    custom_id = "test-custom-request-id-999"
    response = await client.get("/health", headers={"X-Request-ID": custom_id})
    assert response.status_code == 200

    data = response.json()
    assert data["request_id"] == custom_id
    assert response.headers["X-Request-ID"] == custom_id


@pytest.mark.asyncio
async def test_jwks_endpoint(client: AsyncClient):
    response = await client.get("/.well-known/jwks.json")
    assert response.status_code == 200

    data = response.json()
    assert "keys" in data
    assert len(data["keys"]) > 0

    key = data["keys"][0]
    assert key["kty"] == "RSA"
    assert key["use"] == "sig"
    assert key["alg"] == "RS256"
    assert "n" in key
    assert "e" in key
    assert "kid" in key


@pytest.mark.asyncio
async def test_not_found_error_envelope(client: AsyncClient):
    response = await client.get("/non-existent-path")
    assert response.status_code == 404

    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "NOT_FOUND"
    assert "request_id" in data
    assert response.headers["X-Request-ID"] == data["request_id"]
