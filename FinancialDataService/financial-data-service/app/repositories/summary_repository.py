from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
import uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import Expense, ExpenseCategory


class SummaryRepository:
    @staticmethod
    async def get_expense_summary(
        session: AsyncSession,
        business_id: uuid.UUID,
        from_date: date,
        to_date: date,
    ) -> Dict[str, Any]:
        """
        Aggregate posted expenses for a business within the date range.
        Excludes VOID expenses.
        Returns:
            - total_amount: Decimal
            - total_count: int
            - by_category: list of dicts {category_id, category_name, total_amount, count}
            - by_month: list of dicts {month, total_amount, count}
        """
        # 1. Total amount and count
        total_stmt = (
            select(
                func.coalesce(func.sum(Expense.amount), Decimal("0")),
                func.count(Expense.expense_id),
            )
            .where(
                Expense.business_id == business_id,
                Expense.status == "POSTED",
                Expense.expense_date >= from_date,
                Expense.expense_date <= to_date,
            )
        )
        total_result = await session.execute(total_stmt)
        total_amount, total_count = total_result.one()
        total_amount = Decimal(str(total_amount))

        # 2. Aggregation by Category
        cat_stmt = (
            select(
                Expense.category_id,
                ExpenseCategory.category_name,
                func.coalesce(func.sum(Expense.amount), Decimal("0")),
                func.count(Expense.expense_id),
            )
            .outerjoin(
                ExpenseCategory,
                Expense.category_id == ExpenseCategory.category_id,
            )
            .where(
                Expense.business_id == business_id,
                Expense.status == "POSTED",
                Expense.expense_date >= from_date,
                Expense.expense_date <= to_date,
            )
            .group_by(Expense.category_id, ExpenseCategory.category_name)
            .order_by(func.sum(Expense.amount).desc())
        )
        cat_result = await session.execute(cat_stmt)
        by_category = []
        for cat_id, cat_name, amt, cnt in cat_result.all():
            by_category.append({
                "category_id": cat_id,
                "category_name": cat_name or "Uncategorized",
                "total_amount": f"{Decimal(str(amt)):.2f}",
                "count": cnt,
            })

        # 3. Aggregation by Month (using date from posted expenses)
        month_stmt = (
            select(
                Expense.expense_date,
                Expense.amount,
            )
            .where(
                Expense.business_id == business_id,
                Expense.status == "POSTED",
                Expense.expense_date >= from_date,
                Expense.expense_date <= to_date,
            )
            .order_by(Expense.expense_date.asc())
        )
        month_result = await session.execute(month_stmt)
        monthly_map: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"total": Decimal("0"), "count": 0})
        for exp_date, amt in month_result.all():
            month_key = exp_date.strftime("%Y-%m")
            monthly_map[month_key]["total"] += Decimal(str(amt))
            monthly_map[month_key]["count"] += 1

        by_month = []
        for month_key in sorted(monthly_map.keys()):
            by_month.append({
                "month": month_key,
                "total_amount": f"{monthly_map[month_key]['total']:.2f}",
                "count": monthly_map[month_key]["count"],
            })

        return {
            "total_amount": f"{total_amount:.2f}",
            "total_count": total_count,
            "by_category": by_category,
            "by_month": by_month,
        }
