from typing import Any, Generic, Optional, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class SuccessEnvelope(BaseModel, Generic[T]):
    success: bool = True
    data: T
    request_id: str
