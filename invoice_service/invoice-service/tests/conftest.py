import os
import subprocess
import time
from typing import AsyncGenerator
import pytest
import psycopg
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

# Test Postgres settings (port 5433)
TEST_PORT = 5433
PG_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".pgdata"))
PG_BIN_DIR = r"C:\Program Files\PostgreSQL\18\bin"

TEST_OWNER_SYNC_URL = f"postgresql+psycopg://invoice_owner:invoice_owner_pass@127.0.0.1:{TEST_PORT}/invoice_db"
TEST_APP_SYNC_URL = f"postgresql+psycopg://invoice_app:invoice_app_pass@127.0.0.1:{TEST_PORT}/invoice_db"
TEST_APP_ASYNC_URL = f"postgresql+asyncpg://invoice_app:invoice_app_pass@127.0.0.1:{TEST_PORT}/invoice_db"

# Set environment variables early before other app imports
os.environ["ENVIRONMENT"] = "test"
os.environ["MIGRATION_DATABASE_URL"] = TEST_OWNER_SYNC_URL
os.environ["DATABASE_URL"] = TEST_APP_ASYNC_URL


def is_postgres_ready() -> bool:
    is_ready_exe = os.path.join(PG_BIN_DIR, "pg_isready.exe")
    res = subprocess.run([is_ready_exe, "-h", "127.0.0.1", "-p", str(TEST_PORT)], capture_output=True)
    return res.returncode == 0


def ensure_postgres_running():
    if not is_postgres_ready():
        # Start pg_ctl
        pg_ctl_exe = os.path.join(PG_BIN_DIR, "pg_ctl.exe")
        subprocess.run([pg_ctl_exe, "-D", PG_DATA_DIR, "-o", f"-p {TEST_PORT}", "start"], check=True)
        # Wait up to 5s for readiness
        for _ in range(10):
            if is_postgres_ready():
                break
            time.sleep(0.5)


def init_test_database_and_roles():
    """Ensure database invoice_db and roles invoice_owner and invoice_app exist."""
    conn = psycopg.connect(f"postgresql://postgres@127.0.0.1:{TEST_PORT}/postgres", autocommit=True)
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = 'invoice_db'")
        if not cur.fetchone():
            cur.execute("CREATE DATABASE invoice_db")

    init_sql_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "docker", "initdb", "01-init-roles.sql")
    )
    with open(init_sql_path, "r", encoding="utf-8") as f:
        sql = f.read()

    conn_db = psycopg.connect(f"postgresql://postgres@127.0.0.1:{TEST_PORT}/invoice_db", autocommit=True)
    with conn_db.cursor() as cur:
        cur.execute(sql)
    conn_db.close()
    conn.close()


@pytest.fixture(scope="session", autouse=True)
def setup_test_postgres():
    """Session-level setup to ensure test postgres is up, roles initialized, and app engine reconfigured."""
    ensure_postgres_running()
    init_test_database_and_roles()

    # Re-initialize app database engine with test url
    import app.core.database as db_mod
    import app.core.config as cfg_mod

    cfg_mod.settings.ENVIRONMENT = "test"
    cfg_mod.settings.DATABASE_URL = TEST_APP_ASYNC_URL
    cfg_mod.settings.MIGRATION_DATABASE_URL = TEST_OWNER_SYNC_URL

    from sqlalchemy.pool import NullPool

    db_mod.engine = create_async_engine(
        TEST_APP_ASYNC_URL,
        echo=False,
        poolclass=NullPool,
    )
    db_mod.async_session_factory = async_sessionmaker(
        bind=db_mod.engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest.fixture
def owner_engine():
    engine = create_engine(TEST_OWNER_SYNC_URL, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def app_sync_engine():
    engine = create_engine(TEST_APP_SYNC_URL, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
async def app_async_session() -> AsyncGenerator[AsyncSession, None]:
    from sqlalchemy.pool import NullPool
    engine = create_async_engine(TEST_APP_ASYNC_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()
