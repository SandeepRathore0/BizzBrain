import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "financial-data-service"


@pytest.mark.asyncio
async def test_ready_check(client: AsyncClient):
    resp = await client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["service"] == "financial-data-service"
    assert data["checks"]["database"] == "ok"
