from typing import Optional
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import ExpenseReceipt


class ReceiptRepository:
    @staticmethod
    async def create(
        session: AsyncSession,
        receipt: ExpenseReceipt,
    ) -> ExpenseReceipt:
        session.add(receipt)
        await session.flush()
        return receipt

    @staticmethod
    async def get_by_expense_id(
        session: AsyncSession,
        business_id: uuid.UUID,
        expense_id: uuid.UUID,
    ) -> Optional[ExpenseReceipt]:
        stmt = (
            select(ExpenseReceipt)
            .where(
                ExpenseReceipt.business_id == business_id,
                ExpenseReceipt.expense_id == expense_id,
            )
            .order_by(ExpenseReceipt.uploaded_at.desc())
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        business_id: uuid.UUID,
        receipt_id: uuid.UUID,
    ) -> Optional[ExpenseReceipt]:
        stmt = select(ExpenseReceipt).where(
            ExpenseReceipt.business_id == business_id,
            ExpenseReceipt.receipt_id == receipt_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
