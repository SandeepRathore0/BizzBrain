"""Common Pydantic request and response envelope models."""

from typing import Any, Generic, List, Optional, TypeVar
from pydantic import BaseModel, ConfigDict, Field

DataT = TypeVar("DataT")


class SuccessEnvelope(BaseModel, Generic[DataT]):
    """Standard success API response envelope."""
    model_config = ConfigDict(from_attributes=True)

    success: bool = True
    data: DataT
    request_id: str = Field(..., description="Unique request tracing ID")


class ErrorDetail(BaseModel):
    """Detailed error item in the error envelope."""
    code: Optional[str] = None
    message: str
    field: Optional[str] = None
    type: Optional[str] = None


class ErrorBody(BaseModel):
    """Body of the error envelope."""
    code: str
    message: str
    details: List[ErrorDetail] = Field(default_factory=list)


class ErrorEnvelope(BaseModel):
    """Standard error API response envelope."""
    success: bool = False
    error: ErrorBody
    request_id: str = Field(..., description="Unique request tracing ID")


class CursorPaginationParams(BaseModel):
    """Parameters for cursor-based pagination."""
    limit: int = Field(default=25, ge=1, le=100, description="Items per page (1-100)")
    cursor: Optional[str] = Field(default=None, description="Opaque cursor token for next page")


class CursorPage(BaseModel, Generic[DataT]):
    """Cursor-paginated collection."""
    items: List[DataT]
    next_cursor: Optional[str] = None
    has_more: bool = False
