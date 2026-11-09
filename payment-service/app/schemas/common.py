from typing import Any, Generic, Optional, TypeVar
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class APIErrorDetails(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None


class APIErrorResponse(BaseModel):
    success: bool = False
    error: APIErrorDetails
    request_id: str


class APISuccessResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    success: bool = True
    data: T
    request_id: str


class CursorPaginationParams(BaseModel):
    limit: int = Field(default=25, ge=1, le=100)
    cursor: Optional[str] = None


class CursorPageResponse(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: Optional[str] = None
    has_more: bool = False
