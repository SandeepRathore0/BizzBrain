from datetime import datetime, timezone
from typing import Any, Optional
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import AuditLog


class AuditRepository:
    @staticmethod
    async def log_action(
        session: AsyncSession,
        business_id: Optional[uuid.UUID],
        actor_type: str,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID,
        request_id: str,
        before: Optional[dict[str, Any]] = None,
        after: Optional[dict[str, Any]] = None,
    ) -> AuditLog:
        entry = AuditLog(
            audit_id=uuid.uuid4(),
            business_id=business_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=before,
            after=after,
            request_id=request_id,
            occurred_at=datetime.now(timezone.utc),
        )
        session.add(entry)
        return entry
