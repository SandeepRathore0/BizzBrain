"""Authentication & token service – pure business logic, no HTTP concerns."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import (
    AccountInactiveException,
    AccountLockedException,
    BadRequestException,
    ConflictException,
    InvalidCredentialsException,
    NotFoundException,
    TokenReuseException,
)
from app.core.security import (
    create_access_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    normalize_email,
    verify_password,
    verify_password_dummy,
)
from app.models.token import EmailToken, RefreshToken
from app.models.user import User
from app.repositories.user_repo import UserRepository


def _build_memberships(user: User) -> Dict[str, str]:
    """Build memberships dict (business_id → role) from active memberships only."""
    return {
        str(m.business_id): m.role
        for m in (user.memberships or [])
        if m.is_active
    }


def _user_out(user: User) -> dict:
    return {
        "user_id": str(user.user_id),
        "full_name": user.full_name,
        "email": user.email,
        "is_active": user.is_active,
        "email_verified_at": user.email_verified_at.isoformat() if user.email_verified_at else None,
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
    }


class AuthService:
    def __init__(self, session: AsyncSession, request_id: Optional[str] = None) -> None:
        self._repo = UserRepository(session)
        self._request_id = request_id
        self._settings = get_settings()

    # ------------------------------------------------------------------
    # Register
    # ------------------------------------------------------------------

    async def register(
        self, *, full_name: str, email: str, password: str
    ) -> Tuple[dict, str, str]:
        """
        Returns (user_dict, access_token, refresh_token_raw).
        Raises ConflictException if email already taken.
        """
        email_normalized = normalize_email(email)
        existing = await self._repo.get_by_email_normalized(email_normalized)
        if existing:
            raise ConflictException("Email address is already registered.", code="EMAIL_CONFLICT")

        pw_hash = hash_password(password)
        user = await self._repo.create(
            full_name=full_name,
            email=email,
            email_normalized=email_normalized,
            password_hash=pw_hash,
        )

        # Build tokens (empty memberships on first login)
        access_token = create_access_token(str(user.user_id), memberships={})
        raw_rt, rt_expires = await self._issue_refresh_token(user, user_agent=None, ip_address=None)

        await self._repo.write_audit(
            actor_type="USER",
            actor_id=str(user.user_id),
            action="USER_REGISTERED",
            entity_type="user",
            entity_id=user.user_id,
            after={"email": email_normalized},
            request_id=self._request_id,
        )
        return _user_out(user), access_token, raw_rt

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def login(
        self,
        *,
        email: str,
        password: str,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> Tuple[dict, str, str]:
        email_normalized = normalize_email(email)
        user = await self._repo.get_by_email_normalized(email_normalized)

        if user is None:
            # Constant-time dummy compare to avoid timing oracle
            verify_password_dummy(password)
            raise InvalidCredentialsException()

        now = datetime.now(timezone.utc)
        if user.is_locked(now):
            raise AccountLockedException()

        if not user.is_active:
            raise AccountInactiveException()

        if not verify_password(password, user.password_hash):
            await self._repo.increment_failed_login(
                user,
                self._settings.MAX_FAILED_LOGIN_ATTEMPTS,
                self._settings.ACCOUNT_LOCKOUT_MINUTES,
            )
            await self._repo.write_audit(
                actor_type="ANONYMOUS",
                actor_id=email_normalized,
                action="LOGIN_FAILURE",
                entity_type="user",
                entity_id=user.user_id,
                request_id=self._request_id,
            )
            await self._repo._s.commit()
            raise InvalidCredentialsException()

        # Successful login
        await self._repo.reset_failed_login(user)

        # Must eagerly load memberships for token building
        memberships = await self._repo.get_active_memberships(user.user_id)
        access_token = create_access_token(str(user.user_id), memberships=memberships)
        raw_rt, _ = await self._issue_refresh_token(user, user_agent=user_agent, ip_address=ip_address)

        await self._repo.write_audit(
            actor_type="USER",
            actor_id=str(user.user_id),
            action="LOGIN_SUCCESS",
            entity_type="user",
            entity_id=user.user_id,
            request_id=self._request_id,
        )
        return _user_out(user), access_token, raw_rt

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    async def refresh(
        self,
        *,
        refresh_token_raw: str,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Returns (new_access_token, new_refresh_token_raw)."""
        token_hash = hash_token(refresh_token_raw)
        old_rt = await self._repo.get_refresh_token_by_hash_for_update(token_hash)

        if old_rt is None:
            raise InvalidCredentialsException("Invalid refresh token.")

        now = datetime.now(timezone.utc)

        # Reuse detection: token already rotated or revoked
        if old_rt.revoked_at is not None:
            # Revoke entire family
            await self._repo.revoke_family(old_rt.family_id)
            await self._repo.write_audit(
                actor_type="SYSTEM",
                actor_id=str(old_rt.user_id),
                action="REFRESH_TOKEN_REUSE_DETECTED",
                entity_type="refresh_token",
                entity_id=old_rt.token_id,
                request_id=self._request_id,
            )
            await self._repo._s.commit()
            raise TokenReuseException()

        if old_rt.expires_at.replace(tzinfo=timezone.utc) < now:
            raise InvalidCredentialsException("Refresh token expired.")

        user = await self._repo.get_by_id(old_rt.user_id)
        if user is None or not user.is_active:
            raise AccountInactiveException()

        # Rotate
        new_raw = generate_opaque_token(32)
        new_hash = hash_token(new_raw)
        expires_at = now + timedelta(days=self._settings.REFRESH_TOKEN_EXPIRE_DAYS)
        await self._repo.rotate_refresh_token(
            old_rt, new_hash, user_agent=user_agent, ip_address=ip_address, expires_at=expires_at
        )

        memberships = await self._repo.get_active_memberships(user.user_id)
        new_access = create_access_token(str(user.user_id), memberships=memberships)

        await self._repo.write_audit(
            actor_type="USER",
            actor_id=str(user.user_id),
            action="TOKEN_REFRESHED",
            entity_type="refresh_token",
            entity_id=old_rt.token_id,
            request_id=self._request_id,
        )
        return new_access, new_raw

    # ------------------------------------------------------------------
    # Logout
    # ------------------------------------------------------------------

    async def logout(self, *, refresh_token_raw: str) -> None:
        token_hash = hash_token(refresh_token_raw)
        rt = await self._repo.get_refresh_token_by_hash(token_hash)
        if rt and rt.revoked_at is None:
            await self._repo.revoke_token(rt)

    async def logout_all(self, *, user_id: uuid.UUID) -> None:
        await self._repo.revoke_all_user_tokens(user_id)

    # ------------------------------------------------------------------
    # Verify email
    # ------------------------------------------------------------------

    async def request_email_verification(self, *, user_id: uuid.UUID) -> str:
        """Creates and returns a raw email verification token."""
        user = await self._repo.get_by_id(user_id)
        if not user:
            raise NotFoundException("User not found.")
        raw = generate_opaque_token(32)
        token_hash = hash_token(raw)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=self._settings.EMAIL_TOKEN_EXPIRE_HOURS)
        await self._repo.create_email_token(
            user_id=user_id,
            token_hash=token_hash,
            purpose="EMAIL_VERIFICATION",
            expires_at=expires_at,
        )
        return raw

    async def verify_email(self, *, token_raw: str) -> None:
        token_hash = hash_token(token_raw)
        et = await self._repo.get_email_token_by_hash(token_hash)
        now = datetime.now(timezone.utc)
        if et is None or et.purpose != "EMAIL_VERIFICATION":
            raise BadRequestException("Invalid email verification token.")
        if et.used_at is not None:
            raise BadRequestException("Email verification token already used.")
        if et.expires_at.replace(tzinfo=timezone.utc) < now:
            raise BadRequestException("Email verification token has expired.")

        await self._repo.consume_email_token(et)
        user = await self._repo.get_by_id(et.user_id)
        if user:
            await self._repo.mark_email_verified(user)

    # ------------------------------------------------------------------
    # Password reset
    # ------------------------------------------------------------------

    async def request_password_reset(self, *, email: str) -> None:
        """Always returns without revealing whether the user exists."""
        email_normalized = normalize_email(email)
        user = await self._repo.get_by_email_normalized(email_normalized)
        if not user:
            return  # Silent – do not leak user existence
        raw = generate_opaque_token(32)
        token_hash = hash_token(raw)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=self._settings.EMAIL_TOKEN_EXPIRE_HOURS)
        await self._repo.create_email_token(
            user_id=user.user_id,
            token_hash=token_hash,
            purpose="PASSWORD_RESET",
            expires_at=expires_at,
        )
        # In production, this raw token would be sent via email.
        # Logging the token itself would be a security violation – do not log it.

    async def confirm_password_reset(self, *, token_raw: str, new_password: str) -> None:
        token_hash = hash_token(token_raw)
        et = await self._repo.get_email_token_by_hash(token_hash)
        now = datetime.now(timezone.utc)
        if et is None or et.purpose != "PASSWORD_RESET":
            raise BadRequestException("Invalid password reset token.")
        if et.used_at is not None:
            raise BadRequestException("Password reset token already used.")
        if et.expires_at.replace(tzinfo=timezone.utc) < now:
            raise BadRequestException("Password reset token has expired.")

        await self._repo.consume_email_token(et)
        user = await self._repo.get_by_id(et.user_id)
        if not user:
            raise NotFoundException("User not found.")
        pw_hash = hash_password(new_password)
        await self._repo.set_password(user, pw_hash)
        # Revoke all sessions on password change
        await self._repo.revoke_all_user_tokens(user.user_id)

    # ------------------------------------------------------------------
    # Me endpoints
    # ------------------------------------------------------------------

    async def get_me(self, *, user_id: uuid.UUID) -> dict:
        user = await self._repo.get_by_id(user_id)
        if not user:
            raise NotFoundException("User not found.")
        return _user_out(user)

    async def update_me(self, *, user_id: uuid.UUID, full_name: Optional[str]) -> dict:
        user = await self._repo.get_by_id(user_id)
        if not user:
            raise NotFoundException("User not found.")
        if full_name:
            user = await self._repo.update_name(user, full_name.strip())
        return _user_out(user)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _issue_refresh_token(
        self,
        user: User,
        user_agent: Optional[str],
        ip_address: Optional[str],
    ) -> Tuple[str, datetime]:
        raw = generate_opaque_token(32)
        token_hash = hash_token(raw)
        family_id = uuid.uuid4()
        expires_at = datetime.now(timezone.utc) + timedelta(days=self._settings.REFRESH_TOKEN_EXPIRE_DAYS)
        await self._repo.create_refresh_token(
            user_id=user.user_id,
            family_id=family_id,
            token_hash=token_hash,
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=expires_at,
        )
        return raw, expires_at
