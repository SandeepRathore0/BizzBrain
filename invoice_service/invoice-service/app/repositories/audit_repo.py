from typing import Any, Dict, Optional
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.invoice import AuditLog
from app.models.base import utc_now


async def record_audit_log(
    session: AsyncSession,
    business_id: Optional[UUID],
    actor_type: str,
    actor_id: str,
    action: str,
    entity_type: str,
    entity_id: UUID,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
) -> AuditLog:
    """Writes an append-only audit_log record in the same database transaction.
    Sensitive credentials are never stored.
    """
    audit_entry = AuditLog(
        audit_id=uuid4(),
        business_id=business_id,
        actor_type=actor_type,
        actor_id=str(actor_id),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
        request_id=request_id,
        occurred_at=utc_now(),
    )
    session.add(audit_entry)
    return audit_entry
