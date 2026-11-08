import hashlib
import json
from typing import Any, Dict, Optional, Tuple
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.errors import IdempotencyException
from app.models.base import utc_now
from app.models.invoice import IdempotencyKey


def compute_request_hash(data: Any) -> str:
    """Computes SHA-256 hash of serialized request data."""
    if isinstance(data, (dict, list)):
        serialized = json.dumps(data, sort_keys=True, default=str)
    elif isinstance(data, bytes):
        serialized = data.decode("utf-8", errors="replace")
    else:
        serialized = str(data)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def check_idempotency_key(
    session: AsyncSession,
    business_id: UUID,
    key: str,
    endpoint: str,
    request_data: Any,
) -> Optional[Tuple[int, Dict[str, Any]]]:
    """Checks if an idempotency key was previously processed.
    Returns (status_code, response_body) if matching key exists.
    Raises IdempotencyException (422) if same key is reused with different request payload.
    Returns None if key has not been seen before.
    """
    request_hash = compute_request_hash(request_data)

    stmt = select(IdempotencyKey).where(
        IdempotencyKey.business_id == business_id,
        IdempotencyKey.key == key,
        IdempotencyKey.endpoint == endpoint,
    )
    res = await session.execute(stmt)
    existing = res.scalar_one_or_none()

    if existing:
        if existing.request_hash != request_hash:
            raise IdempotencyException("Idempotency key reused with different request payload")
        return existing.response_status, existing.response_body

    return None


async def save_idempotency_key(
    session: AsyncSession,
    business_id: UUID,
    key: str,
    endpoint: str,
    request_data: Any,
    response_status: int,
    response_body: Dict[str, Any],
) -> None:
    """Stores the idempotency record for 24-hour replay."""
    request_hash = compute_request_hash(request_data)
    entry = IdempotencyKey(
        business_id=business_id,
        key=key,
        endpoint=endpoint,
        request_hash=request_hash,
        response_status=response_status,
        response_body=response_body,
        created_at=utc_now(),
    )
    session.add(entry)
    await session.flush()
