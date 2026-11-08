import asyncio
import time
from typing import Any, Dict, Optional
from uuid import UUID
import httpx
from app.core.config import settings
from app.core.errors import DependencyException, NotFoundException
from app.core.logging import logger, request_id_ctx


class FinancialDataServiceClient:
    def __init__(self):
        self.base_url = settings.FINANCIAL_DATA_SERVICE_URL.rstrip("/")
        self._cache: Dict[str, Optional[Dict[str, Any]]] = {}
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def set_cached_customer(
        self, customer_id: UUID, customer_data: Optional[Dict[str, Any]]
    ) -> None:
        """Helper to inject test customer data or 404 None in tests."""
        self._cache[str(customer_id)] = customer_data

    async def get_customer(self, business_id: UUID, customer_id: UUID) -> Optional[Dict[str, Any]]:
        """Calls Financial Data Service: GET /internal/v1/customers/{customer_id}?business_id={id} (Contract C1).
        Enforces 3s connect, 5s read timeout, 2 retries on GET, and circuit breaker.
        Returns customer dict, or None if customer not found (404).
        Raises DependencyException (503) if Financial Data Service is unreachable or returns an error.
        """
        cid_str = str(customer_id)
        bid_str = str(business_id)

        # 1. Check test injection cache
        if cid_str in self._cache:
            data = self._cache[cid_str]
            if data is None:
                return None
            return data

        now = time.time()
        # 2. Check circuit breaker
        if now < self._circuit_open_until:
            logger.error("circuit_breaker_open_financial_data_service")
            raise DependencyException("Financial Data Service dependency temporarily unavailable (circuit open)")

        url = f"{self.base_url}/internal/v1/customers/{cid_str}"
        params = {"business_id": bid_str}
        req_id = request_id_ctx.get()
        headers = {"X-Request-ID": req_id}

        last_error = None
        for attempt in range(1, 4):  # Initial + 2 retries
            try:
                timeout = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=5.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(url, params=params, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json().get("data", {})
                        self._consecutive_failures = 0
                        return data
                    elif resp.status_code == 404:
                        return None
                    else:
                        logger.warning(
                            "financial_data_service_returned_error",
                            status=resp.status_code,
                            attempt=attempt,
                        )
                        last_error = f"Status {resp.status_code}"
            except Exception as e:
                logger.warning(
                    "financial_data_service_call_failed",
                    error=str(e),
                    attempt=attempt,
                )
                last_error = str(e)

            if attempt < 3:
                await asyncio.sleep(0.2 * (2 ** (attempt - 1)))

        # Tripped on repeated failure
        self._consecutive_failures += 1
        if self._consecutive_failures >= 5:
            self._circuit_open_until = now + 30.0
            logger.error("tripped_circuit_breaker_financial_data_service")

        raise DependencyException(f"Failed to communicate with Financial Data Service: {last_error}")


financial_data_client = FinancialDataServiceClient()
