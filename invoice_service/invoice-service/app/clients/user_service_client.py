import asyncio
import time
from typing import Any, Dict, Optional, Tuple
from uuid import UUID
import httpx
from app.core.config import settings
from app.core.errors import DependencyException
from app.core.logging import logger, request_id_ctx


class UserServiceClient:
    def __init__(self):
        self.base_url = settings.USER_SERVICE_URL.rstrip("/")
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_expiry: Dict[str, float] = {}
        self._authz_overrides: Dict[Tuple[str, str, str], bool] = {}
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def set_cached_business_metadata(self, business_id: UUID, currency: str, timezone: str) -> None:
        """Helper to inject test business metadata in tests."""
        key = str(business_id)
        self._cache[key] = {"base_currency": currency, "timezone": timezone, "is_active": True}
        self._cache_expiry[key] = time.time() + 999999.0

    def set_authz_override(
        self, user_id: UUID, business_id: UUID, permission_code: str, authorized: bool
    ) -> None:
        """Helper to override live authz checks in tests."""
        self._authz_overrides[(str(user_id), str(business_id), permission_code)] = authorized

    async def get_business_metadata(self, business_id: UUID) -> Dict[str, Any]:
        """Fetches base_currency, timezone, and is_active from User Service (Contract C5).
        Caches results for 5 minutes (300 seconds).
        Includes 3s connect, 5s read timeout, 2 retries, and circuit breaker.
        """
        now = time.time()
        key = str(business_id)

        # 1. Check in-memory cache
        if key in self._cache and now < self._cache_expiry.get(key, 0):
            return self._cache[key]

        # 2. Check circuit breaker
        if now < self._circuit_open_until:
            logger.error("circuit_breaker_open_user_service")
            raise DependencyException("User Service dependency temporarily unavailable (circuit open)")

        url = f"{self.base_url}/internal/v1/businesses/{business_id}"
        req_id = request_id_ctx.get()
        headers = {"X-Request-ID": req_id}

        last_error = None
        for attempt in range(1, 4):  # Initial + 2 retries
            try:
                timeout = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=5.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json().get("data", {})
                        self._cache[key] = data
                        self._cache_expiry[key] = now + 300.0  # 5 min cache
                        self._consecutive_failures = 0
                        return data
                    elif resp.status_code == 404:
                        logger.warning("business_not_found_in_user_service", business_id=key)
                        raise DependencyException("Business not found in User Service")
                    else:
                        logger.warning("user_service_returned_error", status=resp.status_code, attempt=attempt)
                        last_error = f"Status {resp.status_code}"
            except Exception as e:
                logger.warning("user_service_call_failed", error=str(e), attempt=attempt)
                last_error = str(e)

            if attempt < 3:
                await asyncio.sleep(0.2 * (2 ** (attempt - 1)))  # Exponential backoff

        # Failed after retries
        self._consecutive_failures += 1
        if self._consecutive_failures >= 5:
            self._circuit_open_until = now + 30.0  # Trip circuit for 30s
            logger.error("tripped_circuit_breaker_user_service")

        raise DependencyException(f"Failed to communicate with User Service: {last_error}")

    async def check_live_authz(
        self, user_id: UUID, business_id: UUID, permission_code: str
    ) -> bool:
        """Calls User Service POST /internal/v1/authz/check for sensitive operations (issue, void).
        3s connect, 5s read timeout. Returns boolean.
        Raises DependencyException (503) if User Service is unreachable or returns error.
        """
        override_key = (str(user_id), str(business_id), permission_code)
        if override_key in self._authz_overrides:
            return self._authz_overrides[override_key]

        now = time.time()
        if now < self._circuit_open_until:
            logger.error("circuit_breaker_open_user_service_authz")
            raise DependencyException("User Service dependency temporarily unavailable (circuit open)")

        url = f"{self.base_url}/internal/v1/authz/check"
        req_id = request_id_ctx.get()
        headers = {"X-Request-ID": req_id}
        payload = {
            "user_id": str(user_id),
            "business_id": str(business_id),
            "permission": permission_code,
        }

        try:
            timeout = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=5.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    self._consecutive_failures = 0
                    return bool(resp.json().get("data", {}).get("authorized", False))
                elif resp.status_code == 403:
                    self._consecutive_failures = 0
                    return False
                else:
                    self._consecutive_failures += 1
                    logger.warning("user_service_authz_error_response", status=resp.status_code)
                    raise DependencyException("User Service returned error during authorization check")
        except httpx.RequestError as e:
            self._consecutive_failures += 1
            if self._consecutive_failures >= 5:
                self._circuit_open_until = now + 30.0
            logger.warning("user_service_authz_call_failed", error=str(e))
            raise DependencyException(f"Failed to communicate with User Service: {str(e)}")


user_service_client = UserServiceClient()
