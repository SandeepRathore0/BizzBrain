from datetime import date
from decimal import Decimal
from typing import Any, Optional
import uuid
from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.errors import BadRequestException, ValidationException
from app.core.pagination import PaginatedResponse
from app.core.permissions import AuthContext, require_permission
from app.schemas.common import SuccessEnvelope
from app.schemas.expense import (
    ExpenseCreate,
    ExpensePatch,
    ExpenseResponse,
    ExpenseVoidRequest,
)
from app.schemas.receipt import ReceiptDownloadResponse, ReceiptResponse
from app.services.expense_service import ExpenseService
from app.services.receipt_service import ReceiptService

router = APIRouter(prefix="/api/v1/expenses", tags=["Expenses"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
)
async def record_expense(
    data: ExpenseCreate,
    request: Request,
    idempotency_key_str: Optional[str] = Header(None, alias="Idempotency-Key"),
    auth_context: AuthContext = Depends(require_permission("expenses.record")),
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")

    if not idempotency_key_str:
        raise BadRequestException(
            code="IDEMPOTENCY_KEY_REQUIRED",
            message="Idempotency-Key header is required for creating an expense",
        )

    try:
        idempotency_key = uuid.UUID(idempotency_key_str.strip())
    except ValueError as e:
        raise BadRequestException(
            code="INVALID_IDEMPOTENCY_KEY",
            message="Idempotency-Key header must be a valid UUID",
        ) from e

    result_data, is_replayed = await ExpenseService.record_expense(
        session=session,
        auth_context=auth_context,
        idempotency_key=idempotency_key,
        data=data,
        request_id=request_id,
    )

    content = {
        "success": True,
        "data": result_data,
        "request_id": request_id,
    }

    headers = {}
    if is_replayed:
        headers["Idempotent-Replayed"] = "true"
        return JSONResponse(status_code=status.HTTP_200_OK, content=content, headers=headers)
    else:
        headers["Idempotent-Replayed"] = "false"
        return JSONResponse(status_code=status.HTTP_201_CREATED, content=content, headers=headers)


@router.get(
    "",
    response_model=SuccessEnvelope[PaginatedResponse[ExpenseResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_expenses(
    request: Request,
    from_date: Optional[date] = Query(None, alias="from", description="From date"),
    to_date: Optional[date] = Query(None, alias="to", description="To date"),
    category_id: Optional[uuid.UUID] = Query(None, description="Category filter"),
    supplier_id: Optional[uuid.UUID] = Query(None, description="Supplier filter"),
    status_filter: Optional[str] = Query(None, alias="status", description="Status filter (POSTED, VOID)"),
    min_amount: Optional[Decimal] = Query(None, description="Minimum amount"),
    max_amount: Optional[Decimal] = Query(None, description="Maximum amount"),
    limit: int = Query(25, ge=1, le=100, description="Items per page"),
    cursor: Optional[str] = Query(None, description="Pagination cursor"),
    auth_context: AuthContext = Depends(require_permission("expenses.view")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[PaginatedResponse[ExpenseResponse]]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await ExpenseService.list_expenses(
        session=session,
        auth_context=auth_context,
        from_date=from_date,
        to_date=to_date,
        category_id=category_id,
        supplier_id=supplier_id,
        status=status_filter,
        min_amount=min_amount,
        max_amount=max_amount,
        limit=limit,
        cursor=cursor,
    )
    return SuccessEnvelope(data=result, request_id=request_id)


@router.get(
    "/{expense_id}",
    response_model=SuccessEnvelope[ExpenseResponse],
    status_code=status.HTTP_200_OK,
)
async def get_expense(
    expense_id: uuid.UUID,
    request: Request,
    auth_context: AuthContext = Depends(require_permission("expenses.view")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[ExpenseResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await ExpenseService.get_expense(
        session=session,
        auth_context=auth_context,
        expense_id=expense_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)


@router.patch(
    "/{expense_id}",
    response_model=SuccessEnvelope[ExpenseResponse],
    status_code=status.HTTP_200_OK,
)
async def patch_expense(
    expense_id: uuid.UUID,
    request: Request,
    auth_context: AuthContext = Depends(require_permission("expenses.edit")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[ExpenseResponse]:
    request_id = getattr(request.state, "request_id", "unknown")

    # Read raw body to inspect all passed keys
    try:
        raw_body: dict[str, Any] = await request.json()
    except Exception:
        raw_body = {}

    raw_keys = set(raw_body.keys())
    # Validate with Pydantic
    try:
        data = ExpensePatch.model_validate(raw_body)
    except Exception as e:
        raise ValidationException(
            code="FIELD_NOT_EDITABLE",
            message="Only 'description' and 'category_id' are editable on a posted expense",
            details=[str(e)],
        ) from e

    result = await ExpenseService.patch_expense(
        session=session,
        auth_context=auth_context,
        expense_id=expense_id,
        data=data,
        raw_body_keys=raw_keys,
        request_id=request_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)


@router.post(
    "/{expense_id}/void",
    response_model=SuccessEnvelope[ExpenseResponse],
    status_code=status.HTTP_200_OK,
)
async def void_expense(
    expense_id: uuid.UUID,
    data: ExpenseVoidRequest,
    request: Request,
    auth_context: AuthContext = Depends(require_permission("expenses.void")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[ExpenseResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await ExpenseService.void_expense(
        session=session,
        auth_context=auth_context,
        expense_id=expense_id,
        data=data,
        request_id=request_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)


@router.post(
    "/{expense_id}/receipt",
    response_model=SuccessEnvelope[ReceiptResponse],
    status_code=status.HTTP_201_CREATED,
)
async def upload_receipt(
    expense_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    auth_context: AuthContext = Depends(require_permission("expenses.edit")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[ReceiptResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await ReceiptService.upload_receipt(
        session=session,
        auth_context=auth_context,
        expense_id=expense_id,
        file=file,
        request_id=request_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)


@router.get(
    "/{expense_id}/receipt",
    response_model=SuccessEnvelope[ReceiptDownloadResponse],
    status_code=status.HTTP_200_OK,
)
async def get_receipt(
    expense_id: uuid.UUID,
    request: Request,
    auth_context: AuthContext = Depends(require_permission("expenses.view")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[ReceiptDownloadResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await ReceiptService.get_receipt_download_url(
        session=session,
        auth_context=auth_context,
        expense_id=expense_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)

