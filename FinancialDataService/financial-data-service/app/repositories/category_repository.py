from typing import List, Optional
import uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import ExpenseCategory

DEFAULT_CATEGORIES = [
    "Salaries",
    "Software subscriptions",
    "Rent",
    "Marketing",
    "Travel",
    "Utilities",
    "Office supplies",
]


class CategoryRepository:
    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        business_id: uuid.UUID,
        category_id: uuid.UUID,
    ) -> Optional[ExpenseCategory]:
        stmt = select(ExpenseCategory).where(
            ExpenseCategory.business_id == business_id,
            ExpenseCategory.category_id == category_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_name(
        session: AsyncSession,
        business_id: uuid.UUID,
        name: str,
    ) -> Optional[ExpenseCategory]:
        stmt = select(ExpenseCategory).where(
            ExpenseCategory.business_id == business_id,
            func.lower(ExpenseCategory.category_name) == func.lower(name.strip()),
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_categories(
        session: AsyncSession,
        business_id: uuid.UUID,
    ) -> List[ExpenseCategory]:
        stmt = (
            select(ExpenseCategory)
            .where(ExpenseCategory.business_id == business_id)
            .order_by(ExpenseCategory.category_name.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def create(
        session: AsyncSession,
        category: ExpenseCategory,
    ) -> ExpenseCategory:
        session.add(category)
        await session.flush()
        return category

    @staticmethod
    async def seed_default_categories_if_empty(
        session: AsyncSession,
        business_id: uuid.UUID,
    ) -> None:
        # Check if any categories exist for this business
        stmt = select(func.count(ExpenseCategory.category_id)).where(
            ExpenseCategory.business_id == business_id
        )
        result = await session.execute(stmt)
        count = result.scalar_one()

        if count == 0:
            for cat_name in DEFAULT_CATEGORIES:
                category = ExpenseCategory(
                    category_id=uuid.uuid4(),
                    business_id=business_id,
                    category_name=cat_name,
                    is_active=True,
                )
                session.add(category)
            await session.flush()
