from dataclasses import dataclass
from typing import Dict, List, Optional
from uuid import UUID
from fastapi import Header, Request, Depends
import jwt
from app.core.config import settings
from app.core.errors import (
    BadRequestException,
    ForbiddenException,
    UnauthorizedException,
)
from app.core.jwks import jwks_client
from app.core.logging import logger


@dataclass
class UserContext:
    user_id: UUID
    business_id: UUID
    role: str
    memberships: Dict[str, str]


@dataclass
class ServiceContext:
    client_id: str
    caller_type: str = "SERVICE"


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise UnauthorizedException("Missing Authorization header")
    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise UnauthorizedException("Invalid Authorization header format. Expected 'Bearer <token>'")
    return parts[1]


async def decode_rs256_token(token: str, expected_audience: str) -> dict:
    """Decodes and verifies RS256 JWT using JWKS keys."""
    try:
        unverified_header = jwt.get_unverified_header(token)
    except Exception as e:
        raise UnauthorizedException(f"Invalid token header: {str(e)}")

    alg = unverified_header.get("alg")
    if alg != "RS256":
        logger.warning("token_rejected_invalid_algorithm", alg=alg)
        raise UnauthorizedException(f"Invalid algorithm '{alg}'. Only RS256 is supported.")

    kid = unverified_header.get("kid")
    public_key = await jwks_client.get_key_for_kid(kid)

    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=settings.JWT_ISSUER,
            audience=expected_audience,
            options={
                "require": ["exp", "iss", "aud", "sub"],
                "verify_exp": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise UnauthorizedException("Token has expired")
    except jwt.InvalidAudienceError:
        raise UnauthorizedException("Token audience is invalid")
    except jwt.InvalidIssuerError:
        raise UnauthorizedException("Token issuer is invalid")
    except jwt.PyJWTError as e:
        raise UnauthorizedException(f"Token verification failed: {str(e)}")


async def get_current_user(
    request: Request,
    x_business_id: Optional[str] = Header(None, alias="X-Business-ID"),
) -> UserContext:
    token = _extract_bearer_token(request)
    payload = await decode_rs256_token(token, expected_audience=settings.JWT_AUDIENCE)

    # Ensure this is a user token (must have 'memberships' claim)
    memberships = payload.get("memberships")
    if memberships is None or not isinstance(memberships, dict):
        raise ForbiddenException("Invalid user token: missing memberships claim")

    sub = payload.get("sub")
    try:
        user_id = UUID(str(sub))
    except (ValueError, TypeError):
        raise UnauthorizedException("Invalid user ID in token sub claim")

    # Determine business context
    resolved_business_id_str: str

    if x_business_id:
        # Header explicitly provided
        try:
            # Validate UUID format
            UUID(x_business_id)
            resolved_business_id_str = x_business_id
        except ValueError:
            raise BadRequestException("Invalid X-Business-ID header format. Must be UUID")

        if resolved_business_id_str not in memberships:
            logger.warning(
                "business_id_not_in_token_memberships",
                user_id=str(user_id),
                requested_business_id=resolved_business_id_str,
            )
            raise ForbiddenException("User is not an active member of the specified business")
    else:
        # Header omitted: default if exactly one membership; 400 if several
        if len(memberships) == 1:
            resolved_business_id_str = next(iter(memberships.keys()))
        elif len(memberships) > 1:
            raise BadRequestException("X-Business-ID header is required when user belongs to multiple businesses")
        else:
            raise ForbiddenException("User has no active business memberships")

    role = memberships.get(resolved_business_id_str)
    if not role or role.upper() not in ("OWNER", "ADMIN", "EMPLOYEE"):
        raise ForbiddenException("Invalid or inactive role for business")

    return UserContext(
        user_id=user_id,
        business_id=UUID(resolved_business_id_str),
        role=role.upper(),
        memberships=memberships,
    )


async def get_service_token(request: Request) -> ServiceContext:
    """Verifies service-to-service internal calls."""
    token = _extract_bearer_token(request)
    payload = await decode_rs256_token(token, expected_audience=settings.INTERNAL_JWT_AUDIENCE)

    # Reject user tokens on internal routes
    if "memberships" in payload:
        logger.warning("user_token_rejected_on_internal_route")
        raise ForbiddenException("User tokens are not allowed on internal endpoints")

    caller_id = payload.get("sub") or payload.get("client_id")
    if not caller_id:
        raise UnauthorizedException("Service token missing client/sub identifier")

    return ServiceContext(client_id=str(caller_id), caller_type="SERVICE")


def require_internal_caller(allowed_callers: List[str]):
    async def _dependency(service_ctx: ServiceContext = Depends(get_service_token)) -> ServiceContext:
        if service_ctx.client_id not in allowed_callers:
            logger.warning(
                "internal_caller_not_allowlisted",
                caller=service_ctx.client_id,
                allowed=allowed_callers,
            )
            raise ForbiddenException(f"Caller '{service_ctx.client_id}' is not authorized for this internal endpoint")
        return service_ctx
    return _dependency
