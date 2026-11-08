from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
from app.core.money import format_money, validate_not_float


class PayableDetailsResponse(BaseModel):
    invoice_id: UUID
    business_id: UUID
    invoice_number: Optional[str]
    total_amount: str
    amount_paid: str
    outstanding_balance: str
    currency: str
    status: str
    min_installment_amount: Optional[str]
    payable: bool

    @classmethod
    def from_invoice(cls, inv, payable: bool):
        outstanding = max(Decimal("0.00"), inv.total_amount - inv.amount_paid)
        return cls(
            invoice_id=inv.invoice_id,
            business_id=inv.business_id,
            invoice_number=inv.invoice_number,
            total_amount=format_money(inv.total_amount),
            amount_paid=format_money(inv.amount_paid),
            outstanding_balance=format_money(outstanding),
            currency=inv.currency,
            status=inv.status,
            min_installment_amount=format_money(inv.min_installment_amount) if inv.min_installment_amount is not None else None,
            payable=payable,
        )


class ResolvePayTokenRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=128)


class ResolvePayTokenResponse(BaseModel):
    invoice_id: UUID
    business_id: UUID


class PaymentEventRequest(BaseModel):
    event_id: UUID
    event_type: str = Field(..., description="Must be PaymentCaptured")
    event_version: int = Field(default=1)
    occurred_at: datetime
    request_id: str
    business_id: UUID
    payment_id: UUID
    invoice_id: UUID
    amount: Decimal
    currency: str = Field(..., min_length=3, max_length=3)
    provider_capture_id: Optional[str] = None

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount_not_float(cls, v):
        return validate_not_float(v)

    @field_validator("currency")
    @classmethod
    def validate_currency_upper(cls, v: str) -> str:
        return v.upper()


class PaymentEventResponse(BaseModel):
    outcome: str  # APPLIED, DUPLICATE_IGNORED, REJECTED
    event_id: UUID
    reason_code: Optional[str] = None
    invoice_id: Optional[UUID] = None
    status: Optional[str] = None
    outstanding_balance: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
