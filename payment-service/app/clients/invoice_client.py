import time
import uuid
from decimal import Decimal
from typing import Any, Optional
import httpx
from pydantic import BaseModel, Field

from app.clients.user_service_client import get_user_service_client
from app.core.config import get_settings
from app.core.errors import (
    NotFoundException,
    ServiceUnavailableException,
)
from app.core.logging import get_logger

logger = get_logger("invoice_client")


class InvoicePayableDetails(BaseModel):
    invoice_id: uuid.UUID
    business_id: uuid.UUID
    invoice_number: Optional[str] = None
    business_name: str
    payable: bool
    currency: str
    outstanding_balance: Decimal
    min_installment_amount: Optional[Decimal] = None


class ResolvedPayToken(BaseModel):
    invoice_id: uuid.UUID
    business_id: uuid.UUID


class InvoiceClient:
    def __init__(self):
        self.failure_count: int = 0
        self.circuit_open_until: float = 0.0

    def is_circuit_open(self) -> bool:
        if self.failure_count >= 5:
            if time.time() < self.circuit_open_until:
                return True
            else:
                self.failure_count = 0
        return False

    def record_failure(self):
        self.failure_count += 1
        if self.failure_count >= 5:
            self.circuit_open_until = time.time() + 30.0
            logger.error("invoice_service_circuit_opened", failure_count=self.failure_count)

    def record_success(self):
        self.failure_count = 0

    async def get_payable_details(
        self,
        business_id: uuid.UUID,
        invoice_id: uuid.UUID,
        request_id: Optional[str] = None,
    ) -> InvoicePayableDetails:
        """Call C2 GET /internal/v1/invoices/{id}/payable-details?business_id=..."""
        if self.is_circuit_open():
            raise ServiceUnavailableException(
                code="INVOICE_SERVICE_UNAVAILABLE",
                message="Invoice Service circuit breaker is open.",
            )

        settings = get_settings()
        url = f"{settings.INVOICE_SERVICE_BASE_URL}/internal/v1/invoices/{invoice_id}/payable-details"
        params = {"business_id": str(business_id)}
        headers = {
            "X-Request-ID": request_id or str(uuid.uuid4()),
        }

        # Try to attach service token if configured
        try:
            token = await get_user_service_client().get_service_token()
            headers["Authorization"] = f"Bearer {token}"
        except Exception:
            pass  # In local stub/test environment, service token might be optional

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 200:
                    self.record_success()
                    data = resp.json()
                    payload = data.get("data", data)
                    return InvoicePayableDetails(
                        invoice_id=uuid.UUID(payload["invoice_id"]),
                        business_id=uuid.UUID(payload["business_id"]),
                        invoice_number=payload.get("invoice_number"),
                        business_name=payload.get("business_name", "BizBrain Merchant"),
                        payable=bool(payload["payable"]),
                        currency=payload["currency"].upper(),
                        outstanding_balance=Decimal(str(payload["outstanding_balance"])),
                        min_installment_amount=(
                            Decimal(str(payload["min_installment_amount"]))
                            if payload.get("min_installment_amount") is not None
                            else None
                        ),
                    )
                elif resp.status_code == 404:
                    self.record_success()
                    raise NotFoundException(
                        code="INVOICE_NOT_FOUND",
                        message="Invoice not found.",
                    )
                else:
                    self.record_failure()
                    raise ServiceUnavailableException(
                        code="INVOICE_SERVICE_UNAVAILABLE",
                        message=f"Invoice Service returned HTTP {resp.status_code}.",
                    )
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            self.record_failure()
            raise ServiceUnavailableException(
                code="INVOICE_SERVICE_UNAVAILABLE",
                message=f"Failed to communicate with Invoice Service: {exc}",
            )

    async def resolve_pay_token(
        self,
        token: str,
        request_id: Optional[str] = None,
    ) -> ResolvedPayToken:
        """Call C4 POST /internal/v1/invoices/resolve-pay-token. Uniform 404 on any failure."""
        if self.is_circuit_open():
            raise ServiceUnavailableException(
                code="INVOICE_SERVICE_UNAVAILABLE",
                message="Invoice Service circuit breaker is open.",
            )

        settings = get_settings()
        url = f"{settings.INVOICE_SERVICE_BASE_URL}/internal/v1/invoices/resolve-pay-token"
        headers = {
            "X-Request-ID": request_id or str(uuid.uuid4()),
        }

        try:
            token_auth = await get_user_service_client().get_service_token()
            headers["Authorization"] = f"Bearer {token_auth}"
        except Exception:
            pass

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
                resp = await client.post(url, json={"token": token}, headers=headers)
                if resp.status_code == 200:
                    self.record_success()
                    data = resp.json()
                    payload = data.get("data", data)
                    return ResolvedPayToken(
                        invoice_id=uuid.UUID(payload["invoice_id"]),
                        business_id=uuid.UUID(payload["business_id"]),
                    )
                else:
                    # Uniform 404 on any failure (expired, invalid, missing)
                    self.record_success()
                    raise NotFoundException(
                        code="INVALID_PAY_TOKEN",
                        message="Invalid or expired payment link.",
                    )
        except NotFoundException:
            raise
        except (httpx.RequestError, httpx.TimeoutException):
            self.record_failure()
            # Uniform 404 as required by Section 7 ("uniform 404 on any failure")
            raise NotFoundException(
                code="INVALID_PAY_TOKEN",
                message="Invalid or expired payment link.",
            )


# Singleton
_invoice_client = InvoiceClient()


def get_invoice_client() -> InvoiceClient:
    return _invoice_client
