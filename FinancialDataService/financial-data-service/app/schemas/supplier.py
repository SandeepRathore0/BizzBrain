from datetime import datetime
from typing import Any, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class SupplierCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=30)
    address: Optional[dict[str, Any]] = None


class SupplierUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=30)
    address: Optional[dict[str, Any]] = None


class SupplierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    supplier_id: uuid.UUID
    business_id: uuid.UUID
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[dict[str, Any]] = None
    is_active: bool
    created_by: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime
