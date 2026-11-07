"""Unit tests for the AuthService business logic."""

import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.auth_service import AuthService
from app.core.errors import (
    ConflictException,
    InvalidCredentialsException,
    AccountLockedException,
    TokenReuseException,
    BadRequestException,
    UnauthorizedException,
)


@pytest.mark.asyncio
async def test_register_creates_user_and_tokens(db_session: AsyncSession):
    svc = AuthService(db_session, request_id="test-req-1")
    user, at, rt = await svc.register(
        full_name="Test User",
        email="testuser@example.com",
        password="SecurePassword123!",
    )
    assert user["email"] == "testuser@example.com"
    assert at  # access token
    assert rt  # refresh token


@pytest.mark.asyncio
async def test_register_duplicate_email_raises_conflict(db_session: AsyncSession):
    svc = AuthService(db_session, request_id="test-req-2")
    await svc.register(full_name="User A", email="dup@example.com", password="SecurePassword123!")
    with pytest.raises(ConflictException):
        await svc.register(full_name="User B", email="dup@example.com", password="AnotherPassword123!")


@pytest.mark.asyncio
async def test_register_normalizes_email(db_session: AsyncSession):
    svc = AuthService(db_session)
    user, _, _ = await svc.register(
        full_name="Mixed Case", email="Mixed@EXAMPLE.COM", password="SecurePassword123!"
    )
    assert user["email"] == "Mixed@EXAMPLE.COM"  # original preserved
    # Duplicate with same normalized form should raise
    with pytest.raises(ConflictException):
        await svc.register(full_name="X", email="mixed@example.com", password="SecurePassword123!")


@pytest.mark.asyncio
async def test_login_success(db_session: AsyncSession):
    svc = AuthService(db_session)
    await svc.register(full_name="Login User", email="login@example.com", password="ValidPass123!")
    user, at, rt = await svc.login(email="login@example.com", password="ValidPass123!")
    assert user["email"] == "login@example.com"
    assert at and rt


@pytest.mark.asyncio
async def test_login_wrong_password_raises(db_session: AsyncSession):
    svc = AuthService(db_session)
    await svc.register(full_name="PW User", email="pw@example.com", password="CorrectPass123!")
    with pytest.raises(InvalidCredentialsException):
        await svc.login(email="pw@example.com", password="WrongPass123!")


@pytest.mark.asyncio
async def test_login_unknown_email_raises_same_error(db_session: AsyncSession):
    svc = AuthService(db_session)
    with pytest.raises(InvalidCredentialsException):
        await svc.login(email="ghost@example.com", password="SomePass123!")


@pytest.mark.asyncio
async def test_login_lockout_after_5_failures(db_session: AsyncSession):
    svc = AuthService(db_session)
    await svc.register(full_name="Lock Me", email="lockme@example.com", password="CorrectPass123!")
    for _ in range(5):
        try:
            await svc.login(email="lockme@example.com", password="Wrong!")
        except (InvalidCredentialsException, AccountLockedException):
            pass
    with pytest.raises(AccountLockedException):
        await svc.login(email="lockme@example.com", password="CorrectPass123!")


@pytest.mark.asyncio
async def test_refresh_token_rotation(db_session: AsyncSession):
    svc = AuthService(db_session)
    _, at, rt = await svc.register(full_name="Rotate", email="rotate@example.com", password="RotatePass123!")
    new_at, new_rt = await svc.refresh(refresh_token_raw=rt)
    assert new_at and new_rt
    assert new_rt != rt


@pytest.mark.asyncio
async def test_refresh_token_reuse_raises_token_reuse(db_session: AsyncSession):
    svc = AuthService(db_session)
    _, _, rt = await svc.register(full_name="Reuse", email="reuse@example.com", password="ReusePass123!")
    await svc.refresh(refresh_token_raw=rt)  # first use – OK
    with pytest.raises(TokenReuseException):
        await svc.refresh(refresh_token_raw=rt)  # reuse – should fail and revoke family


@pytest.mark.asyncio
async def test_logout_invalidates_refresh_token(db_session: AsyncSession):
    svc = AuthService(db_session)
    _, _, rt = await svc.register(full_name="Logout", email="logout@example.com", password="LogoutPass123!")
    await svc.logout(refresh_token_raw=rt)
    # After logout, refreshing should fail
    with pytest.raises(UnauthorizedException):
        await svc.refresh(refresh_token_raw=rt)


@pytest.mark.asyncio
async def test_logout_all_invalidates_all_tokens(db_session: AsyncSession):
    svc = AuthService(db_session)
    user, _, rt1 = await svc.register(full_name="LogAll", email="logall@example.com", password="LogAllPass123!")
    _, rt2 = await svc.refresh(refresh_token_raw=rt1)
    uid = uuid.UUID(user["user_id"])
    await svc.logout_all(user_id=uid)
    with pytest.raises(UnauthorizedException):
        await svc.refresh(refresh_token_raw=rt2)


@pytest.mark.asyncio
async def test_email_verification_bad_token_raises(db_session: AsyncSession):
    svc = AuthService(db_session)
    with pytest.raises(BadRequestException):
        await svc.verify_email(token_raw="nonexistent-token-abc")


@pytest.mark.asyncio
async def test_password_reset_bad_token_raises(db_session: AsyncSession):
    svc = AuthService(db_session)
    with pytest.raises(BadRequestException):
        await svc.confirm_password_reset(token_raw="bad-token", new_password="NewPassword123!")


@pytest.mark.asyncio
async def test_get_me_returns_user(db_session: AsyncSession):
    svc = AuthService(db_session)
    user, _, _ = await svc.register(full_name="Me User", email="me@example.com", password="MePass123456!")
    fetched = await svc.get_me(user_id=uuid.UUID(user["user_id"]))
    assert fetched["email"] == "me@example.com"


@pytest.mark.asyncio
async def test_update_me_changes_name(db_session: AsyncSession):
    svc = AuthService(db_session)
    user, _, _ = await svc.register(full_name="Old Name", email="update@example.com", password="UpdatePass123!")
    updated = await svc.update_me(user_id=uuid.UUID(user["user_id"]), full_name="New Name")
    assert updated["full_name"] == "New Name"
