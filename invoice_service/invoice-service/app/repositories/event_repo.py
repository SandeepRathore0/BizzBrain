from typing import Optional
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.invoice import ProcessedEvent


async def get_processed_event(
    session: AsyncSession,
    event_id: UUID,
) -> Optional[ProcessedEvent]:
    stmt = select(ProcessedEvent).where(ProcessedEvent.event_id == event_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def record_processed_event(
    session: AsyncSession,
    event_id: UUID,
    event_type: str,
    outcome: str,
) -> bool:
    """Inserts into processed_events (inbox) with outcome 'APPLIED' or 'REJECTED'.
    Returns True if successfully inserted, or False if event_id already exists.
    """
    stmt = (
        insert(ProcessedEvent)
        .values(
            event_id=event_id,
            event_type=event_type,
            outcome=outcome,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(ProcessedEvent.event_id)
    )
    res = await session.execute(stmt)
    inserted_id = res.scalar_one_or_none()
    return inserted_id is not None
