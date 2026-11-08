from datetime import date
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import UserContext
from app.core.database import get_db
from app.core.errors import BadRequestException, NotFoundException, ValidationException
from app.core.idempotency import check_idempotency_key, save_idempotency_key
from app.core.logging import request_id_ctx
from app.core.permissions import require_permission
from app.repositories import audit_repo, reconciliation_repo
from app.schemas.envelope import SuccessResponse
from app.schemas.invoice import (
    InvoiceBalanceResponse,
    InvoiceCreate,
    InvoiceIssueResponse,
    InvoiceResponse,
    InvoiceUpdate,
    InvoiceVoidRequest,
    PaginatedInvoicesResponse,
    PaymentAllocationResponse,
    PayLinkResponse,
)
from app.schemas.reconciliation import (
    PaginatedReconciliationExceptionsResponse,
    ReconciliationExceptionResponse,
    ResolveExceptionRequest,
)
from app.services.invoice_service import InvoiceService

router = APIRouter(prefix="/api/v1/invoices", tags=["Invoices"])


@router.post("", response_model=SuccessResponse[InvoiceResponse], status_code=status.HTTP_201_CREATED)
async def create_draft_invoice(
    payload: InvoiceCreate,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user: UserContext = Depends(require_permission("invoices.create")),
    db: AsyncSession = Depends(get_db),
):
    try:
        UUID(idempotency_key)
    except ValueError:
        raise BadRequestException("Idempotency-Key header must be a valid UUID")

    req_id = request_id_ctx.get()
    endpoint = "POST:/api/v1/invoices"
    req_dict = payload.model_dump(mode="json")

    cached = await check_idempotency_key(
        session=db,
        business_id=user.business_id,
        key=idempotency_key,
        endpoint=endpoint,
        request_data=req_dict,
    )
    if cached:
        cached_status, cached_body = cached
        response.status_code = cached_status
        response.headers["Idempotent-Replayed"] = "true"
        return cached_body

    created_invoice = await InvoiceService.create_draft(
        session=db,
        user=user,
        payload=payload,
        request_id=req_id,
    )

    success_data = SuccessResponse(
        success=True,
        data=created_invoice,
        request_id=req_id,
    )
    resp_dict = success_data.model_dump(mode="json")

    await save_idempotency_key(
        session=db,
        business_id=user.business_id,
        key=idempotency_key,
        endpoint=endpoint,
        request_data=req_dict,
        response_status=status.HTTP_201_CREATED,
        response_body=resp_dict,
    )

    await db.commit()
    return success_data


