from datetime import datetime
from typing import Any, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class CustomerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=30)
    billing_address: Optional[dict[str, Any]] = None
    tax_id: Optional[str] = Field(None, max_length=50)


class CustomerUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=30)
    billing_address: Optional[dict[str, Any]] = None
    tax_id: Optional[str] = Field(None, max_length=50)


class CustomerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_id: uuid.UUID
    business_id: uuid.UUID
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    billing_address: Optional[dict[str, Any]] = None
    tax_id: Optional[str] = None
    is_active: bool
    created_by: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime
