import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    payment_id: uuid.UUID
    business_id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_number: Optional[str] = None
    amount: str
    currency: str
    provider: str
    status: str
    allocation_status: str
    status_reason: Optional[str] = None
    initiated_by_type: str
    initiated_by_id: Optional[uuid.UUID] = None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class OrderCreationResponse(BaseModel):
    payment_id: uuid.UUID
    approve_url: str
    status: str
    amount: str
    currency: str


class StaffOrderCreateRequest(BaseModel):
    invoice_id: uuid.UUID
    amount: Optional[str] = None


class PublicOrderCreateRequest(BaseModel):
    token: str
    amount: Optional[str] = None


class PublicPaySummaryResponse(BaseModel):
    invoice_number: Optional[str] = None
    business_name: str
    amount_due: str
    currency: str
    payable: bool


class CaptureResponse(BaseModel):
    payment_id: uuid.UUID
    status: str
    amount: str
    currency: str
    provider_capture_id: Optional[str] = None


class PublicCaptureRequest(BaseModel):
    token: str
