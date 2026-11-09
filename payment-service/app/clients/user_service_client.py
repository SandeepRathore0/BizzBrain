import time
from typing import Optional
import httpx
from app.core.config import get_settings
from app.core.errors import ServiceUnavailableException
from app.core.logging import get_logger

logger = get_logger("user_service_client")


class UserServiceClient:
    def __init__(self):
        self.cached_token: Optional[str] = None
        self.token_expires_at: float = 0.0
        self.failure_count: int = 0
        self.circuit_open_until: float = 0.0

    def is_circuit_open(self) -> bool:
        if self.failure_count >= 5:
            if time.time() < self.circuit_open_until:
                return True
            else:
                # Reset circuit on expiry
                self.failure_count = 0
        return False

    def record_failure(self):
        self.failure_count += 1
        if self.failure_count >= 5:
            self.circuit_open_until = time.time() + 30.0  # Open for 30s
            logger.error("user_service_circuit_opened", failure_count=self.failure_count)

    def record_success(self):
        self.failure_count = 0

    async def get_service_token(self) -> str:
        """Obtain service token via client credentials, cached until shortly before expiry."""
        now = time.time()
        if self.cached_token and now < (self.token_expires_at - 60):
            return self.cached_token

        if self.is_circuit_open():
            raise ServiceUnavailableException(
                code="USER_SERVICE_UNAVAILABLE",
                message="User Service circuit breaker is open.",
            )

        settings = get_settings()
        url = f"{settings.USER_SERVICE_BASE_URL}/api/v1/auth/service-token"
        payload = {
            "client_id": settings.USER_SERVICE_CLIENT_ID,
            "client_secret": settings.USER_SERVICE_CLIENT_SECRET,
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    # User service envelope data or direct
                    token_data = data.get("data", data)
                    token = token_data.get("token") or token_data.get("access_token")
                    expires_in = token_data.get("expires_in", 300)
                    self.cached_token = token
                    self.token_expires_at = now + expires_in
                    self.record_success()
                    return token
                else:
                    self.record_failure()
                    raise ServiceUnavailableException(
                        code="USER_SERVICE_UNAVAILABLE",
                        message=f"Failed to obtain service token: HTTP {resp.status_code}",
                    )
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            self.record_failure()
            raise ServiceUnavailableException(
                code="USER_SERVICE_UNAVAILABLE",
                message=f"Network error communicating with User Service: {exc}",
            )


# Singleton instance
_user_service_client = UserServiceClient()


def get_user_service_client() -> UserServiceClient:
    return _user_service_client
