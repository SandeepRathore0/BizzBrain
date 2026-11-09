import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.invoice_client import get_invoice_client
from app.core.database import get_db
from app.core.errors import BadRequestException
from app.core.idempotency import check_idempotency, record_idempotency
from app.core.money import format_amount
from app.schemas.common import APISuccessResponse
from app.schemas.payment import (
    CaptureResponse,
    OrderCreationResponse,
    PublicCaptureRequest,
    PublicOrderCreateRequest,
    PublicPaySummaryResponse,
)
from app.services.capture_service import get_capture_service
from app.services.payment_service import get_payment_service

router = APIRouter(prefix="/public/pay", tags=["Public Pay"])


@router.post(
    "/orders",
    status_code=201,
    response_model=APISuccessResponse[OrderCreationResponse],
)
async def create_public_order(
    req: PublicOrderCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Public customer order creation via pay token."""
    if not idempotency_key:
        raise BadRequestException(
            code="MISSING_IDEMPOTENCY_KEY",
            message="Idempotency-Key header is required.",
        )

    try:
        ikey_uuid = uuid.UUID(idempotency_key)
    except ValueError:
        raise BadRequestException(
            code="INVALID_IDEMPOTENCY_KEY",
            message="Idempotency-Key must be a valid UUID.",
        )

    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    invoice_client = get_invoice_client()

    # 1. Resolve pay token to invoice_id and business_id (uniform 404 on any failure)
    resolved = await invoice_client.resolve_pay_token(req.token, request_id=req_id)

    endpoint = "/api/v1/public/pay/orders"
    payload_dict = req.model_dump(mode="json")

    # 2. Idempotency check scoped to resolved business_id
    is_replayed, status_code, cached_body = await check_idempotency(
        db=db,
        business_id=resolved.business_id,
        key=str(ikey_uuid),
        endpoint=endpoint,
        payload=payload_dict,
    )
    if is_replayed and cached_body:
        response.headers["Idempotent-Replayed"] = "true"
        response.status_code = status_code or 200
        return cached_body

    # 3. Create payment order
    payment_service = get_payment_service()
    result = await payment_service.create_payment_order(
        business_id=resolved.business_id,
        invoice_id=resolved.invoice_id,
        requested_amount_str=req.amount,
        idempotency_key=ikey_uuid,
        initiated_by_type="PUBLIC_LINK",
        initiated_by_id=None,
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
        business_id=resolved.business_id,
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
async def capture_public_payment(
    payment_id: uuid.UUID,
    req: PublicCaptureRequest,
    request: Request,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Customer captures payment using pay token."""
    if not idempotency_key:
        raise BadRequestException(
            code="MISSING_IDEMPOTENCY_KEY",
            message="Idempotency-Key header is required.",
        )

    try:
        ikey_uuid = uuid.UUID(idempotency_key)
    except ValueError:
        raise BadRequestException(
            code="INVALID_IDEMPOTENCY_KEY",
            message="Idempotency-Key must be a valid UUID.",
        )

    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    invoice_client = get_invoice_client()

    # 1. Resolve token via C4 (uniform 404 on bad token)
    resolved = await invoice_client.resolve_pay_token(req.token, request_id=req_id)

    # 2. Verify payment belongs to resolved business_id and invoice_id
    from app.repositories.payment_repository import PaymentRepository
    repo = PaymentRepository(db)
    p = await repo.get_payment_by_id(resolved.business_id, payment_id)
    if not p or p.invoice_id != resolved.invoice_id:
        from app.core.errors import NotFoundException
        raise NotFoundException(
            code="PAYMENT_NOT_FOUND",
            message="Payment not found for this payment link.",
        )

    endpoint = f"/api/v1/public/pay/{payment_id}/capture"
    payload_dict = {"token": req.token}

    # 3. Idempotency Check
    is_replayed, status_code, cached_body = await check_idempotency(
        db=db,
        business_id=resolved.business_id,
        key=str(ikey_uuid),
        endpoint=endpoint,
        payload=payload_dict,
    )
    if is_replayed and cached_body:
        response.headers["Idempotent-Replayed"] = "true"
        response.status_code = status_code or 200
        return cached_body

    # 4. Perform Capture
    capture_service = get_capture_service()
    result = await capture_service.capture_payment(
        business_id=resolved.business_id,
        payment_id=payment_id,
        actor_type="PUBLIC_LINK",
        actor_id=None,
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
        business_id=resolved.business_id,
        key=str(ikey_uuid),
        endpoint=endpoint,
        payload=payload_dict,
        response_status=200,
        response_body=envelope,
    )
    await db.commit()

    return envelope


@router.get(
    "/{token}/summary",
    response_model=APISuccessResponse[PublicPaySummaryResponse],
)
async def get_public_summary(
    token: str,
    request: Request,
):
    """Customer views invoice summary via pay token. Return only permitted public fields."""
    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    invoice_client = get_invoice_client()

    # 1. Resolve token (uniform 404 on invalid or expired)
    resolved = await invoice_client.resolve_pay_token(token, request_id=req_id)

    # 2. Fetch payable details
    invoice = await invoice_client.get_payable_details(
        business_id=resolved.business_id,
        invoice_id=resolved.invoice_id,
        request_id=req_id,
    )

    return {
        "success": True,
        "data": PublicPaySummaryResponse(
            invoice_number=invoice.invoice_number,
            business_name=invoice.business_name,
            amount_due=format_amount(invoice.outstanding_balance, invoice.currency),
            currency=invoice.currency,
            payable=invoice.payable,
        ).model_dump(mode="json"),
        "request_id": req_id,
    }
