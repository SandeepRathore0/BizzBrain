"""FastAPI dependencies for JWT authentication and tenancy resolution."""

from __future__ import annotations

import uuid
from typing import Dict, Optional

import jwt as pyjwt
from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import ForbiddenException, UnauthorizedException
from app.core.security import decode_jwt_token


class TokenClaims:
    """Parsed and verified JWT claims attached to the request."""

    def __init__(self, claims: dict) -> None:
        self.sub: str = claims["sub"]
        self.user_id: uuid.UUID = uuid.UUID(self.sub)
        self.jti: str = claims.get("jti", "")
        self.memberships: Dict[str, str] = claims.get("memberships", {})
        self.is_service_token: bool = claims.get("type") == "service_token"


async def get_current_user(request: Request) -> TokenClaims:
    """Dependency: extracts and verifies the Bearer JWT from Authorization header."""
    auth_header: Optional[str] = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise UnauthorizedException("Bearer token required.")
    raw_token = auth_header.removeprefix("Bearer ").strip()
    try:
        claims = decode_jwt_token(raw_token)
    except pyjwt.ExpiredSignatureError:
        raise UnauthorizedException("Access token has expired.")
    except pyjwt.InvalidTokenError as e:
        raise UnauthorizedException(f"Invalid access token: {e}")

    tc = TokenClaims(claims)
    if tc.is_service_token:
        raise UnauthorizedException("Service tokens cannot be used for user endpoints.")
    return tc


async def get_service_token(request: Request) -> TokenClaims:
    """Dependency: verifies the Bearer JWT is a valid service token."""
    auth_header: Optional[str] = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise UnauthorizedException("Bearer token required.")
    raw_token = auth_header.removeprefix("Bearer ").strip()
    try:
        claims = decode_jwt_token(raw_token)
    except pyjwt.ExpiredSignatureError:
        raise UnauthorizedException("Service token has expired.")
    except pyjwt.InvalidTokenError as e:
        raise UnauthorizedException(f"Invalid service token: {e}")

    tc = TokenClaims(claims)
    if not tc.is_service_token:
        raise ForbiddenException("This endpoint requires a service token.")
    return tc


def resolve_business_id(
    x_business_id: Optional[str] = Header(None, alias="X-Business-ID"),
) -> Optional[uuid.UUID]:
    """Extract the X-Business-ID header and return as UUID (or None)."""
    if not x_business_id:
        return None
    try:
        return uuid.UUID(x_business_id)
    except ValueError:
        raise ForbiddenException("X-Business-ID header must be a valid UUID.")


def require_business_context(
    token: TokenClaims = Depends(get_current_user),
    x_business_id: Optional[uuid.UUID] = Depends(resolve_business_id),
) -> tuple[TokenClaims, uuid.UUID]:
    """
    Ensures a valid X-Business-ID is provided and the caller is a member.
    If the user has exactly one active business and no header is provided, auto-resolve.
    Otherwise 400 (ambiguous) or 403 (not a member).
    """
    memberships = token.memberships
    if x_business_id is None:
        if len(memberships) == 1:
            bid_str = next(iter(memberships))
            return token, uuid.UUID(bid_str)
        elif len(memberships) == 0:
            raise ForbiddenException("You are not a member of any business.")
        else:
            from app.core.errors import BadRequestException
            raise BadRequestException(
                "X-Business-ID header is required when you have multiple business memberships."
            )

    # Validate membership
    if str(x_business_id) not in memberships:
        raise ForbiddenException("You are not a member of the specified business.")

    return token, x_business_id
