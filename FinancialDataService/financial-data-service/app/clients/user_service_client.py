import asyncio
import time
from typing import Any, Dict, Optional, Tuple
import uuid
import httpx
from pydantic import BaseModel
import structlog
from app.core.config import settings
from app.core.errors import ForbiddenException, ServiceUnavailableException

logger = structlog.get_logger()


class BusinessDetails(BaseModel):
    business_id: uuid.UUID
    base_currency: str
    timezone: str
    is_active: bool


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.is_open = False

    def record_success(self) -> None:
        self.failure_count = 0
        self.is_open = False

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.is_open = True
            logger.warning("circuit_breaker_opened", service="user-service")

    def allow_request(self) -> bool:
        if not self.is_open:
            return True
        if time.time() - self.last_failure_time > self.recovery_timeout:
            # Half-open state: allow a probe
            return True
        return False


class UserServiceClient:
    def __init__(self) -> None:
        self.base_url = settings.USER_SERVICE_BASE_URL.rstrip("/")
        self.business_cache: Dict[str, Tuple[BusinessDetails, float]] = {}
        self.cache_ttl = settings.BUSINESS_CACHE_TTL_SECONDS
        self.circuit_breaker = CircuitBreaker()
        self.service_token: Optional[str] = None
        self.token_expiry: float = 0.0

    async def get_business_details(
        self,
        business_id: uuid.UUID,
        request_id: str,
    ) -> BusinessDetails:
        bid_str = str(business_id)
        now = time.time()

        # Check cache
        if bid_str in self.business_cache:
            details, cached_at = self.business_cache[bid_str]
            if now - cached_at < self.cache_ttl:
                return details

        if not self.circuit_breaker.allow_request():
            logger.error("user_service_circuit_open")
            raise ServiceUnavailableException("User Service dependency temporarily unavailable")

        url = f"{self.base_url}/internal/v1/businesses/{bid_str}"
        headers = {
            "X-Request-ID": request_id,
        }

        # Safe GET with retries and exponential backoff
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=5.0)) as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        self.circuit_breaker.record_success()
                        data = resp.json().get("data", resp.json())
                        details = BusinessDetails(
                            business_id=business_id,
                            base_currency=data.get("base_currency", "USD"),
                            timezone=data.get("timezone", "UTC"),
                            is_active=data.get("is_active", True),
                        )
                        self.business_cache[bid_str] = (details, now)
                        return details
                    elif resp.status_code == 404:
                        raise ServiceUnavailableException("Business not found in User Service")
                    else:
                        logger.warning("user_service_error_status", status_code=resp.status_code)
            except (httpx.RequestError, httpx.TimeoutException) as e:
                logger.warning("user_service_request_retry", attempt=attempt, error=str(e))
                if attempt < max_retries:
                    await asyncio.sleep(0.2 * (2**attempt))
                else:
                    self.circuit_breaker.record_failure()

        # If cache contains stale entry on network failure, return it as fallback if available
        if bid_str in self.business_cache:
            details, _ = self.business_cache[bid_str]
            return details

        raise ServiceUnavailableException("Failed to fetch business details from User Service")

    async def check_live_authz(
        self,
        user_id: uuid.UUID,
        business_id: uuid.UUID,
        permission: str,
        request_id: str,
    ) -> bool:
        """Call User Service POST /internal/v1/authz/check for sensitive actions."""
        if not self.circuit_breaker.allow_request():
            raise ServiceUnavailableException("User Service dependency temporarily unavailable for authz check")

        url = f"{self.base_url}/internal/v1/authz/check"
        payload = {
            "user_id": str(user_id),
            "business_id": str(business_id),
            "permission": permission,
        }
        headers = {
            "X-Request-ID": request_id,
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=5.0)) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    self.circuit_breaker.record_success()
                    data = resp.json()
                    return data.get("data", {}).get("allowed", False)
                elif resp.status_code == 403:
                    return False
                else:
                    self.circuit_breaker.record_failure()
                    raise ServiceUnavailableException("User Service returned error during authz check")
        except (httpx.RequestError, httpx.TimeoutException) as e:
            self.circuit_breaker.record_failure()
            logger.error("user_service_authz_unreachable", error=str(e))
            raise ServiceUnavailableException("User Service unreachable for sensitive action authz check") from e


user_service_client = UserServiceClient()
