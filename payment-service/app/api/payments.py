import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, get_current_auth
from app.core.database import get_db
from app.core.errors import BadRequestException, NotFoundException
from app.core.idempotency import check_idempotency, record_idempotency
from app.core.money import format_amount
from app.repositories.payment_repository import PaymentRepository
from app.schemas.common import APISuccessResponse, CursorPageResponse
from app.schemas.payment import (
    CaptureResponse,
    OrderCreationResponse,
    PaymentResponse,
    StaffOrderCreateRequest,
)
from app.services.capture_service import get_capture_service
from app.services.payment_service import get_payment_service

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post(
    "/orders",
    status_code=201,
    response_model=APISuccessResponse[OrderCreationResponse],
)
async def create_staff_order(
    req: StaffOrderCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
):
    if not idempotency_key:
        raise BadRequestException(
            code="MISSING_IDEMPOTENCY_KEY",
            message="Idempotency-Key header is required for creating payment orders.",
        )

    try:
        ikey_uuid = uuid.UUID(idempotency_key)
    except ValueError:
        raise BadRequestException(
            code="INVALID_IDEMPOTENCY_KEY",
            message="Idempotency-Key must be a valid UUID.",
        )

    endpoint = "/api/v1/payments/orders"
    payload_dict = req.model_dump(mode="json")
    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))

    # Idempotency Check
    is_replayed, status_code, cached_body = await check_idempotency(
        db=db,
        business_id=auth.business_id,
        key=str(ikey_uuid),
        endpoint=endpoint,
        payload=payload_dict,
    )
    if is_replayed and cached_body:
        response.headers["Idempotent-Replayed"] = "true"
        response.status_code = status_code or 200
        return cached_body

    service = get_payment_service()
    result = await service.create_payment_order(
        business_id=auth.business_id,
        invoice_id=req.invoice_id,
        requested_amount_str=req.amount,
        idempotency_key=ikey_uuid,
        initiated_by_type="USER",
        initiated_by_id=auth.user_id,
        request_id=req_id,
    )

    envelope = {
        "success": True,
        "data": result.model_dump(mode="json"),
        "request_id": req_id,
    }

    # Record completed idempotency entry
    await record_idempotency(
        db=db,
        business_id=auth.business_id,
        key=str(ikey_uuid),
        endpoint=endpoint,
        payload=payload_dict,
        response_status=201,
        response_body=envelope,
    )
    await db.commit()

    return envelope


@router.post(
    "/{payment_id}/capture",
    response_model=APISuccessResponse[CaptureResponse],
)
async def capture_staff_payment(
    payment_id: uuid.UUID,
    request: Request,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
):
    if not idempotency_key:
        raise BadRequestException(
            code="MISSING_IDEMPOTENCY_KEY",
            message="Idempotency-Key header is required for payment capture.",
        )

    try:
        ikey_uuid = uuid.UUID(idempotency_key)
    except ValueError:
        raise BadRequestException(
            code="INVALID_IDEMPOTENCY_KEY",
            message="Idempotency-Key must be a valid UUID.",
        )

    endpoint = f"/api/v1/payments/{payment_id}/capture"
    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    payload_dict = {"payment_id": str(payment_id)}

    # Idempotency Check
    is_replayed, status_code, cached_body = await check_idempotency(
        db=db,
        business_id=auth.business_id,
        key=str(ikey_uuid),
        endpoint=endpoint,
        payload=payload_dict,
    )
    if is_replayed and cached_body:
        response.headers["Idempotent-Replayed"] = "true"
        response.status_code = status_code or 200
        return cached_body

    capture_service = get_capture_service()
    result = await capture_service.capture_payment(
        business_id=auth.business_id,
        payment_id=payment_id,
        actor_type="USER",
        actor_id=str(auth.user_id),
        request_id=req_id,
    )

    envelope = {
        "success": True,
        "data": result.model_dump(mode="json"),
        "request_id": req_id,
    }

    # Record idempotency
    await record_idempotency(
        db=db,
        business_id=auth.business_id,
        key=str(ikey_uuid),
        endpoint=endpoint,
        payload=payload_dict,
        response_status=200,
        response_body=envelope,
    )
    await db.commit()

    return envelope


@router.get(
    "/{payment_id}",
    response_model=APISuccessResponse[PaymentResponse],
)
async def get_payment(
    payment_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
):
    repo = PaymentRepository(db)
    payment = await repo.get_payment_by_id(business_id=auth.business_id, payment_id=payment_id)
    if not payment:
        raise NotFoundException(
            code="PAYMENT_NOT_FOUND",
            message=f"Payment {payment_id} not found.",
        )

    req_id = getattr(request.state, "request_id", "")
    return {
        "success": True,
        "data": PaymentResponse(
            payment_id=payment.payment_id,
            business_id=payment.business_id,
            invoice_id=payment.invoice_id,
            invoice_number=payment.invoice_number,
            amount=format_amount(payment.amount, payment.currency),
            currency=payment.currency,
            provider=payment.provider,
            status=payment.status,
            allocation_status=payment.allocation_status,
            status_reason=payment.status_reason,
            initiated_by_type=payment.initiated_by_type,
            initiated_by_id=payment.initiated_by_id,
            expires_at=payment.expires_at,
            created_at=payment.created_at,
            updated_at=payment.updated_at,
        ),
        "request_id": req_id,
    }


@router.get(
    "",
    response_model=APISuccessResponse[CursorPageResponse[PaymentResponse]],
)
async def list_payments(
    request: Request,
    invoice_id: Optional[uuid.UUID] = Query(None),
    status: Optional[str] = Query(None),
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    cursor: Optional[datetime] = Query(None),
    limit: int = Query(default=25, ge=1, le=100),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
):
    repo = PaymentRepository(db)
    items = await repo.list_payments(
        business_id=auth.business_id,
        invoice_id=invoice_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        cursor=cursor,
    )

    has_more = len(items) > limit
    returned_items = items[:limit]

    next_cursor = None
    if has_more and returned_items:
        next_cursor = returned_items[-1].created_at.isoformat()

    req_id = getattr(request.state, "request_id", "")
    data_items = [
        PaymentResponse(
            payment_id=p.payment_id,
            business_id=p.business_id,
            invoice_id=p.invoice_id,
            invoice_number=p.invoice_number,
            amount=format_amount(p.amount, p.currency),
            currency=p.currency,
            provider=p.provider,
            status=p.status,
            allocation_status=p.allocation_status,
            status_reason=p.status_reason,
            initiated_by_type=p.initiated_by_type,
            initiated_by_id=p.initiated_by_id,
            expires_at=p.expires_at,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p in returned_items
    ]

    return {
        "success": True,
        "data": {
            "items": data_items,
            "next_cursor": next_cursor,
            "has_more": has_more,
        },
        "request_id": req_id,
    }
