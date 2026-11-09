import json
import uuid
from dataclasses import dataclass
from typing import Any, Optional
import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from fastapi import Header, Request

from app.core.config import get_settings
from app.core.errors import (
    BadRequestException,
    ForbiddenException,
    UnauthorizedException,
)
from app.core.permissions import has_permission

# In-memory JWKS cache
_JWKS_CACHE: dict[str, Any] = {}


@dataclass
class AuthContext:
    user_id: uuid.UUID
    business_id: uuid.UUID
    role: str
    token_claims: dict[str, Any]


def set_test_jwks(jwks: dict[str, Any]) -> None:
    """Helper for unit and integration testing to inject JWKS directly."""
    global _JWKS_CACHE
    _JWKS_CACHE = jwks


async def get_jwks() -> dict[str, Any]:
    global _JWKS_CACHE
    if _JWKS_CACHE:
        return _JWKS_CACHE

    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(settings.USER_SERVICE_JWKS_URL)
            if resp.status_code == 200:
                _JWKS_CACHE = resp.json()
                return _JWKS_CACHE
    except Exception:
        pass
    return _JWKS_CACHE


async def verify_jwt_token(token: str) -> dict[str, Any]:
    """Verify RS256 token against User Service JWKS."""
    if not token:
        raise UnauthorizedException(code="TOKEN_MISSING", message="Authorization Bearer token is required.")

    # 1. Unverified header inspect for kid & alg
    try:
        unverified_header = jwt.get_unverified_header(token)
    except Exception as e:
        raise UnauthorizedException(code="INVALID_TOKEN_HEADER", message=f"Malformed JWT header: {e}")

    alg = unverified_header.get("alg")
    if alg != "RS256":
        raise UnauthorizedException(
            code="UNSUPPORTED_ALGORITHM",
            message=f"Only RS256 algorithm is supported. Got: {alg}",
        )

    kid = unverified_header.get("kid")
    if not kid:
        raise UnauthorizedException(code="MISSING_KID", message="Token header missing 'kid'.")

    # 2. Get public key from JWKS
    jwks = await get_jwks()
    keys = jwks.get("keys", [])
    matching_key = next((k for k in keys if k.get("kid") == kid), None)
    if not matching_key:
        raise UnauthorizedException(code="UNKNOWN_KID", message="Unknown key identifier 'kid'.")

    public_key = RSAAlgorithm.from_jwk(json.dumps(matching_key))

    # 3. Verify signature, exp, iss, aud
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer="bizbrain-user-service",
            audience="bizbrain-services",
            options={
                "require": ["exp", "iss", "aud", "sub"],
                "verify_exp": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )
    except jwt.ExpiredSignatureError:
        raise UnauthorizedException(code="TOKEN_EXPIRED", message="Authorization token has expired.")
    except jwt.InvalidIssuerError:
        raise UnauthorizedException(code="INVALID_ISSUER", message="Token issuer mismatch.")
    except jwt.InvalidAudienceError:
        raise UnauthorizedException(code="INVALID_AUDIENCE", message="Token audience mismatch.")
    except jwt.PyJWTError as e:
        raise UnauthorizedException(code="INVALID_TOKEN", message=f"Token verification failed: {e}")

    return payload


async def get_current_auth(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_business_id: Optional[str] = Header(None, alias="X-Business-ID"),
) -> AuthContext:
    """Dependency for staff routes requiring authenticated user with business context."""
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedException(
            code="AUTHORIZATION_REQUIRED",
            message="Bearer token required in Authorization header.",
        )

    token = authorization.split(" ", 1)[1].strip()
    payload = await verify_jwt_token(token)

    sub = payload.get("sub")
    try:
        user_id = uuid.UUID(sub)
    except (TypeError, ValueError):
        raise UnauthorizedException(code="INVALID_SUBJECT", message="Token 'sub' must be a valid UUID.")

    memberships: dict[str, str] = payload.get("memberships", {})
    if not isinstance(memberships, dict):
        raise ForbiddenException(code="INVALID_MEMBERSHIPS", message="Token memberships claim must be an object.")

    # Business context resolution
    target_business_id_str: Optional[str] = None
    if x_business_id:
        target_business_id_str = x_business_id.strip()
    else:
        # If missing and several memberships -> 400
        if len(memberships) > 1:
            raise BadRequestException(
                code="MISSING_BUSINESS_ID",
                message="X-Business-ID header is required when user belongs to multiple businesses.",
            )
        elif len(memberships) == 1:
            target_business_id_str = next(iter(memberships.keys()))
        else:
            raise ForbiddenException(
                code="NO_BUSINESS_MEMBERSHIP",
                message="User does not have an active business membership.",
            )

    try:
        business_uuid = uuid.UUID(target_business_id_str)
    except (TypeError, ValueError):
        raise BadRequestException(code="INVALID_BUSINESS_ID", message="X-Business-ID must be a valid UUID.")

    # Match against token memberships
    role = memberships.get(str(business_uuid))
    if not role:
        raise ForbiddenException(
            code="NOT_A_BUSINESS_MEMBER",
            message="User does not belong to the requested business.",
        )

    # Permission check for payments
    if not has_permission(role, "payments.view"):
        raise ForbiddenException(
            code="INSUFFICIENT_PERMISSIONS",
            message=f"Role '{role}' lacks 'payments.view' permission.",
        )

    auth_ctx = AuthContext(
        user_id=user_id,
        business_id=business_uuid,
        role=role,
        token_claims=payload,
    )
    request.state.auth = auth_ctx
    return auth_ctx
