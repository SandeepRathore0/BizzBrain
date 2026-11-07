"""Pytest fixtures for PostgreSQL test environment.

Connects to the PostgreSQL test database (user_test_db) using the restricted
bizbrain_app role as required by the specification.
Uses NullPool to ensure connections are not shared across asyncio event loops.
"""

import os
import sys
from typing import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Ensure the user-service root is importable
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Force test environment before importing app modules
os.environ["ENVIRONMENT"] = "test"
TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://bizbrain_app:app_runtime_secure_pass_123@localhost:5432/user_test_db"
)
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["MIGRATION_DATABASE_URL"] = os.getenv(
    "MIGRATION_DATABASE_URL",
    "postgresql+asyncpg://bizbrain_migrator:migrator_secure_pass_123@localhost:5432/user_test_db"
)

from app.core.database import get_db
from app.main import app

# PostgreSQL async engine for testing with NullPool
test_engine: AsyncEngine = create_async_engine(
    TEST_DB_URL,
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

CLEANUP_STATEMENTS = [
    text("DELETE FROM refresh_tokens"),
    text("DELETE FROM email_tokens"),
    text("DELETE FROM business_members"),
    text("DELETE FROM businesses"),
    text("DELETE FROM users"),
]


async def _clean_tables():
    async with test_engine.begin() as conn:
        for stmt in CLEANUP_STATEMENTS:
            await conn.execute(stmt)


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provides a clean database session for each test, cleaning up test tables afterwards."""
    await _clean_tables()

    async with TestingSessionLocal() as session:
        yield session

    await _clean_tables()


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Test HTTP client with the DB dependency overridden."""
    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with TestingSessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()
