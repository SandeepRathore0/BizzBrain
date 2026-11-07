"""Business + membership database access layer."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Role
from app.models.audit import AuditLog
from app.models.business import Business
from app.models.member import BusinessMember


class BusinessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ------------------------------------------------------------------
    # Businesses
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        business_name: str,
        base_currency: str,
        timezone: str,
    ) -> Business:
        biz = Business(
            business_id=uuid.uuid4(),
            business_name=business_name,
            base_currency=base_currency,
            timezone=timezone,
        )
        self._s.add(biz)
        await self._s.flush()
        await self._s.refresh(biz)
        return biz

    async def get_by_id(self, business_id: uuid.UUID) -> Optional[Business]:
        result = await self._s.execute(
            select(Business).where(Business.business_id == business_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> Tuple[List[Business], Optional[str]]:
        """Return businesses where the user has an active membership, cursor-paginated."""
        after_ts: Optional[datetime] = None
        after_id: Optional[uuid.UUID] = None
        if cursor:
            try:
                decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
                if "|" in decoded:
                    ts_str, id_str = decoded.split("|", 1)
                    after_ts = datetime.fromisoformat(ts_str)
                    after_id = uuid.UUID(id_str)
                else:
                    after_ts = datetime.fromisoformat(decoded)
            except Exception:
                after_ts = None
                after_id = None

        query = (
            select(Business)
            .join(BusinessMember, BusinessMember.business_id == Business.business_id)
            .where(
                BusinessMember.user_id == user_id,
                BusinessMember.is_active.is_(True),
                Business.is_active.is_(True),
            )
            .order_by(Business.created_at, Business.business_id)
        )
        if after_ts and after_id:
            query = query.where(
                (Business.created_at > after_ts) |
                ((Business.created_at == after_ts) & (Business.business_id > after_id))
            )
        elif after_ts:
            query = query.where(Business.created_at > after_ts)

        query = query.limit(limit + 1)

        result = await self._s.execute(query)
        rows = list(result.scalars().all())
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = None
        if has_more:
            token_str = f"{items[-1].created_at.isoformat()}|{items[-1].business_id}"
            next_cursor = base64.urlsafe_b64encode(token_str.encode()).decode()
        return items, next_cursor

    async def update(
        self,
        biz: Business,
        *,
        business_name: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> Business:
        if business_name is not None:
            biz.business_name = business_name
        if timezone is not None:
            biz.timezone = timezone
        self._s.add(biz)
        await self._s.flush()
        await self._s.refresh(biz)
        return biz

    async def soft_delete(self, biz: Business) -> None:
        biz.is_active = False
        self._s.add(biz)
        await self._s.flush()

    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------

    async def add_member(
        self,
        *,
        business_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str,
    ) -> BusinessMember:
        m = BusinessMember(
            membership_id=uuid.uuid4(),
            business_id=business_id,
            user_id=user_id,
            role=role,
        )
        self._s.add(m)
        await self._s.flush()
        await self._s.refresh(m)
        return m

    async def get_member(
        self,
        business_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Optional[BusinessMember]:
        result = await self._s.execute(
            select(BusinessMember).where(
                BusinessMember.business_id == business_id,
                BusinessMember.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_member_by_id(self, membership_id: uuid.UUID) -> Optional[BusinessMember]:
        result = await self._s.execute(
            select(BusinessMember).where(BusinessMember.membership_id == membership_id)
        )
        return result.scalar_one_or_none()

    async def list_members(
        self,
        business_id: uuid.UUID,
        *,
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> Tuple[List[BusinessMember], Optional[str]]:
        after_ts: Optional[datetime] = None
        after_id: Optional[uuid.UUID] = None
        if cursor:
            try:
                decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
                if "|" in decoded:
                    ts_str, id_str = decoded.split("|", 1)
                    after_ts = datetime.fromisoformat(ts_str)
                    after_id = uuid.UUID(id_str)
                else:
                    after_ts = datetime.fromisoformat(decoded)
            except Exception:
                after_ts = None
                after_id = None

        query = (
            select(BusinessMember)
            .where(BusinessMember.business_id == business_id)
            .order_by(BusinessMember.created_at, BusinessMember.membership_id)
        )
        if after_ts and after_id:
            query = query.where(
                (BusinessMember.created_at > after_ts) |
                ((BusinessMember.created_at == after_ts) & (BusinessMember.membership_id > after_id))
            )
        elif after_ts:
            query = query.where(BusinessMember.created_at > after_ts)

        query = query.limit(limit + 1)

        result = await self._s.execute(query)
        rows = list(result.scalars().all())
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = None
        if has_more:
            token_str = f"{items[-1].created_at.isoformat()}|{items[-1].membership_id}"
            next_cursor = base64.urlsafe_b64encode(token_str.encode()).decode()
        return items, next_cursor

    async def update_member(
        self,
        member: BusinessMember,
        *,
        role: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> BusinessMember:
        if role is not None:
            member.role = role
        if is_active is not None:
            member.is_active = is_active
        self._s.add(member)
        await self._s.flush()
        await self._s.refresh(member)
        return member

    async def count_active_owners(self, business_id: uuid.UUID) -> int:
        result = await self._s.execute(
            select(func.count())
            .select_from(BusinessMember)
            .where(
                BusinessMember.business_id == business_id,
                BusinessMember.role == Role.OWNER.value,
                BusinessMember.is_active.is_(True),
            )
        )
        return result.scalar_one()

    async def lock_active_owners_for_update(self, business_id: uuid.UUID) -> List[BusinessMember]:
        """Select all active owner rows with FOR UPDATE to guard last-owner invariant."""
        result = await self._s.execute(
            select(BusinessMember)
            .where(
                BusinessMember.business_id == business_id,
                BusinessMember.role == Role.OWNER.value,
                BusinessMember.is_active.is_(True),
            )
            .with_for_update()
        )
        return list(result.scalars().all())

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
