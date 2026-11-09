import hashlib
import json
import uuid
from typing import Any, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableEntityException
from app.models.idempotency import IdempotencyKey


def compute_request_hash(payload: Any) -> str:
    """Compute deterministic SHA-256 hex digest of request payload."""
    if payload is None:
        canonical = ""
    elif isinstance(payload, bytes):
        canonical = payload.decode("utf-8")
    elif isinstance(payload, str):
        canonical = payload.strip()
    else:
        # Sort keys for deterministic JSON serialization
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def check_idempotency(
    db: AsyncSession,
    business_id: uuid.UUID,
    key: str,
    endpoint: str,
    payload: Any,
) -> Tuple[bool, Optional[int], Optional[dict[str, Any]]]:
    """Check if an idempotency key was previously processed.
    
    Returns (is_replayed, status_code, response_body).
    Raises UnprocessableEntityException with IDEMPOTENCY_KEY_REUSED if payload hash differs.
    """
    stmt = select(IdempotencyKey).where(
        IdempotencyKey.business_id == business_id,
        IdempotencyKey.key == key,
        IdempotencyKey.endpoint == endpoint,
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if not existing:
        return False, None, None

    request_hash = compute_request_hash(payload)
    if existing.request_hash != request_hash:
        raise UnprocessableEntityException(
            code="IDEMPOTENCY_KEY_REUSED",
            message="Idempotency key was previously used with a different request payload.",
            details={"key": key, "endpoint": endpoint},
        )

    return True, existing.response_status, existing.response_body


async def record_idempotency(
    db: AsyncSession,
    business_id: uuid.UUID,
    key: str,
    endpoint: str,
    payload: Any,
    response_status: int,
    response_body: dict[str, Any],
) -> None:
    """Record completed response for an idempotency key in the current transaction."""
    request_hash = compute_request_hash(payload)
    item = IdempotencyKey(
        business_id=business_id,
        key=key,
        endpoint=endpoint,
        request_hash=request_hash,
        response_status=response_status,
        response_body=response_body,
    )
    db.add(item)
