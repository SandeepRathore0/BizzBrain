from decimal import Decimal
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
from app.core.money import validate_not_float


class TaxRateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    rate: Decimal = Field(..., ge=Decimal("0.0000"), le=Decimal("1.0000"))

    @field_validator("rate", mode="before")
    @classmethod
    def validate_rate(cls, v):
        return validate_not_float(v)


class TaxRateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    is_active: Optional[bool] = None


class TaxRateResponse(BaseModel):
    tax_rate_id: UUID
    business_id: UUID
    name: str
    rate: str
    is_active: bool

    @classmethod
    def from_orm_model(cls, model):
        return cls(
            tax_rate_id=model.tax_rate_id,
            business_id=model.business_id,
            name=model.name,
            rate=f"{model.rate:.4f}",
            is_active=model.is_active,
        )

    class Config:
        from_attributes = True
