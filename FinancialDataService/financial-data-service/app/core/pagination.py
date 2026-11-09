import base64
import json
from datetime import datetime
from typing import Any, Generic, List, Optional, Tuple, TypeVar
from pydantic import BaseModel
import uuid

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    next_cursor: Optional[str] = None
    limit: int


def encode_cursor(created_at: datetime, entity_id: uuid.UUID) -> str:
    payload = {
        "created_at": created_at.isoformat(),
        "id": str(entity_id),
    }
    dumped = json.dumps(payload)
    return base64.urlsafe_b64encode(dumped.encode("utf-8")).decode("utf-8")


def decode_cursor(cursor: str) -> Tuple[datetime, uuid.UUID]:
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8")
        payload = json.loads(decoded)
        created_at = datetime.fromisoformat(payload["created_at"])
        entity_id = uuid.UUID(payload["id"])
        return created_at, entity_id
    except Exception as e:
        raise ValueError("Invalid cursor format") from e
