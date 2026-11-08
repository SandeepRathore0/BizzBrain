from datetime import datetime
from typing import List, Optional, Tuple
import uuid
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import Supplier


class SupplierRepository:
    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        business_id: uuid.UUID,
        supplier_id: uuid.UUID,
    ) -> Optional[Supplier]:
        stmt = select(Supplier).where(
            Supplier.business_id == business_id,
            Supplier.supplier_id == supplier_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_suppliers(
        session: AsyncSession,
        business_id: uuid.UUID,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
        limit: int = 25,
        cursor_created_at: Optional[datetime] = None,
        cursor_id: Optional[uuid.UUID] = None,
    ) -> Tuple[List[Supplier], bool]:
        stmt = select(Supplier).where(Supplier.business_id == business_id)

        if is_active is not None:
            stmt = stmt.where(Supplier.is_active == is_active)

        if search:
            search_pattern = f"%{search.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(Supplier.name).like(search_pattern),
                    func.lower(Supplier.email).like(search_pattern),
                )
            )

        if cursor_created_at and cursor_id:
            stmt = stmt.where(
                or_(
                    Supplier.created_at < cursor_created_at,
                    and_(
                        Supplier.created_at == cursor_created_at,
                        Supplier.supplier_id < cursor_id,
                    ),
                )
            )

        stmt = stmt.order_by(Supplier.created_at.desc(), Supplier.supplier_id.desc()).limit(limit + 1)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())

        has_next = len(rows) > limit
        items = rows[:limit]
        return items, has_next

    @staticmethod
    async def create(session: AsyncSession, supplier: Supplier) -> Supplier:
        session.add(supplier)
        await session.flush()
        return supplier
