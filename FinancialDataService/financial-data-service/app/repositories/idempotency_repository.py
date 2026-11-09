from datetime import datetime, timezone
from typing import Any, Optional
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import IdempotencyKey


class IdempotencyRepository:
    @staticmethod
    async def get_key(
        session: AsyncSession,
        business_id: uuid.UUID,
        key: uuid.UUID,
        endpoint: str,
    ) -> Optional[IdempotencyKey]:
        stmt = select(IdempotencyKey).where(
            IdempotencyKey.business_id == business_id,
            IdempotencyKey.key == key,
            IdempotencyKey.endpoint == endpoint,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def save_key(
        session: AsyncSession,
        business_id: uuid.UUID,
        key: uuid.UUID,
        endpoint: str,
        request_hash: str,
        response_status: int,
        response_body: dict[str, Any],
    ) -> IdempotencyKey:
        record = IdempotencyKey(
            business_id=business_id,
            key=key,
            endpoint=endpoint,
            request_hash=request_hash,
            response_status=response_status,
            response_body=response_body,
            created_at=datetime.now(timezone.utc),
        )
        session.add(record)
        await session.flush()
        return record
