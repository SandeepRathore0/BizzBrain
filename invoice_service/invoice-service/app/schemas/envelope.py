from typing import Any, Generic, List, Optional, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    field: Optional[str] = None
    message: str


class ErrorPayload(BaseModel):
    code: str
    message: str
    details: List[Any] = Field(default_factory=list)


class SuccessResponse(BaseModel, Generic[T]):
    success: bool = True
    data: T
    request_id: str


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorPayload
    request_id: str
