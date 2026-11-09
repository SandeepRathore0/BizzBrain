from datetime import date
from decimal import Decimal
from typing import List, Optional, Tuple
import uuid
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import Expense


class ExpenseRepository:
    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        business_id: uuid.UUID,
        expense_id: uuid.UUID,
        for_update: bool = False,
    ) -> Optional[Expense]:
        stmt = select(Expense).where(
            Expense.business_id == business_id,
            Expense.expense_id == expense_id,
        )
        if for_update:
            stmt = stmt.with_for_update()

        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def create(
        session: AsyncSession,
        expense: Expense,
    ) -> Expense:
        session.add(expense)
        await session.flush()
        return expense

    @staticmethod
    async def list_expenses(
        session: AsyncSession,
        business_id: uuid.UUID,
        created_by: Optional[uuid.UUID] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        category_id: Optional[uuid.UUID] = None,
        supplier_id: Optional[uuid.UUID] = None,
        status: Optional[str] = None,
        min_amount: Optional[Decimal] = None,
        max_amount: Optional[Decimal] = None,
        limit: int = 25,
        cursor_date: Optional[date] = None,
        cursor_id: Optional[uuid.UUID] = None,
    ) -> Tuple[List[Expense], bool]:
        stmt = select(Expense).where(Expense.business_id == business_id)

        # Employee repository filter: only own expenses
        if created_by is not None:
            stmt = stmt.where(Expense.created_by == created_by)

        if from_date is not None:
            stmt = stmt.where(Expense.expense_date >= from_date)

        if to_date is not None:
            stmt = stmt.where(Expense.expense_date <= to_date)

        if category_id is not None:
            stmt = stmt.where(Expense.category_id == category_id)

        if supplier_id is not None:
            stmt = stmt.where(Expense.supplier_id == supplier_id)

        if status is not None:
            stmt = stmt.where(Expense.status == status.upper())

        if min_amount is not None:
            stmt = stmt.where(Expense.amount >= min_amount)

        if max_amount is not None:
            stmt = stmt.where(Expense.amount <= max_amount)

        if cursor_date and cursor_id:
            stmt = stmt.where(
                or_(
                    Expense.expense_date < cursor_date,
                    and_(
                        Expense.expense_date == cursor_date,
                        Expense.expense_id < cursor_id,
                    ),
                )
            )

        stmt = (
            stmt.order_by(Expense.expense_date.desc(), Expense.expense_id.desc())
            .limit(limit + 1)
        )

        result = await session.execute(stmt)
        rows = list(result.scalars().all())

        has_next = len(rows) > limit
        items = rows[:limit]
        return items, has_next
