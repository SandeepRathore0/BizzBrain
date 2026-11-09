import base64
from datetime import date, datetime
from typing import List, Optional, Tuple
from uuid import UUID
from sqlalchemy import select, and_, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.models.invoice import Invoice, InvoiceItem, PaymentAllocation


def _encode_cursor(created_at: datetime, invoice_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{invoice_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")


def _decode_cursor(cursor_str: str) -> Optional[Tuple[datetime, UUID]]:
    try:
        decoded = base64.urlsafe_b64decode(cursor_str.encode("utf-8")).decode("utf-8")
        dt_str, id_str = decoded.split("|", 1)
        return datetime.fromisoformat(dt_str), UUID(id_str)
    except Exception:
        return None


async def get_invoice_by_id(
    session: AsyncSession,
    business_id: UUID,
    invoice_id: UUID,
    for_update: bool = False,
) -> Optional[Invoice]:
    """Retrieves an invoice scoped strictly by business_id.
    Optionally acquires SELECT ... FOR UPDATE row lock for atomic mutations.
    """
    stmt = (
        select(Invoice)
        .where(
            Invoice.business_id == business_id,
            Invoice.invoice_id == invoice_id,
        )
        .options(
            selectinload(Invoice.items),
            selectinload(Invoice.allocations),
        )
    )
    if for_update:
        stmt = stmt.with_for_update()

    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def get_invoice_by_pay_token_hash(
    session: AsyncSession,
    token_hash: str,
) -> Optional[Invoice]:
    """Retrieves an invoice by pay_token_hash (used by internal C4 resolve-pay-token)."""
    stmt = (
        select(Invoice)
        .where(Invoice.pay_token_hash == token_hash)
        .options(selectinload(Invoice.allocations))
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def get_invoice_by_id_any_business(
    session: AsyncSession,
    invoice_id: UUID,
    for_update: bool = False,
) -> Optional[Invoice]:
    """Retrieves an invoice without business filter to detect business mismatch in event handler."""
    stmt = (
        select(Invoice)
        .where(Invoice.invoice_id == invoice_id)
        .options(
            selectinload(Invoice.items),
            selectinload(Invoice.allocations),
        )
    )
    if for_update:
        stmt = stmt.with_for_update()
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def create_invoice(
    session: AsyncSession,
    business_id: UUID,
    invoice: Invoice,
) -> Invoice:
    """Inserts a new draft invoice with its items for the specified business."""
    invoice.business_id = business_id
    session.add(invoice)
    await session.flush()
    return invoice


async def delete_draft_items(
    session: AsyncSession,
    business_id: UUID,
    invoice_id: UUID,
) -> None:
    """Deletes existing line items for a draft invoice before replacing them."""
    invoice = await get_invoice_by_id(session, business_id, invoice_id)
    if invoice and invoice.items:
        for item in list(invoice.items):
            await session.delete(item)
        await session.flush()


async def list_invoices(
    session: AsyncSession,
    business_id: UUID,
    limit: int = 25,
    cursor: Optional[str] = None,
    status: Optional[str] = None,
    customer_id: Optional[UUID] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    overdue_only: bool = False,
    business_local_date: Optional[date] = None,
    search: Optional[str] = None,
) -> Tuple[List[Invoice], Optional[str]]:
    """Cursor-paginated listing of invoices strictly scoped by business_id.
    Ordered by created_at DESC, invoice_id DESC.
    """
    safe_limit = min(max(1, limit), 100)
    conditions = [Invoice.business_id == business_id]

    if status:
        conditions.append(Invoice.status == status)
    if customer_id:
        conditions.append(Invoice.customer_id == customer_id)
    if from_date:
        conditions.append(Invoice.issue_date >= from_date)
    if to_date:
        conditions.append(Invoice.issue_date <= to_date)
    if search:
        conditions.append(Invoice.invoice_number.ilike(f"%{search.strip()}%"))

    if overdue_only:
        conditions.append(Invoice.status.in_(["ISSUED", "PARTIALLY_PAID"]))
        conditions.append(Invoice.total_amount > Invoice.amount_paid)
        if business_local_date:
            conditions.append(Invoice.due_date < business_local_date)

    if cursor:
        decoded = _decode_cursor(cursor)
        if decoded:
            cursor_dt, cursor_id = decoded
            # Row value comparison: (created_at, invoice_id) < (cursor_dt, cursor_id)
            conditions.append(
                tuple_(Invoice.created_at, Invoice.invoice_id) < (cursor_dt, cursor_id)
            )

    stmt = (
        select(Invoice)
        .where(and_(*conditions))
        .options(
            selectinload(Invoice.items),
            selectinload(Invoice.allocations),
        )
        .order_by(Invoice.created_at.desc(), Invoice.invoice_id.desc())
        .limit(safe_limit + 1)
    )

    result = await session.execute(stmt)
    invoices = list(result.scalars().all())

    next_cursor = None
    if len(invoices) > safe_limit:
        next_record = invoices.pop()
        last_item = invoices[-1]
        next_cursor = _encode_cursor(last_item.created_at, last_item.invoice_id)

    return invoices, next_cursor


async def get_invoice_allocations(
    session: AsyncSession,
    business_id: UUID,
    invoice_id: UUID,
) -> List[PaymentAllocation]:
    """Retrieves all payment allocations for an invoice, strictly scoped by business_id."""
    stmt = (
        select(PaymentAllocation)
        .join(Invoice, PaymentAllocation.invoice_id == Invoice.invoice_id)
        .where(
            Invoice.business_id == business_id,
            Invoice.invoice_id == invoice_id,
        )
        .order_by(PaymentAllocation.allocated_at.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
