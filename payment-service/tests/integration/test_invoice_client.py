import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from app.clients.invoice_client import InvoiceClient
from app.core.errors import NotFoundException, ServiceUnavailableException
from tests.stubs.invoice_service_stub import app as stub_app


@pytest.mark.asyncio
async def test_invoice_client_circuit_breaker():
    client = InvoiceClient()
    # Trigger 5 failures
    for _ in range(5):
        client.record_failure()

    assert client.is_circuit_open() is True

    # Next call should fail immediately with circuit open
    with pytest.raises(ServiceUnavailableException) as exc:
        await client.get_payable_details(uuid.uuid4(), uuid.uuid4())
    assert exc.value.code == "INVOICE_SERVICE_UNAVAILABLE"
    assert "circuit breaker is open" in exc.value.message


@pytest.mark.asyncio
async def test_resolve_pay_token_uniform_404():
    client = InvoiceClient()

    # Intercept with ASGI transport to stub_app
    async def fast_resolve(token: str, request_id=None):
        transport = ASGITransport(app=stub_app)
        async with AsyncClient(transport=transport, base_url="http://stub-invoice") as ac:
            resp = await ac.post("/internal/v1/invoices/resolve-pay-token", json={"token": token})
            if resp.status_code != 200:
                raise NotFoundException(code="INVALID_PAY_TOKEN", message="Invalid pay token")

    with pytest.raises(NotFoundException) as exc:
        await fast_resolve("non_existent_token")
    assert exc.value.code == "INVALID_PAY_TOKEN"