@router.get("", response_model=SuccessResponse[PaginatedInvoicesResponse])
async def list_invoices(
    limit: int = Query(25, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    customer_id: Optional[UUID] = Query(None),
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    overdue: Optional[bool] = Query(False),
    search: Optional[str] = Query(None),
    user: UserContext = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
):
    req_id = request_id_ctx.get()
    result = await InvoiceService.list_invoices(
        session=db,
        business_id=user.business_id,
        limit=limit,
        cursor=cursor,
        status=status,
        customer_id=customer_id,
        from_date=from_date,
        to_date=to_date,
        overdue_only=bool(overdue),
        search=search,
    )
    return SuccessResponse(
        success=True,
        data=result,
        request_id=req_id,
    )


@router.get("/overdue", response_model=SuccessResponse[PaginatedInvoicesResponse])
async def list_overdue_invoices(
    limit: int = Query(25, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    customer_id: Optional[UUID] = Query(None),
    user: UserContext = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
):
    """Dedicated endpoint for overdue invoices, declared BEFORE /{invoice_id}."""
    req_id = request_id_ctx.get()
    result = await InvoiceService.list_invoices(
        session=db,
        business_id=user.business_id,
        limit=limit,
        cursor=cursor,
        customer_id=customer_id,
        overdue_only=True,
    )
    return SuccessResponse(
        success=True,
        data=result,
        request_id=req_id,
    )


@router.get(
    "/reconciliation-exceptions",
    response_model=SuccessResponse[PaginatedReconciliationExceptionsResponse],
)
async def list_reconciliation_exceptions(
    status: Optional[str] = Query(None, description="Filter by status: OPEN or RESOLVED"),
    limit: int = Query(25, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    user: UserContext = Depends(require_permission("reconciliation.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Lists reconciliation exceptions for the current business (Owner or Admin only)."""
    req_id = request_id_ctx.get()
    records, next_cursor = await reconciliation_repo.list_reconciliation_exceptions(
        session=db,
        business_id=user.business_id,
        status=status,
        limit=limit,
        cursor=cursor,
    )
    items = [ReconciliationExceptionResponse.model_validate(r) for r in records]
    return SuccessResponse(
        success=True,
        data=PaginatedReconciliationExceptionsResponse(
            items=items,
            next_cursor=next_cursor,
            has_more=bool(next_cursor),
        ),
        request_id=req_id,
    )


@router.post(
    "/reconciliation-exceptions/{exception_id}/resolve",
    response_model=SuccessResponse[ReconciliationExceptionResponse],
)
async def resolve_reconciliation_exception(
    exception_id: UUID,
    payload: ResolveExceptionRequest,
    user: UserContext = Depends(require_permission("reconciliation.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Resolves an open reconciliation exception (Owner or Admin only)."""
    req_id = request_id_ctx.get()
    exc = await reconciliation_repo.get_reconciliation_exception(
        session=db,
        business_id=user.business_id,
        exception_id=exception_id,
    )
    if not exc:
        raise NotFoundException("Reconciliation exception not found")

    if exc.status == "RESOLVED":
        raise ValidationException("Reconciliation exception is already resolved")

    before_state = {"status": exc.status}
    resolved = await reconciliation_repo.resolve_reconciliation_exception(
        session=db,
        business_id=user.business_id,
        exception_id=exception_id,
        resolved_by=user.user_id,
        resolution_note=payload.resolution_note,
    )

    await audit_repo.record_audit_log(
        session=db,
        business_id=user.business_id,
        actor_type="USER",
        actor_id=str(user.user_id),
        action="RECONCILIATION_EXCEPTION_RESOLVED",
        entity_type="RECONCILIATION_EXCEPTION",
        entity_id=exception_id,
        before=before_state,
        after={"status": "RESOLVED", "resolution_note": payload.resolution_note},
        request_id=req_id,
    )
    await db.commit()

    return SuccessResponse(
        success=True,
        data=ReconciliationExceptionResponse.model_validate(resolved),
        request_id=req_id,
    )


@router.get("/{invoice_id}", response_model=SuccessResponse[InvoiceResponse])
async def get_invoice(
    invoice_id: UUID,
    user: UserContext = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
):
    inv = await InvoiceService.get_invoice(
        session=db,
        business_id=user.business_id,
        invoice_id=invoice_id,
    )
    req_id = request_id_ctx.get()
    return SuccessResponse(
        success=True,
        data=inv,
        request_id=req_id,
    )


@router.patch("/{invoice_id}", response_model=SuccessResponse[InvoiceResponse])
async def update_invoice(
    invoice_id: UUID,
    payload: InvoiceUpdate,
    user: UserContext = Depends(require_permission("invoices.edit_draft")),
    db: AsyncSession = Depends(get_db),
):
    req_id = request_id_ctx.get()
    updated = await InvoiceService.update_invoice(
        session=db,
        user=user,
        invoice_id=invoice_id,
        payload=payload,
        request_id=req_id,
    )
    await db.commit()

    return SuccessResponse(
        success=True,
        data=updated,
        request_id=req_id,
    )


@router.post("/{invoice_id}/issue", response_model=SuccessResponse[InvoiceIssueResponse])
async def issue_invoice(
    invoice_id: UUID,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    user: UserContext = Depends(require_permission("invoices.issue")),
    db: AsyncSession = Depends(get_db),
):
    req_id = request_id_ctx.get()
    endpoint = f"POST:/api/v1/invoices/{invoice_id}/issue"

    if idempotency_key:
        try:
            UUID(idempotency_key)
        except ValueError:
            raise BadRequestException("Idempotency-Key header must be a valid UUID")

        cached = await check_idempotency_key(
            session=db,
            business_id=user.business_id,
            key=idempotency_key,
            endpoint=endpoint,
            request_data={},
        )
        if cached:
            cached_status, cached_body = cached
            response.status_code = cached_status
            response.headers["Idempotent-Replayed"] = "true"
            return cached_body

    issued = await InvoiceService.issue_invoice(
        session=db,
        user=user,
        invoice_id=invoice_id,
        request_id=req_id,
    )

    success_data = SuccessResponse(
        success=True,
        data=issued,
        request_id=req_id,
    )

    if idempotency_key:
        resp_dict = success_data.model_dump(mode="json")
        await save_idempotency_key(
            session=db,
            business_id=user.business_id,
            key=idempotency_key,
            endpoint=endpoint,
            request_data={},
            response_status=status.HTTP_200_OK,
            response_body=resp_dict,
        )

    await db.commit()
    return success_data


@router.post("/{invoice_id}/void", response_model=SuccessResponse[InvoiceResponse])
async def void_invoice(
    invoice_id: UUID,
    payload: InvoiceVoidRequest,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    user: UserContext = Depends(require_permission("invoices.void")),
    db: AsyncSession = Depends(get_db),
):
    req_id = request_id_ctx.get()
    endpoint = f"POST:/api/v1/invoices/{invoice_id}/void"
    req_dict = payload.model_dump(mode="json")

    if idempotency_key:
        try:
            UUID(idempotency_key)
        except ValueError:
            raise BadRequestException("Idempotency-Key header must be a valid UUID")

        cached = await check_idempotency_key(
            session=db,
            business_id=user.business_id,
            key=idempotency_key,
            endpoint=endpoint,
            request_data=req_dict,
        )
        if cached:
            cached_status, cached_body = cached
            response.status_code = cached_status
            response.headers["Idempotent-Replayed"] = "true"
            return cached_body

    voided = await InvoiceService.void_invoice(
        session=db,
        user=user,
        invoice_id=invoice_id,
        void_reason=payload.void_reason,
        request_id=req_id,
    )

    success_data = SuccessResponse(
        success=True,
        data=voided,
        request_id=req_id,
    )

    if idempotency_key:
        resp_dict = success_data.model_dump(mode="json")
        await save_idempotency_key(
            session=db,
            business_id=user.business_id,
            key=idempotency_key,
            endpoint=endpoint,
            request_data=req_dict,
            response_status=status.HTTP_200_OK,
            response_body=resp_dict,
        )

    await db.commit()
    return success_data


@router.get("/{invoice_id}/balance", response_model=SuccessResponse[InvoiceBalanceResponse])
async def get_invoice_balance(
    invoice_id: UUID,
    user: UserContext = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
):
    req_id = request_id_ctx.get()
    balance = await InvoiceService.get_invoice_balance(
        session=db,
        business_id=user.business_id,
        invoice_id=invoice_id,
    )
    return SuccessResponse(
        success=True,
        data=balance,
        request_id=req_id,
    )


@router.get("/{invoice_id}/allocations", response_model=SuccessResponse[List[PaymentAllocationResponse]])
async def get_invoice_allocations(
    invoice_id: UUID,
    user: UserContext = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
):
    req_id = request_id_ctx.get()
    allocations = await InvoiceService.get_invoice_allocations(
        session=db,
        business_id=user.business_id,
        invoice_id=invoice_id,
    )
    return SuccessResponse(
        success=True,
        data=allocations,
        request_id=req_id,
    )


@router.post("/{invoice_id}/pay-link", response_model=SuccessResponse[PayLinkResponse])
async def regenerate_pay_link(
    invoice_id: UUID,
    user: UserContext = Depends(require_permission("invoices.issue")),
    db: AsyncSession = Depends(get_db),
):
    req_id = request_id_ctx.get()
    result = await InvoiceService.regenerate_pay_link(
        session=db,
        user=user,
        invoice_id=invoice_id,
        request_id=req_id,
    )
    await db.commit()
    return SuccessResponse(
        success=True,
        data=result,
        request_id=req_id,
    )
