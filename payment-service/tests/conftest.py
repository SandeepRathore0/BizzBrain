import os
import sys
import uuid
import time
from typing import AsyncGenerator
from decimal import Decimal
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

# Ensure payment-service root is in sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Force test environment variables
os.environ["ENVIRONMENT"] = "test"
os.environ["PAYPAL_ENV"] = "sandbox"
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://payment_app:app_runtime_secure_pass_123@localhost:5432/payment_test_db",
)
os.environ["MIGRATION_DATABASE_URL"] = os.getenv(
    "TEST_MIGRATION_DATABASE_URL",
    "postgresql+asyncpg://payment_migrator:migrator_secure_pass_123@localhost:5432/payment_test_db",
)

from app.clients.invoice_client import InvoicePayableDetails, ResolvedPayToken, get_invoice_client
from app.clients.paypal_client import FakePayPalClient, set_paypal_client
from app.core.auth import set_test_jwks
from app.core.config import get_settings
from app.core.database import get_db, set_sessionmaker
from app.core.middleware import PublicPayRateLimiterMiddleware
from app.main import app
from fake_paypal.main import app as fake_paypal_app
from tests.stubs.invoice_service_stub import app as fake_invoice_app
from tests.test_keys import get_test_jwks, create_test_jwt

# Set test JWKS right away
set_test_jwks(get_test_jwks())

# Test Database Engine using payment_app runtime role with NullPool
test_engine: AsyncEngine = create_async_engine(
    os.environ["DATABASE_URL"],
    echo=False,
    future=True,
    poolclass=NullPool,
)

TestingSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# Set global sessionmaker to TestingSessionLocal for tests
set_sessionmaker(TestingSessionLocal)

# Migrator engine for administrative test operations
migrator_engine: AsyncEngine = create_async_engine(
    os.environ["MIGRATION_DATABASE_URL"],
    echo=False,
    future=True,
    poolclass=NullPool,
)


@pytest.fixture(autouse=True)
def setup_test_environment(monkeypatch):
    set_test_jwks(get_test_jwks())
    PublicPayRateLimiterMiddleware.reset()

    # Fake PayPal
    fake_pp = FakePayPalClient(fake_paypal_app)
    set_paypal_client(fake_pp)

    # Fake Invoice Client
    inv_transport = ASGITransport(app=fake_invoice_app)
    inv_client = get_invoice_client()

    async def mock_get_payable_details(business_id: uuid.UUID, invoice_id: uuid.UUID, request_id=None):
        async with AsyncClient(transport=inv_transport, base_url="http://fake-invoice") as client:
            resp = await client.get(
                f"/internal/v1/invoices/{invoice_id}/payable-details",
                params={"business_id": str(business_id)},
            )
            if resp.status_code == 200:
                data = resp.json()["data"]
                return InvoicePayableDetails(
                    invoice_id=uuid.UUID(data["invoice_id"]),
                    business_id=uuid.UUID(data["business_id"]),
                    invoice_number=data.get("invoice_number"),
                    business_name=data.get("business_name", "Merchant"),
                    payable=data["payable"],
                    currency=data["currency"],
                    outstanding_balance=Decimal(str(data["outstanding_balance"])),
                    min_installment_amount=(
                        Decimal(str(data["min_installment_amount"]))
                        if data.get("min_installment_amount") is not None
                        else None
                    ),
                )
            elif resp.status_code == 404:
                from app.core.errors import NotFoundException
                raise NotFoundException(code="INVOICE_NOT_FOUND", message="Invoice not found")
            else:
                from app.core.errors import ServiceUnavailableException
                raise ServiceUnavailableException(code="INVOICE_SERVICE_UNAVAILABLE", message="Invoice error")

    async def mock_resolve_pay_token(token: str, request_id=None):
        async with AsyncClient(transport=inv_transport, base_url="http://fake-invoice") as client:
            resp = await client.post("/internal/v1/invoices/resolve-pay-token", json={"token": token})
            if resp.status_code == 200:
                data = resp.json()["data"]
                return ResolvedPayToken(
                    invoice_id=uuid.UUID(data["invoice_id"]),
                    business_id=uuid.UUID(data["business_id"]),
                )
            else:
                from app.core.errors import NotFoundException
                raise NotFoundException(code="INVALID_PAY_TOKEN", message="Invalid or expired pay token")

    monkeypatch.setattr(inv_client, "get_payable_details", mock_get_payable_details)
    monkeypatch.setattr(inv_client, "resolve_pay_token", mock_resolve_pay_token)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestingSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def migrator_session() -> AsyncGenerator[AsyncSession, None]:
    async with migrator_engine.connect() as conn:
        yield conn


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db():
        async with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
