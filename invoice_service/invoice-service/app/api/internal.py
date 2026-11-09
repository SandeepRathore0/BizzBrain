from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import ServiceContext, require_internal_caller
from app.core.config import settings
from app.core.database import get_db
from app.core.logging import request_id_ctx
from app.schemas.envelope import SuccessResponse
from app.schemas.internal import (
    PayableDetailsResponse,
    PaymentEventRequest,
    PaymentEventResponse,
    ResolvePayTokenRequest,
    ResolvePayTokenResponse,
)
from app.services.allocation import AllocationService

router = APIRouter(prefix="/internal/v1", tags=["Internal"])


@router.get(
    "/invoices/{invoice_id}/payable-details",
    response_model=SuccessResponse[PayableDetailsResponse],
)
async def get_payable_details(
    invoice_id: UUID,
    business_id: UUID = Query(..., description="Business ID to scope verification"),
    service_ctx: ServiceContext = Depends(
        require_internal_caller(settings.ALLOWED_INTERNAL_PAYMENT_CALLERS)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Contract C2: Called by Payment Service at order creation and before capture."""
    req_id = request_id_ctx.get()
    details = await AllocationService.get_payable_details(
        session=db,
        business_id=business_id,
        invoice_id=invoice_id,
    )
    return SuccessResponse(
        success=True,
        data=details,
        request_id=req_id,
    )


@router.post(
    "/invoices/resolve-pay-token",
    response_model=SuccessResponse[ResolvePayTokenResponse],
)
async def resolve_pay_token(
    payload: ResolvePayTokenRequest,
    service_ctx: ServiceContext = Depends(
        require_internal_caller(settings.ALLOWED_INTERNAL_PAYMENT_CALLERS)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Contract C4: Resolves raw pay token to invoice_id and business_id."""
    req_id = request_id_ctx.get()
    resolved = await AllocationService.resolve_pay_token(
        session=db,
        raw_token=payload.token,
    )
    return SuccessResponse(
        success=True,
        data=resolved,
        request_id=req_id,
    )


@router.post(
    "/payment-events",
    response_model=SuccessResponse[PaymentEventResponse],
    status_code=status.HTTP_200_OK,
)
async def handle_payment_event(
    payload: PaymentEventRequest,
    service_ctx: ServiceContext = Depends(
        require_internal_caller(settings.ALLOWED_INTERNAL_PAYMENT_CALLERS)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Contract C3: Handles PaymentCaptured event with inbox deduplication,
    status/balance validation, allocation creation, state transition,
    and reconciliation exception recording.
    Always returns 200 to acknowledge delivery.
    """
    req_id = request_id_ctx.get()
    result = await AllocationService.handle_payment_captured_event(
        session=db,
        event=payload,
        request_id=req_id,
    )
    await db.commit()

    return SuccessResponse(
        success=True,
        data=result,
        request_id=req_id,
    )
