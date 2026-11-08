from datetime import datetime
from typing import List, Optional, Tuple
import uuid
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import Customer


class CustomerRepository:
    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        business_id: uuid.UUID,
        customer_id: uuid.UUID,
    ) -> Optional[Customer]:
        stmt = select(Customer).where(
            Customer.business_id == business_id,
            Customer.customer_id == customer_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def find_active_by_name_and_email(
        session: AsyncSession,
        business_id: uuid.UUID,
        name: str,
        email: Optional[str],
    ) -> Optional[Customer]:
        stmt = select(Customer).where(
            Customer.business_id == business_id,
            Customer.is_active.is_(True),
            func.lower(Customer.name) == func.lower(name.strip()),
        )
        if email:
            stmt = stmt.where(func.lower(Customer.email) == func.lower(email.strip()))
        else:
            stmt = stmt.where(Customer.email.is_(None))

        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_customers(
        session: AsyncSession,
        business_id: uuid.UUID,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
        limit: int = 25,
        cursor_created_at: Optional[datetime] = None,
        cursor_id: Optional[uuid.UUID] = None,
    ) -> Tuple[List[Customer], bool]:
        # Fetch limit + 1 to check if there is a next page
        stmt = select(Customer).where(Customer.business_id == business_id)

        if is_active is not None:
            stmt = stmt.where(Customer.is_active == is_active)

        if search:
            search_pattern = f"%{search.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(Customer.name).like(search_pattern),
                    func.lower(Customer.email).like(search_pattern),
                )
            )

        if cursor_created_at and cursor_id:
            stmt = stmt.where(
                or_(
                    Customer.created_at < cursor_created_at,
                    and_(
                        Customer.created_at == cursor_created_at,
                        Customer.customer_id < cursor_id,
                    ),
                )
            )

        stmt = stmt.order_by(Customer.created_at.desc(), Customer.customer_id.desc()).limit(limit + 1)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())

        has_next = len(rows) > limit
        items = rows[:limit]
        return items, has_next

    @staticmethod
    async def create(session: AsyncSession, customer: Customer) -> Customer:
        session.add(customer)
        await session.flush()
        return customer
