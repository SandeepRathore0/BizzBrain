from typing import Any, Dict, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class InvoiceSettingsUpdate(BaseModel):
    invoice_prefix: Optional[str] = Field(None, max_length=10)
    seller_name: Optional[str] = Field(None, max_length=150)
    seller_address: Optional[Dict[str, Any]] = None
    seller_tax_id: Optional[str] = Field(None, max_length=50)
    default_due_days: Optional[int] = Field(None, ge=0, le=365)


class InvoiceSettingsResponse(BaseModel):
    business_id: UUID
    invoice_prefix: str
    seller_name: Optional[str]
    seller_address: Optional[Dict[str, Any]]
    seller_tax_id: Optional[str]
    default_due_days: int

    class Config:
        from_attributes = True
