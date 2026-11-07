"""Integration / API tests for the auth endpoints.

These tests use the in-memory SQLite-backed test client (conftest.py)
so they run without a real PostgreSQL instance.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

REGISTER_PAYLOAD = {
    "full_name": "Alice Example",
    "email": "alice@example.com",
    "password": "SuperSecret123!",
}


async def register_and_login(client: AsyncClient) -> dict:
    """Register a user and return the full login response body."""
    r = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
    assert r.status_code == 201, r.text
    return r.json()


# ─────────────────────────────────────────────────────────
# Register
# ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_success(client: AsyncClient):
    r = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
    assert r.status_code == 201
    body = r.json()
    assert body["success"] is True
    data = body["data"]
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "Bearer"
    assert data["user"]["email"] == REGISTER_PAYLOAD["email"]
    assert "request_id" in body


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient):
    await register_and_login(client)
    r = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
    assert r.status_code == 409
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "EMAIL_CONFLICT"


@pytest.mark.asyncio
async def test_register_short_password_rejected(client: AsyncClient):
    payload = {**REGISTER_PAYLOAD, "email": "b@example.com", "password": "short"}
    r = await client.post("/api/v1/auth/register", json=payload)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_register_missing_name_rejected(client: AsyncClient):
    payload = {**REGISTER_PAYLOAD, "full_name": ""}
    r = await client.post("/api/v1/auth/register", json=payload)
    assert r.status_code in (400, 422)


# ─────────────────────────────────────────────────────────
# Login
# ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_success(client: AsyncClient):
    await register_and_login(client)
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert "access_token" in data
    assert "refresh_token" in data


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    await register_and_login(client)
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": "WrongPassword999!"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_login_unknown_email_same_response(client: AsyncClient):
    """Unknown email and wrong password return the same 401 to prevent user enumeration."""
    r_unknown = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@nowhere.com", "password": "SomePassword123!"},
    )
    await register_and_login(client)
    r_wrong_pw = await client.post(
        "/api/v1/auth/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": "WrongPassword999!"},
    )
    assert r_unknown.status_code == r_wrong_pw.status_code == 401
    assert r_unknown.json()["error"]["code"] == r_wrong_pw.json()["error"]["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_login_lockout_after_5_failures(client: AsyncClient):
    await register_and_login(client)
    email = REGISTER_PAYLOAD["email"]
    for _ in range(5):
        await client.post("/api/v1/auth/login", json={"email": email, "password": "wrong1!"})
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": REGISTER_PAYLOAD["password"]},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] in ("ACCOUNT_LOCKED", "INVALID_CREDENTIALS")


# ─────────────────────────────────────────────────────────
# Refresh token rotation
# ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_token_rotation(client: AsyncClient):
    reg = await register_and_login(client)
    old_rt = reg["data"]["refresh_token"]

    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_rt})
    assert r.status_code == 200
    data = r.json()["data"]
    new_rt = data["refresh_token"]
    assert new_rt != old_rt
    assert "access_token" in data


@pytest.mark.asyncio
async def test_refresh_token_reuse_revokes_family(client: AsyncClient):
    reg = await register_and_login(client)
    old_rt = reg["data"]["refresh_token"]

    # First use — succeeds; get new token
    r1 = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_rt})
    assert r1.status_code == 200

    # Reuse old token → entire family should be revoked
    r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_rt})
    assert r2.status_code == 401
    assert r2.json()["error"]["code"] == "TOKEN_REUSE_DETECTED"


# ─────────────────────────────────────────────────────────
# Logout
# ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_logout(client: AsyncClient):
    reg = await register_and_login(client)
    access_token = reg["data"]["access_token"]
    refresh_token = reg["data"]["refresh_token"]

    r = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refresh_token},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r.status_code == 200

    # Revoked token should no longer work
    r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert r2.status_code == 401


@pytest.mark.asyncio
async def test_logout_all(client: AsyncClient):
    reg = await register_and_login(client)
    access_token = reg["data"]["access_token"]
    refresh_token = reg["data"]["refresh_token"]

    r = await client.post(
        "/api/v1/auth/logout-all",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r.status_code == 200

    r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert r2.status_code == 401


# ─────────────────────────────────────────────────────────
# /users/me
# ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_me(client: AsyncClient):
    reg = await register_and_login(client)
    access_token = reg["data"]["access_token"]
    r = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {access_token}"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["email"] == REGISTER_PAYLOAD["email"]


@pytest.mark.asyncio
async def test_get_me_no_token(client: AsyncClient):
    r = await client.get("/api/v1/users/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_update_me(client: AsyncClient):
    reg = await register_and_login(client)
    access_token = reg["data"]["access_token"]
    r = await client.put(
        "/api/v1/users/me",
        json={"full_name": "Alice Updated"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["full_name"] == "Alice Updated"


# ─────────────────────────────────────────────────────────
# Email verification & password reset (happy path)
# ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_email_bad_token(client: AsyncClient):
    r = await client.post("/api/v1/auth/verify-email", json={"token": "bad-token"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_password_reset_request_does_not_leak_existence(client: AsyncClient):
    # For non-existent email – same 200
    r1 = await client.post(
        "/api/v1/auth/password-reset/request", json={"email": "ghost@example.com"}
    )
    assert r1.status_code == 200

    # For registered email – same 200
    await register_and_login(client)
    r2 = await client.post(
        "/api/v1/auth/password-reset/request", json={"email": REGISTER_PAYLOAD["email"]}
    )
    assert r2.status_code == 200
    assert r1.json()["data"]["message"] == r2.json()["data"]["message"]


@pytest.mark.asyncio
async def test_password_reset_bad_token(client: AsyncClient):
    r = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "badtoken", "new_password": "NewPassword123!"},
    )
    assert r.status_code == 400
