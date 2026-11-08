from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class ExpenseCreate(BaseModel):
    description: str = Field(..., min_length=1, max_length=255)
    amount: Decimal = Field(...)
    currency: str = Field(..., min_length=3, max_length=3)
    expense_date: date = Field(...)
    category_id: Optional[uuid.UUID] = None
    supplier_id: Optional[uuid.UUID] = None

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount_not_float(cls, v: Any) -> Any:
        if isinstance(v, float):
            raise ValueError("Float values are forbidden for money. Provide a string or integer.")
        return v


class ExpensePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Optional[str] = Field(None, min_length=1, max_length=255)
    category_id: Optional[uuid.UUID] = None


class ExpenseVoidRequest(BaseModel):
    void_reason: str = Field(..., min_length=1, max_length=255)


class ExpenseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    expense_id: uuid.UUID
    business_id: uuid.UUID
    supplier_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None
    description: str
    amount: Decimal
    currency: str
    expense_date: date
    receipt_file_key: Optional[str] = None
    status: str
    created_by: uuid.UUID
    created_at: datetime
    voided_by: Optional[uuid.UUID] = None
    voided_at: Optional[datetime] = None
    void_reason: Optional[str] = None
    idempotency_key: uuid.UUID

    @field_serializer("amount")
    def serialize_amount(self, value: Decimal) -> str:
        return f"{value:.2f}"
