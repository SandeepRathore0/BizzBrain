"""User + token database access layer (all SQL lives here)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.member import BusinessMember
from app.models.token import EmailToken, RefreshToken
from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    async def get_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        result = await self._s.execute(select(User).where(User.user_id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email_normalized(self, email_normalized: str) -> Optional[User]:
        result = await self._s.execute(
            select(User).where(User.email_normalized == email_normalized)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        full_name: str,
        email: str,
        email_normalized: str,
        password_hash: str,
    ) -> User:
        user = User(
            user_id=uuid.uuid4(),
            full_name=full_name,
            email=email,
            email_normalized=email_normalized,
            password_hash=password_hash,
        )
        self._s.add(user)
        await self._s.flush()
        await self._s.refresh(user)
        return user

    async def update_name(self, user: User, full_name: str) -> User:
        user.full_name = full_name
        self._s.add(user)
        await self._s.flush()
        await self._s.refresh(user)
        return user

    async def mark_email_verified(self, user: User) -> None:
        user.email_verified_at = datetime.now(timezone.utc)
        self._s.add(user)
        await self._s.flush()

    async def set_password(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        self._s.add(user)
        await self._s.flush()

    async def increment_failed_login(self, user: User, max_attempts: int, lockout_minutes: int) -> None:
        user.failed_login_count += 1
        if user.failed_login_count >= max_attempts:
            user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=lockout_minutes)
        self._s.add(user)
        await self._s.flush()

    async def reset_failed_login(self, user: User) -> None:
        user.failed_login_count = 0
        user.locked_until = None
        self._s.add(user)
        await self._s.flush()

    async def get_active_memberships(self, user_id: uuid.UUID) -> Dict[str, str]:
        """Fetch active memberships mapping business_id -> role without lazy loading."""
        result = await self._s.execute(
            select(BusinessMember.business_id, BusinessMember.role).where(
                BusinessMember.user_id == user_id,
                BusinessMember.is_active.is_(True),
            )
        )
        return {str(bid): role for bid, role in result.all()}

    # ------------------------------------------------------------------
    # Refresh tokens
    # ------------------------------------------------------------------

    async def create_refresh_token(
        self,
        *,
        user_id: uuid.UUID,
        family_id: uuid.UUID,
        token_hash: str,
        user_agent: Optional[str],
        ip_address: Optional[str],
        expires_at: datetime,
    ) -> RefreshToken:
        rt = RefreshToken(
            token_id=uuid.uuid4(),
            user_id=user_id,
            family_id=family_id,
            token_hash=token_hash,
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=expires_at,
        )
        self._s.add(rt)
        await self._s.flush()
        return rt

    async def get_refresh_token_by_hash(self, token_hash: str) -> Optional[RefreshToken]:
        result = await self._s.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def get_refresh_token_by_hash_for_update(self, token_hash: str) -> Optional[RefreshToken]:
        """Lock the row for update (prevents concurrent rotation races)."""
        result = await self._s.execute(
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def rotate_refresh_token(
        self,
        old_token: RefreshToken,
        new_token_hash: str,
        user_agent: Optional[str],
        ip_address: Optional[str],
        expires_at: datetime,
    ) -> RefreshToken:
        """Revoke old token, create new token in same family."""
        old_token.revoked_at = datetime.now(timezone.utc)
        new_rt = RefreshToken(
            token_id=uuid.uuid4(),
            user_id=old_token.user_id,
            family_id=old_token.family_id,
            token_hash=new_token_hash,
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=expires_at,
        )
        old_token.replaced_by = new_rt.token_id
        self._s.add(old_token)
        self._s.add(new_rt)
        await self._s.flush()
        return new_rt

    async def revoke_family(self, family_id: uuid.UUID) -> None:
        """Revoke all tokens in a family (reuse detection)."""
        now = datetime.now(timezone.utc)
        await self._s.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    async def revoke_token(self, token: RefreshToken) -> None:
        token.revoked_at = datetime.now(timezone.utc)
        self._s.add(token)
        await self._s.flush()

    async def revoke_all_user_tokens(self, user_id: uuid.UUID) -> None:
        now = datetime.now(timezone.utc)
        await self._s.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    # ------------------------------------------------------------------
    # Email tokens
    # ------------------------------------------------------------------

    async def create_email_token(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        purpose: str,
        expires_at: datetime,
    ) -> EmailToken:
        et = EmailToken(
            token_id=uuid.uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            purpose=purpose,
            expires_at=expires_at,
        )
        self._s.add(et)
        await self._s.flush()
        return et

    async def get_email_token_by_hash(self, token_hash: str) -> Optional[EmailToken]:
        result = await self._s.execute(
            select(EmailToken).where(EmailToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def consume_email_token(self, token: EmailToken) -> None:
        token.used_at = datetime.now(timezone.utc)
        self._s.add(token)
        await self._s.flush()

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    async def write_audit(
        self,
        *,
        actor_type: str,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: Optional[uuid.UUID] = None,
        business_id: Optional[uuid.UUID] = None,
        before: Optional[dict] = None,
        after: Optional[dict] = None,
        request_id: Optional[str] = None,
    ) -> None:
        log = AuditLog(
            audit_id=uuid.uuid4(),
            business_id=business_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=before,
            after=after,
            request_id=request_id,
        )
        self._s.add(log)
        await self._s.flush()
