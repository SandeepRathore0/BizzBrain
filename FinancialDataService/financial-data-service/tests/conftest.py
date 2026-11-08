import asyncio
import time
import uuid
from typing import AsyncGenerator
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, patch

from app.core.database import Base, get_db_session
from app.core.auth import jwks_cache
from app.main import app as fastapi_app
import app.models  # Register all models


TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def rsa_key_pair():
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    public_key = private_key.public_key()
    return private_key, public_key


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    async_session = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with async_session() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(db_session, rsa_key_pair) -> AsyncGenerator[AsyncClient, None]:
    _, public_key = rsa_key_pair

    mock_pyjwk = type("JWK", (), {"key": public_key})()

    async def override_get_db():
        yield db_session

    fastapi_app.dependency_overrides[get_db_session] = override_get_db

    with patch.object(jwks_cache, "get_signing_key", new=AsyncMock(return_value=mock_pyjwk)):
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def create_token(rsa_key_pair):
    private_key, _ = rsa_key_pair

    def _create(user_id: uuid.UUID, memberships: dict[str, str], exp_delta: int = 600) -> str:
        payload = {
            "sub": str(user_id),
            "memberships": memberships,
            "exp": int(time.time()) + exp_delta,
            "iat": int(time.time()),
            "iss": "http://localhost:8001",
            "aud": "bizbrain",
        }
        return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-kid"})

    return _create


@pytest.fixture
def create_service_token(rsa_key_pair):
    private_key, _ = rsa_key_pair

    def _create(caller_service: str, audience: str = "financial-data-service", exp_delta: int = 600) -> str:
        payload = {
            "sub": caller_service,
            "iss": "http://localhost:8001",
            "aud": audience,
            "exp": int(time.time()) + exp_delta,
            "iat": int(time.time()),
        }
        return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-kid"})

    return _create
