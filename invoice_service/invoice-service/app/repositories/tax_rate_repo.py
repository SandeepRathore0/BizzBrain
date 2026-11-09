from decimal import Decimal
from typing import List, Optional
from uuid import UUID, uuid4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.invoice import TaxRate


async def list_tax_rates(
    session: AsyncSession,
    business_id: UUID,
    active_only: bool = False,
) -> List[TaxRate]:
    stmt = select(TaxRate).where(TaxRate.business_id == business_id)
    if active_only:
        stmt = stmt.where(TaxRate.is_active.is_(True))
    stmt = stmt.order_by(TaxRate.rate.asc(), TaxRate.name.asc())
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def get_tax_rate_by_id(
    session: AsyncSession,
    business_id: UUID,
    tax_rate_id: UUID,
) -> Optional[TaxRate]:
    stmt = select(TaxRate).where(
        TaxRate.business_id == business_id,
        TaxRate.tax_rate_id == tax_rate_id,
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def get_active_tax_rate_by_rate(
    session: AsyncSession,
    business_id: UUID,
    rate: Decimal,
) -> Optional[TaxRate]:
    """Finds an active tax rate matching the given rate fraction for this business."""
    stmt = select(TaxRate).where(
        TaxRate.business_id == business_id,
        TaxRate.rate == rate,
        TaxRate.is_active.is_(True),
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def create_tax_rate(
    session: AsyncSession,
    business_id: UUID,
    name: str,
    rate: Decimal,
) -> TaxRate:
    tax_rate = TaxRate(
        tax_rate_id=uuid4(),
        business_id=business_id,
        name=name,
        rate=rate,
        is_active=True,
    )
    session.add(tax_rate)
    await session.flush()
    return tax_rate


async def update_tax_rate(
    session: AsyncSession,
    business_id: UUID,
    tax_rate_id: UUID,
    name: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> Optional[TaxRate]:
    tax_rate = await get_tax_rate_by_id(session, business_id, tax_rate_id)
    if not tax_rate:
        return None

    if name is not None:
        tax_rate.name = name
    if is_active is not None:
        tax_rate.is_active = is_active

    await session.flush()
    return tax_rate
