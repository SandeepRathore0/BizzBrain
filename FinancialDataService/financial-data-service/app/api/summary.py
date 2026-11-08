from datetime import date
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.permissions import AuthContext, require_permission
from app.schemas.common import SuccessEnvelope
from app.schemas.summary import (
    ExpenseSummaryResponse,
    FinancialSummaryOverviewResponse,
)
from app.services.summary_service import SummaryService

router = APIRouter(prefix="/api/v1/financial-summary", tags=["Financial Summary"])


@router.get(
    "/expenses",
    response_model=SuccessEnvelope[ExpenseSummaryResponse],
    status_code=status.HTTP_200_OK,
)
async def get_expense_summary(
    request: Request,
    from_date: date = Query(..., alias="from", description="Start date (YYYY-MM-DD)"),
    to_date: date = Query(..., alias="to", description="End date (YYYY-MM-DD)"),
    auth_context: AuthContext = Depends(require_permission("dashboard.view")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[ExpenseSummaryResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await SummaryService.get_expense_summary(
        session=session,
        auth_context=auth_context,
        from_date=from_date,
        to_date=to_date,
        request_id=request_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)


@router.get(
    "",
    response_model=SuccessEnvelope[FinancialSummaryOverviewResponse],
    status_code=status.HTTP_200_OK,
)
async def get_financial_summary_overview(
    request: Request,
    from_date: date = Query(..., alias="from", description="Start date (YYYY-MM-DD)"),
    to_date: date = Query(..., alias="to", description="End date (YYYY-MM-DD)"),
    auth_context: AuthContext = Depends(require_permission("dashboard.view")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[FinancialSummaryOverviewResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await SummaryService.get_financial_summary_overview(
        session=session,
        auth_context=auth_context,
        from_date=from_date,
        to_date=to_date,
        request_id=request_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)
