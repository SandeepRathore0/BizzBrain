from datetime import date
from typing import List, Optional
import uuid
from pydantic import BaseModel


class CategoryExpenseSummary(BaseModel):
    category_id: Optional[uuid.UUID] = None
    category_name: str
    total_amount: str
    count: int


class MonthlyExpenseSummary(BaseModel):
    month: str  # YYYY-MM
    total_amount: str
    count: int


class ExpenseSummaryResponse(BaseModel):
    from_date: date
    to_date: date
    currency: str
    total_amount: str
    total_count: int
    by_category: List[CategoryExpenseSummary]
    by_month: List[MonthlyExpenseSummary]


class FinancialSummaryOverviewResponse(BaseModel):
    from_date: date
    to_date: date
    currency: str
    total_expenses: str
    expense_count: int
