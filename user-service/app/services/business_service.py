"""Business management service – tenancy, CRUD, members, last-owner invariant."""

from __future__ import annotations

import uuid
from typing import Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import (
    AccountInactiveException,
    BadRequestException,
    BusinessInactiveException,
    ConflictException,
    ForbiddenException,
    LastOwnerException,
    NotFoundException,
)
from app.core.permissions import Role, can_manage_member, has_permission, Permission
from app.repositories.business_repo import BusinessRepository
from app.repositories.user_repo import UserRepository


def _biz_out(biz) -> dict:
    return {
        "business_id": str(biz.business_id),
        "business_name": biz.business_name,
        "base_currency": biz.base_currency.strip(),
        "timezone": biz.timezone,
        "is_active": biz.is_active,
        "created_at": biz.created_at.isoformat(),
    }


def _member_out(m) -> dict:
    return {
        "membership_id": str(m.membership_id),
        "business_id": str(m.business_id),
        "user_id": str(m.user_id),
        "role": m.role,
        "is_active": m.is_active,
        "created_at": m.created_at.isoformat(),
    }


class BusinessService:
    def __init__(self, session: AsyncSession, request_id: Optional[str] = None) -> None:
        self._repo = BusinessRepository(session)
        self._user_repo = UserRepository(session)
        self._settings = get_settings()
        self._request_id = request_id

    # ------------------------------------------------------------------
    # Business CRUD
    # ------------------------------------------------------------------

    async def create_business(
        self,
        *,
        actor_user_id: uuid.UUID,
        actor_role_in_token: Optional[str],
        business_name: str,
        base_currency: str,
        timezone: str,
    ) -> dict:
        # Employees need flag
        if actor_role_in_token == Role.EMPLOYEE.value and not self._settings.ALLOW_EMPLOYEE_BUSINESS_CREATION:
            raise ForbiddenException("Employees are not allowed to create businesses.")

        # Validate currency and timezone
        settings = self._settings
        if not settings.is_valid_currency(base_currency.upper()):
            raise BadRequestException(f"'{base_currency}' is not a valid ISO 4217 currency code.")
        if not settings.is_valid_timezone(timezone):
            raise BadRequestException(f"'{timezone}' is not a valid IANA timezone.")

        # Check actor user is active
        actor = await self._user_repo.get_by_id(actor_user_id)
        if not actor or not actor.is_active:
            raise AccountInactiveException()

        biz = await self._repo.create(
            business_name=business_name,
            base_currency=base_currency.upper(),
            timezone=timezone,
        )
        # Creator becomes OWNER
        member = await self._repo.add_member(
            business_id=biz.business_id,
            user_id=actor_user_id,
            role=Role.OWNER.value,
        )
        await self._repo.write_audit(
            actor_type="USER",
            actor_id=str(actor_user_id),
            action="BUSINESS_CREATED",
            entity_type="business",
            entity_id=biz.business_id,
            after={"business_name": business_name, "base_currency": base_currency.upper()},
            request_id=self._request_id,
        )
        return _biz_out(biz)

    async def get_business(
        self, *, business_id: uuid.UUID, actor_user_id: uuid.UUID, actor_memberships: Dict[str, str]
    ) -> dict:
        self._require_membership(business_id, actor_memberships)
        biz = await self._repo.get_by_id(business_id)
        if not biz:
            raise NotFoundException("Business not found.")
        return _biz_out(biz)

    async def list_businesses(
        self, *, actor_user_id: uuid.UUID, limit: int = 25, cursor: Optional[str] = None
    ) -> Tuple[List[dict], Optional[str]]:
        items, next_cursor = await self._repo.list_for_user(actor_user_id, limit=limit, cursor=cursor)
        return [_biz_out(b) for b in items], next_cursor

    async def update_business(
        self,
        *,
        business_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_memberships: Dict[str, str],
        business_name: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> dict:
        actor_role = self._require_membership(business_id, actor_memberships)
        if not has_permission(actor_role, Permission.BUSINESS_UPDATE.value):
            raise ForbiddenException("You do not have permission to update this business.")

        biz = await self._repo.get_by_id(business_id)
        if not biz:
            raise NotFoundException("Business not found.")
        if not biz.is_active:
            raise BusinessInactiveException()

        if timezone is not None and not self._settings.is_valid_timezone(timezone):
            raise BadRequestException(f"'{timezone}' is not a valid IANA timezone.")

        biz = await self._repo.update(biz, business_name=business_name, timezone=timezone)
        await self._repo.write_audit(
            actor_type="USER",
            actor_id=str(actor_user_id),
            action="BUSINESS_UPDATED",
            entity_type="business",
            entity_id=biz.business_id,
            business_id=biz.business_id,
            request_id=self._request_id,
        )
        return _biz_out(biz)

    async def delete_business(
        self,
        *,
        business_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_memberships: Dict[str, str],
    ) -> None:
        actor_role = self._require_membership(business_id, actor_memberships)
        if not has_permission(actor_role, Permission.BUSINESS_DELETE.value):
            raise ForbiddenException("Only owners can delete a business.")

        biz = await self._repo.get_by_id(business_id)
        if not biz:
            raise NotFoundException("Business not found.")

        await self._repo.soft_delete(biz)
        await self._repo.write_audit(
            actor_type="USER",
            actor_id=str(actor_user_id),
            action="BUSINESS_DELETED",
            entity_type="business",
            entity_id=biz.business_id,
            business_id=biz.business_id,
            request_id=self._request_id,
        )

    # ------------------------------------------------------------------
    # Member management
    # ------------------------------------------------------------------

    async def list_members(
        self,
        *,
        business_id: uuid.UUID,
        actor_memberships: Dict[str, str],
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> Tuple[List[dict], Optional[str]]:
        self._require_membership(business_id, actor_memberships)
        items, next_cursor = await self._repo.list_members(business_id, limit=limit, cursor=cursor)
        return [_member_out(m) for m in items], next_cursor

    async def add_member(
        self,
        *,
        business_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_memberships: Dict[str, str],
        target_user_id: uuid.UUID,
        role: str,
    ) -> dict:
        actor_role = self._require_membership(business_id, actor_memberships)
        if not has_permission(actor_role, Permission.MEMBERS_MANAGE.value):
            raise ForbiddenException("You do not have permission to manage members.")
        if not can_manage_member(actor_role, role, new_role=role):
            raise ForbiddenException("You cannot assign this role.")
        if role not in (Role.OWNER.value, Role.ADMIN.value, Role.EMPLOYEE.value):
            raise BadRequestException(f"Invalid role '{role}'.")

        biz = await self._repo.get_by_id(business_id)
        if not biz or not biz.is_active:
            raise BusinessInactiveException()

        # Check target user exists
        target = await self._user_repo.get_by_id(target_user_id)
        if not target or not target.is_active:
            raise NotFoundException("Target user not found or inactive.")

        # Conflict: already a member
        existing = await self._repo.get_member(business_id, target_user_id)
        if existing:
            raise ConflictException("User is already a member of this business.")

        member = await self._repo.add_member(
            business_id=business_id, user_id=target_user_id, role=role
        )
        await self._repo.write_audit(
            actor_type="USER",
            actor_id=str(actor_user_id),
            action="MEMBER_ADDED",
            entity_type="business_member",
            entity_id=member.membership_id,
            business_id=business_id,
            after={"user_id": str(target_user_id), "role": role},
            request_id=self._request_id,
        )
        return _member_out(member)

    async def update_member(
        self,
        *,
        business_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_memberships: Dict[str, str],
        target_user_id: uuid.UUID,
        new_role: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> dict:
        actor_role = self._require_membership(business_id, actor_memberships)
        if not has_permission(actor_role, Permission.MEMBERS_MANAGE.value):
            raise ForbiddenException("You do not have permission to manage members.")

        member = await self._repo.get_member(business_id, target_user_id)
        if not member:
            raise NotFoundException("Membership not found.")

        # Hierarchy check
        if not can_manage_member(actor_role, member.role, new_role=new_role):
            raise ForbiddenException("You cannot modify this member.")

        # Last-owner invariant: if we are demoting/deactivating an owner, ensure >=1 remains
        will_lose_owner_role = (
            member.role == Role.OWNER.value and
            (new_role is not None and new_role != Role.OWNER.value or is_active is False)
        )
        if will_lose_owner_role:
            owners = await self._repo.lock_active_owners_for_update(business_id)
            if len(owners) <= 1:
                raise LastOwnerException()

        before = {"role": member.role, "is_active": member.is_active}
        member = await self._repo.update_member(member, role=new_role, is_active=is_active)
        after = {"role": member.role, "is_active": member.is_active}

        await self._repo.write_audit(
            actor_type="USER",
            actor_id=str(actor_user_id),
            action="MEMBER_UPDATED",
            entity_type="business_member",
            entity_id=member.membership_id,
            business_id=business_id,
            before=before,
            after=after,
            request_id=self._request_id,
        )
        return _member_out(member)

    # ------------------------------------------------------------------
    # Internal inter-service helpers
    # ------------------------------------------------------------------

    async def internal_get_business(self, business_id: uuid.UUID) -> dict:
        biz = await self._repo.get_by_id(business_id)
        if not biz:
            raise NotFoundException("Business not found.")
        return _biz_out(biz)

    async def internal_check_authz(
        self, *, business_id: uuid.UUID, user_id: uuid.UUID, permission: str
    ) -> tuple[bool, Optional[str]]:
        member = await self._repo.get_member(business_id, user_id)
        if member and member.is_active:
            role = member.role
            return has_permission(role, permission), role
        return False, None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _require_membership(
        self, business_id: uuid.UUID, actor_memberships: Dict[str, str]
    ) -> str:
        """Return the actor's role, or raise 403 if not a member."""
        role = actor_memberships.get(str(business_id))
        if role is None:
            raise ForbiddenException("You are not a member of this business.")
        return role
