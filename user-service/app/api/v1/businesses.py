"""Businesses + members router – /api/v1/businesses/*"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, resolve_business_id, require_business_context, TokenClaims
from app.schemas.business import BusinessCreateRequest, BusinessUpdateRequest, AddMemberRequest, UpdateMemberRequest
from app.services.business_service import BusinessService

router = APIRouter(prefix="/api/v1/businesses", tags=["Businesses"])


def _ok(request: Request, data) -> dict:
    return {
        "success": True,
        "data": data,
        "request_id": getattr(request.state, "request_id", "unknown"),
    }


# ───────────────────────────────────────────────
# Business CRUD
# ───────────────────────────────────────────────

@router.post("", status_code=201)
async def create_business(
    body: BusinessCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(get_current_user),
):
    # Determine actor role (None if no memberships yet – new user)
    actor_role = next(iter(token.memberships.values()), None) if token.memberships else None
    svc = BusinessService(db, request_id=getattr(request.state, "request_id", None))
    biz = await svc.create_business(
        actor_user_id=token.user_id,
        actor_role_in_token=actor_role,
        business_name=body.business_name,
        base_currency=body.base_currency,
        timezone=body.timezone,
    )
    return _ok(request, biz)


@router.get("", status_code=200)
async def list_businesses(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(get_current_user),
    limit: int = Query(default=25, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
):
    svc = BusinessService(db, request_id=getattr(request.state, "request_id", None))
    items, next_cursor = await svc.list_businesses(
        actor_user_id=token.user_id, limit=limit, cursor=cursor
    )
    return _ok(request, {"items": items, "next_cursor": next_cursor, "has_more": next_cursor is not None})


@router.get("/{business_id}", status_code=200)
async def get_business(
    business_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(get_current_user),
):
    svc = BusinessService(db, request_id=getattr(request.state, "request_id", None))
    biz = await svc.get_business(
        business_id=business_id, actor_user_id=token.user_id, actor_memberships=token.memberships
    )
    return _ok(request, biz)


@router.patch("/{business_id}", status_code=200)
async def update_business(
    business_id: uuid.UUID,
    body: BusinessUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(get_current_user),
):
    svc = BusinessService(db, request_id=getattr(request.state, "request_id", None))
    biz = await svc.update_business(
        business_id=business_id,
        actor_user_id=token.user_id,
        actor_memberships=token.memberships,
        business_name=body.business_name,
        timezone=body.timezone,
    )
    return _ok(request, biz)


@router.delete("/{business_id}", status_code=200)
async def delete_business(
    business_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(get_current_user),
):
    svc = BusinessService(db, request_id=getattr(request.state, "request_id", None))
    await svc.delete_business(
        business_id=business_id,
        actor_user_id=token.user_id,
        actor_memberships=token.memberships,
    )
    return _ok(request, {"message": "Business deactivated."})


# ───────────────────────────────────────────────
# Member management
# ───────────────────────────────────────────────

@router.get("/{business_id}/members", status_code=200)
async def list_members(
    business_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(get_current_user),
    limit: int = Query(default=25, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
):
    svc = BusinessService(db, request_id=getattr(request.state, "request_id", None))
    items, next_cursor = await svc.list_members(
        business_id=business_id,
        actor_memberships=token.memberships,
        limit=limit,
        cursor=cursor,
    )
    return _ok(request, {"items": items, "next_cursor": next_cursor, "has_more": next_cursor is not None})


@router.post("/{business_id}/members", status_code=201)
async def add_member(
    business_id: uuid.UUID,
    body: AddMemberRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(get_current_user),
):
    try:
        target_uid = uuid.UUID(body.user_id)
    except ValueError:
        from app.core.errors import BadRequestException
        raise BadRequestException("user_id must be a valid UUID.")
    svc = BusinessService(db, request_id=getattr(request.state, "request_id", None))
    member = await svc.add_member(
        business_id=business_id,
        actor_user_id=token.user_id,
        actor_memberships=token.memberships,
        target_user_id=target_uid,
        role=body.role,
    )
    return _ok(request, member)


@router.patch("/{business_id}/members/{target_user_id}", status_code=200)
async def update_member(
    business_id: uuid.UUID,
    target_user_id: uuid.UUID,
    body: UpdateMemberRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(get_current_user),
):
    svc = BusinessService(db, request_id=getattr(request.state, "request_id", None))
    member = await svc.update_member(
        business_id=business_id,
        actor_user_id=token.user_id,
        actor_memberships=token.memberships,
        target_user_id=target_user_id,
        new_role=body.role,
        is_active=body.is_active,
    )
    return _ok(request, member)
