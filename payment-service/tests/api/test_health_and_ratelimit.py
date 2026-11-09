import pytest
from httpx import AsyncClient

from app.clients.invoice_client import get_invoice_client
from app.core.errors import NotFoundException


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["status"] == "healthy"
    assert "request_id" in data


@pytest.mark.asyncio
async def test_readiness_check(client: AsyncClient):
    resp = await client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["status"] == "ready"
    assert data["data"]["database"] == "connected"
    assert data["data"]["paypal_configured"] is True


@pytest.mark.asyncio
async def test_public_pay_rate_limiting(client: AsyncClient, monkeypatch):
    """Public endpoints are limited to 20 requests/min per IP."""
    inv_client = get_invoice_client()

    async def mock_resolve_fast(token: str, request_id=None):
        raise NotFoundException(code="INVALID_PAY_TOKEN", message="Invalid token")

    monkeypatch.setattr(inv_client, "resolve_pay_token", mock_resolve_fast)

    # 20 requests return 404 immediately, not 429
    for i in range(20):
        resp = await client.get(f"/api/v1/public/pay/mock_token_{i}/summary")
        assert resp.status_code == 404

    # 21st request must trigger 429 RateLimitExceededException
    resp = await client.get("/api/v1/public/pay/mock_token_overflow/summary")
    assert resp.status_code == 429
    data = resp.json()
    assert data["success"] is False
    assert data["error"]["code"] == "RATE_LIMIT_EXCEEDED"
