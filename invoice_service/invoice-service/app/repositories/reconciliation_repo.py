import base64
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4
from sqlalchemy import and_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.invoice import ReconciliationException
from app.models.base import utc_now


def _encode_cursor(created_at: datetime, exception_id: UUID) -> str:
    payload = f"{created_at.isoformat()}|{str(exception_id)}"
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("utf-8")


def _decode_cursor(cursor: str) -> Optional[Tuple[datetime, UUID]]:
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8")
        dt_str, id_str = decoded.split("|")
        return datetime.fromisoformat(dt_str), UUID(id_str)
    except Exception:
        return None


async def create_reconciliation_exception(
    session: AsyncSession,
    business_id: UUID,
    event_id: UUID,
    payment_id: UUID,
    reason_code: str,
    details: Dict[str, Any],
    invoice_id: Optional[UUID] = None,
) -> ReconciliationException:
    """Records a reconciliation exception strictly scoped by business_id."""
    exc = ReconciliationException(
        exception_id=uuid4(),
        business_id=business_id,
        event_id=event_id,
        payment_id=payment_id,
        invoice_id=invoice_id,
        reason_code=reason_code,
        details=details,
        status="OPEN",
        created_at=utc_now(),
    )
    session.add(exc)
    await session.flush()
    return exc


async def list_reconciliation_exceptions(
    session: AsyncSession,
    business_id: UUID,
    status: Optional[str] = None,
    limit: int = 25,
    cursor: Optional[str] = None,
) -> Tuple[List[ReconciliationException], Optional[str]]:
    """Cursor-paginated listing of reconciliation exceptions strictly filtered by business_id."""
    safe_limit = min(max(1, limit), 100)
    conditions = [ReconciliationException.business_id == business_id]

    if status:
        conditions.append(ReconciliationException.status == status)

    if cursor:
        decoded = _decode_cursor(cursor)
        if decoded:
            cursor_dt, cursor_id = decoded
            conditions.append(
                tuple_(ReconciliationException.created_at, ReconciliationException.exception_id)
                < (cursor_dt, cursor_id)
            )

    stmt = (
        select(ReconciliationException)
        .where(and_(*conditions))
        .order_by(
            ReconciliationException.created_at.desc(),
            ReconciliationException.exception_id.desc(),
        )
        .limit(safe_limit + 1)
    )

    res = await session.execute(stmt)
    records = list(res.scalars().all())

    next_cursor = None
    if len(records) > safe_limit:
        records.pop()
        last_item = records[-1]
        next_cursor = _encode_cursor(last_item.created_at, last_item.exception_id)

    return records, next_cursor


async def get_reconciliation_exception(
    session: AsyncSession,
    business_id: UUID,
    exception_id: UUID,
) -> Optional[ReconciliationException]:
    stmt = select(ReconciliationException).where(
        ReconciliationException.business_id == business_id,
        ReconciliationException.exception_id == exception_id,
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def resolve_reconciliation_exception(
    session: AsyncSession,
    business_id: UUID,
    exception_id: UUID,
    resolved_by: UUID,
    resolution_note: str,
) -> Optional[ReconciliationException]:
    """Resolves an open reconciliation exception within the business boundary."""
    exc = await get_reconciliation_exception(session, business_id, exception_id)
    if not exc:
        return None

    exc.status = "RESOLVED"
    exc.resolved_by = resolved_by
    exc.resolved_at = utc_now()
    exc.resolution_note = resolution_note
    await session.flush()
    return exc
