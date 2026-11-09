from datetime import date
from sqlalchemy.ext.asyncio import AsyncSession
from app.clients.user_service_client import user_service_client
from app.core.errors import ValidationException
from app.core.permissions import AuthContext
from app.repositories.summary_repository import SummaryRepository
from app.schemas.summary import (
    ExpenseSummaryResponse,
    FinancialSummaryOverviewResponse,
)


class SummaryService:
    @staticmethod
    def _validate_date_range(from_date: date, to_date: date) -> None:
        if from_date > to_date:
            raise ValidationException(
                code="INVALID_DATE_RANGE",
                message="'from' date cannot be after 'to' date",
            )
        delta_days = (to_date - from_date).days
        if delta_days > 366:
            raise ValidationException(
                code="PERIOD_EXCEEDS_MAXIMUM",
                message=f"Date range of {delta_days} days exceeds maximum allowed period of 366 days",
            )

    @staticmethod
    async def get_expense_summary(
        session: AsyncSession,
        auth_context: AuthContext,
        from_date: date,
        to_date: date,
        request_id: str,
    ) -> ExpenseSummaryResponse:
        SummaryService._validate_date_range(from_date, to_date)

        # Base currency from User Service
        business = await user_service_client.get_business_details(
            business_id=auth_context.business_id,
            request_id=request_id,
        )

        data = await SummaryRepository.get_expense_summary(
            session=session,
            business_id=auth_context.business_id,
            from_date=from_date,
            to_date=to_date,
        )

        return ExpenseSummaryResponse(
            from_date=from_date,
            to_date=to_date,
            currency=business.base_currency,
            total_amount=data["total_amount"],
            total_count=data["total_count"],
            by_category=data["by_category"],
            by_month=data["by_month"],
        )

    @staticmethod
    async def get_financial_summary_overview(
        session: AsyncSession,
        auth_context: AuthContext,
        from_date: date,
        to_date: date,
        request_id: str,
    ) -> FinancialSummaryOverviewResponse:
        SummaryService._validate_date_range(from_date, to_date)

        business = await user_service_client.get_business_details(
            business_id=auth_context.business_id,
            request_id=request_id,
        )

        data = await SummaryRepository.get_expense_summary(
            session=session,
            business_id=auth_context.business_id,
            from_date=from_date,
            to_date=to_date,
        )

        return FinancialSummaryOverviewResponse(
            from_date=from_date,
            to_date=to_date,
            currency=business.base_currency,
            total_expenses=data["total_amount"],
            expense_count=data["total_count"],
        )
