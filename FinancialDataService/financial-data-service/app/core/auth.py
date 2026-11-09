import time
from typing import Any, Dict, Optional
import httpx
import jwt
from jwt import PyJWKClient, PyJWK
from pydantic import BaseModel, Field
import structlog
from app.core.config import settings
from app.core.errors import AppException, UnauthorizedException, ServiceUnavailableException

logger = structlog.get_logger()


class UserTokenPayload(BaseModel):
    sub: str
    jti: Optional[str] = None
    iat: Optional[int] = None
    exp: Optional[int] = None
    iss: Optional[str] = None
    aud: Optional[str] = None
    memberships: Dict[str, str] = Field(default_factory=dict)  # business_id -> role


class ServiceTokenPayload(BaseModel):
    sub: str
    iss: Optional[str] = None
    aud: Optional[str] = None
    exp: Optional[int] = None


class JWKSCache:
    def __init__(self, jwks_url: str, ttl_seconds: int = 3600):
        self.jwks_url = jwks_url
        self.ttl_seconds = ttl_seconds
        self.keys: Dict[str, PyJWK] = {}
        self.last_fetch: float = 0

    async def get_signing_key(self, token: str) -> PyJWK:
        try:
            unverified_header = jwt.get_unverified_header(token)
        except Exception as e:
            raise UnauthorizedException("Invalid token header") from e

        algorithm = unverified_header.get("alg")
        if algorithm != "RS256":
            raise UnauthorizedException(f"Unsupported algorithm '{algorithm}', expected RS256")

        kid = unverified_header.get("kid")
        if not kid:
            raise UnauthorizedException("Token missing 'kid' in header")

        now = time.time()
        # If cache expired or kid not found, refresh JWKS
        if kid not in self.keys or (now - self.last_fetch > self.ttl_seconds):
            await self.refresh_keys()

        if kid not in self.keys:
            # Try one more refresh in case key was just rotated
            await self.refresh_keys()

        if kid not in self.keys:
            raise UnauthorizedException(f"Signing key '{kid}' not found in JWKS")

        return self.keys[kid]

    async def refresh_keys(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=5.0)) as client:
                resp = await client.get(self.jwks_url)
                if resp.status_code != 200:
                    logger.error("jwks_fetch_failed", status_code=resp.status_code)
                    raise ServiceUnavailableException("Failed to fetch JWKS from User Service")
                jwks_data = resp.json()
                keys_list = jwks_data.get("keys", [])
                new_keys = {}
                for k in keys_list:
                    pyjwk = PyJWK(k)
                    new_keys[pyjwk.key_id] = pyjwk
                self.keys = new_keys
                self.last_fetch = time.time()
                logger.info("jwks_cache_refreshed", key_count=len(self.keys))
        except (httpx.RequestError, httpx.TimeoutException) as e:
            logger.error("jwks_network_error", error=str(e))
            raise ServiceUnavailableException("User Service unavailable for JWKS") from e


jwks_cache = JWKSCache(
    jwks_url=f"{settings.USER_SERVICE_BASE_URL.rstrip('/')}/.well-known/jwks.json",
    ttl_seconds=settings.JWKS_CACHE_TTL_SECONDS,
)


async def verify_user_token(token: str) -> UserTokenPayload:
    try:
        signing_key = await jwks_cache.get_signing_key(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.JWT_AUDIENCE,
            options={"verify_exp": True, "verify_signature": True, "verify_aud": True},
        )
        return UserTokenPayload(**payload)
    except jwt.ExpiredSignatureError as e:
        raise UnauthorizedException("Token has expired") from e
    except jwt.InvalidTokenError as e:
        raise UnauthorizedException("Invalid token") from e
    except AppException:
        raise
    except Exception as e:
        logger.warning("token_verification_error", error=str(e))
        raise UnauthorizedException("Token verification failed") from e


async def verify_service_token(token: str, expected_audience: str) -> ServiceTokenPayload:
    try:
        signing_key = await jwks_cache.get_signing_key(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=expected_audience,
            options={"verify_exp": True, "verify_signature": True, "verify_aud": True},
        )
        return ServiceTokenPayload(**payload)
    except jwt.ExpiredSignatureError as e:
        raise UnauthorizedException("Service token has expired") from e
    except jwt.InvalidAudienceError as e:
        raise UnauthorizedException("Service token audience mismatch") from e
    except jwt.InvalidTokenError as e:
        raise UnauthorizedException("Invalid service token") from e
    except AppException:
        raise
    except Exception as e:
        logger.warning("service_token_verification_error", error=str(e))
        raise UnauthorizedException("Service token verification failed") from e
