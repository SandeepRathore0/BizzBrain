"""Internal service router – /internal/v1/*
These endpoints must NOT be reachable via the public API prefix.
Caller must present a valid service token (client-credentials flow).
"""

from __future__ import annotations

import uuid
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_service_token, TokenClaims
from app.core.errors import BadRequestException, UnauthorizedException
from app.core.security import create_service_token
from app.services.business_service import BusinessService

router = APIRouter(prefix="/internal/v1", tags=["Internal"])


def _ok(request: Request, data) -> dict:
    return {
        "success": True,
        "data": data,
        "request_id": getattr(request.state, "request_id", "unknown"),
    }


# ───────────────────────────────────────────────
# Service-token issuance (client-credentials)
# ───────────────────────────────────────────────

class ServiceTokenRequest(BaseModel):
    client_id: str
    client_secret: str


@router.post("/service-token", status_code=200)
async def issue_service_token(body: ServiceTokenRequest, request: Request):
    settings = get_settings()
    expected_secret = settings.INTERNAL_SERVICE_CLIENTS.get(body.client_id)
    if expected_secret is None or expected_secret != body.client_secret:
        raise UnauthorizedException("Invalid client credentials.")

    token = create_service_token(client_id=body.client_id)
    return _ok(
        request,
        {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": settings.SERVICE_TOKEN_EXPIRE_MINUTES * 60,
        },
    )


# ───────────────────────────────────────────────
# Authorization check
# ───────────────────────────────────────────────

class AuthzCheckRequest(BaseModel):
    user_id: str
    business_id: str
    permission: str


@router.post("/authz/check", status_code=200)
async def authz_check(
    body: AuthzCheckRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    svc_token: TokenClaims = Depends(get_service_token),
):
    """Check if a user has a given permission in a business (reads live DB state)."""
    try:
        user_uuid = uuid.UUID(body.user_id)
        biz_uuid = uuid.UUID(body.business_id)
    except ValueError:
        raise BadRequestException("user_id and business_id must be valid UUIDs.")

    bsvc = BusinessService(db, request_id=getattr(request.state, "request_id", None))
    allowed, role = await bsvc.internal_check_authz(
        business_id=biz_uuid, user_id=user_uuid, permission=body.permission
    )

    return _ok(
        request,
        {
            "user_id": body.user_id,
            "business_id": body.business_id,
            "permission": body.permission,
            "allowed": allowed,
            "role": role,
        },
    )


# ───────────────────────────────────────────────
# Internal business lookup
# ───────────────────────────────────────────────

@router.get("/businesses/{business_id}", status_code=200)
async def internal_get_business(
    business_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    svc_token: TokenClaims = Depends(get_service_token),
):
    """Fetch a business record for inter-service use (requires service token)."""
    bsvc = BusinessService(db, request_id=getattr(request.state, "request_id", None))
    biz_data = await bsvc.internal_get_business(business_id)
    return _ok(request, biz_data)
