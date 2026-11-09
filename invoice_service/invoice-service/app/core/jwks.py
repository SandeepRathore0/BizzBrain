import time
from typing import Any, Dict, Optional
import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from app.core.config import settings
from app.core.errors import UnauthorizedException
from app.core.logging import logger


class JWKSClient:
    def __init__(self, jwks_url: Optional[str] = None, ttl_seconds: int = 3600):
        self.jwks_url = jwks_url or settings.JWKS_URL
        self.ttl_seconds = ttl_seconds
        self._keys: Dict[str, Any] = {}
        self._last_fetched: float = 0.0
        self._test_keys: Dict[str, Any] = {}  # Allow injection of test public keys

    def set_test_key(self, kid: str, public_key_pem_or_obj: Any) -> None:
        """Inject a test key for test suites."""
        self._test_keys[kid] = public_key_pem_or_obj

    def clear_test_keys(self) -> None:
        self._test_keys.clear()

    async def _fetch_jwks(self) -> None:
        if not self.jwks_url:
            raise UnauthorizedException("JWKS URL is not configured")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(self.jwks_url)
                if resp.status_code != 200:
                    logger.error("jwks_fetch_failed", status_code=resp.status_code)
                    raise UnauthorizedException("Failed to fetch JWKS keys")
                data = resp.json()
                keys = data.get("keys", [])
                new_keys = {}
                for k in keys:
                    kid = k.get("kid")
                    if kid:
                        new_keys[kid] = RSAAlgorithm.from_jwk(k)
                self._keys = new_keys
                self._last_fetched = time.time()
                logger.info("jwks_keys_refreshed", count=len(new_keys))
        except Exception as e:
            logger.error("jwks_fetch_error", error=str(e))
            raise UnauthorizedException("Could not verify token signing keys")

    async def get_key_for_kid(self, kid: Optional[str]) -> Any:
        # Check test keys first
        if kid and kid in self._test_keys:
            return self._test_keys[kid]

        now = time.time()
        # Fetch if empty or cache expired
        if not self._keys or (now - self._last_fetched > self.ttl_seconds):
            await self._fetch_jwks()

        if kid in self._keys:
            return self._keys[kid]

        # Unknown kid: refresh once from server in case of key rotation
        logger.info("jwks_unknown_kid_refreshing", kid=kid)
        await self._fetch_jwks()

        if kid in self._keys:
            return self._keys[kid]

        logger.warning("jwks_key_not_found", kid=kid)
        raise UnauthorizedException("Invalid token: unknown signing key ID (kid)")


jwks_client = JWKSClient()
