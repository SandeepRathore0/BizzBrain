from decimal import Decimal
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.invoice import PaymentAllocation


async def create_payment_allocation(
    session: AsyncSession,
    business_id: UUID,
    invoice_id: UUID,
    payment_id: UUID,
    event_id: UUID,
    allocated_amount: Decimal,
) -> PaymentAllocation:
    """Inserts a new payment allocation row."""
    allocation = PaymentAllocation(
        allocation_id=uuid4(),
        invoice_id=invoice_id,
        payment_id=payment_id,
        event_id=event_id,
        allocated_amount=allocated_amount,
    )
    session.add(allocation)
    await session.flush()
    return allocation
