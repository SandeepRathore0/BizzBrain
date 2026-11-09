from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class ReconciliationExceptionResponse(BaseModel):
    exception_id: UUID
    business_id: UUID
    event_id: UUID
    payment_id: UUID
    invoice_id: Optional[UUID] = None
    reason_code: str
    details: Optional[Dict[str, Any]] = None
    status: str
    resolved_by: Optional[UUID] = None
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedReconciliationExceptionsResponse(BaseModel):
    items: List[ReconciliationExceptionResponse]
    next_cursor: Optional[str] = None
    has_more: bool = False


class ResolveExceptionRequest(BaseModel):
    resolution_note: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Reason or notes explaining how this reconciliation exception was resolved",
    )
