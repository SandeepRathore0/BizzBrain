from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field, field_validator, model_validator
from app.core.errors import ValidationException
from app.core.money import format_money, validate_not_float


class DraftItemInput(BaseModel):
    description: str = Field(..., min_length=1, max_length=255)
    quantity: Decimal = Field(..., gt=Decimal("0"))
    unit_price: Decimal = Field(..., ge=Decimal("0.0000"))
    tax_rate: Decimal = Field(..., ge=Decimal("0.0000"), le=Decimal("1.0000"))

    @field_validator("quantity", "unit_price", "tax_rate", mode="before")
    @classmethod
    def validate_no_float(cls, v):
        return validate_not_float(v)

    @model_validator(mode="before")
    @classmethod
    def reject_client_totals(cls, values):
        if isinstance(values, dict):
            for forbidden in ("line_subtotal", "line_tax", "line_total"):
                if forbidden in values and values[forbidden] is not None:
                    raise ValidationException(
                        f"Client cannot supply calculated '{forbidden}'. Totals are backend calculated."
                    )
        return values


class InvoiceCreate(BaseModel):
    customer_id: UUID
    due_date: date
    currency: str = Field(..., min_length=3, max_length=3)
    items: List[DraftItemInput] = Field(..., min_length=1)
    notes: Optional[str] = Field(None, max_length=1000)
    min_installment_amount: Optional[Decimal] = Field(None, gt=Decimal("0"))

    @field_validator("min_installment_amount", mode="before")
    @classmethod
    def validate_min_installment(cls, v):
        return validate_not_float(v) if v is not None else None

    @field_validator("currency")
    @classmethod
    def validate_currency_uppercase(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="before")
    @classmethod
    def reject_client_totals(cls, values):
        if isinstance(values, dict):
            for forbidden in ("subtotal", "tax_amount", "total_amount", "amount_paid", "status", "invoice_number"):
                if forbidden in values and values[forbidden] is not None:
                    raise ValidationException(
                        f"Client cannot supply '{forbidden}'. Field is backend determined."
                    )
        return values


class InvoiceUpdate(BaseModel):
    customer_id: Optional[UUID] = None
    due_date: Optional[date] = None
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    items: Optional[List[DraftItemInput]] = None
    notes: Optional[str] = Field(None, max_length=1000)
    min_installment_amount: Optional[Decimal] = Field(None, gt=Decimal("0"))

    @field_validator("min_installment_amount", mode="before")
    @classmethod
    def validate_min_installment(cls, v):
        return validate_not_float(v) if v is not None else None

    @field_validator("currency")
    @classmethod
    def validate_currency_uppercase(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if v else None

    @model_validator(mode="before")
    @classmethod
    def reject_client_totals(cls, values):
        if isinstance(values, dict):
            for forbidden in ("subtotal", "tax_amount", "total_amount", "amount_paid", "status", "invoice_number"):
                if forbidden in values and values[forbidden] is not None:
                    raise ValidationException(
                        f"Client cannot supply '{forbidden}'. Field is backend determined."
                    )
        return values


class InvoiceItemResponse(BaseModel):
    item_id: UUID
    position: int
    description: str
    quantity: str
    unit_price: str
    tax_rate: str
    line_subtotal: str
    line_tax: str
    line_total: str

    @classmethod
    def from_orm(cls, item):
        return cls(
            item_id=item.item_id,
            position=item.position,
            description=item.description,
            quantity=f"{item.quantity:.4f}",
            unit_price=format_money(item.unit_price),
            tax_rate=f"{item.tax_rate:.4f}",
            line_subtotal=format_money(item.line_subtotal),
            line_tax=format_money(item.line_tax),
            line_total=format_money(item.line_total),
        )


class InvoiceResponse(BaseModel):
    invoice_id: UUID
    business_id: UUID
    customer_id: UUID
    invoice_number: Optional[str]
    status: str
    issue_date: Optional[date]
    due_date: date
    currency: str
    subtotal: str
    tax_amount: str
    total_amount: str
    amount_paid: str
    outstanding_balance: str
    is_overdue: bool
    notes: Optional[str]
    min_installment_amount: Optional[str]
    business_timezone: Optional[str]
    customer_snapshot: Optional[Dict[str, Any]]
    seller_snapshot: Optional[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    issued_at: Optional[datetime]
    voided_at: Optional[datetime]
    void_reason: Optional[str]
    items: List[InvoiceItemResponse]

    @classmethod
    def from_orm(cls, inv, is_overdue: bool = False):
        outstanding = max(Decimal("0.00"), inv.total_amount - inv.amount_paid)
        return cls(
            invoice_id=inv.invoice_id,
            business_id=inv.business_id,
            customer_id=inv.customer_id,
            invoice_number=inv.invoice_number,
            status=inv.status,
            issue_date=inv.issue_date,
            due_date=inv.due_date,
            currency=inv.currency,
            subtotal=format_money(inv.subtotal),
            tax_amount=format_money(inv.tax_amount),
            total_amount=format_money(inv.total_amount),
            amount_paid=format_money(inv.amount_paid),
            outstanding_balance=format_money(outstanding),
            is_overdue=is_overdue,
            notes=inv.notes,
            min_installment_amount=format_money(inv.min_installment_amount) if inv.min_installment_amount is not None else None,
            business_timezone=inv.business_timezone,
            customer_snapshot=inv.customer_snapshot,
            seller_snapshot=inv.seller_snapshot,
            created_at=inv.created_at,
            updated_at=inv.updated_at,
            issued_at=inv.issued_at,
            voided_at=inv.voided_at,
            void_reason=inv.void_reason,
            items=[InvoiceItemResponse.from_orm(item) for item in sorted(inv.items, key=lambda x: x.position)] if inv.items else [],
        )


class InvoiceIssueResponse(InvoiceResponse):
    pay_token: str

    @classmethod
    def from_orm_with_token(cls, inv, pay_token: str, is_overdue: bool = False):
        base_resp = InvoiceResponse.from_orm(inv, is_overdue=is_overdue)
        data = base_resp.model_dump()
        data["pay_token"] = pay_token
        return cls(**data)


class PayLinkResponse(BaseModel):
    invoice_id: UUID
    pay_token: str


class InvoiceVoidRequest(BaseModel):
    void_reason: str = Field(..., min_length=1, max_length=255)


class InvoiceBalanceResponse(BaseModel):
    invoice_id: UUID
    business_id: UUID
    currency: str
    total_amount: str
    amount_paid: str
    outstanding_balance: str
    status: str
    is_overdue: bool


class PaymentAllocationResponse(BaseModel):
    allocation_id: UUID
    invoice_id: UUID
    payment_id: UUID
    event_id: UUID
    allocated_amount: str
    allocated_at: datetime

    @classmethod
    def from_orm(cls, alloc):
        return cls(
            allocation_id=alloc.allocation_id,
            invoice_id=alloc.invoice_id,
            payment_id=alloc.payment_id,
            event_id=alloc.event_id,
            allocated_amount=format_money(alloc.allocated_amount),
            allocated_at=alloc.allocated_at,
        )


class PaginatedInvoicesResponse(BaseModel):
    items: List[InvoiceResponse]
    next_cursor: Optional[str] = None
