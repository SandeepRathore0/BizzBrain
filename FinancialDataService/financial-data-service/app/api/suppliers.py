from typing import Optional
import uuid
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.permissions import AuthContext, get_auth_context, require_permission
from app.core.pagination import PaginatedResponse
from app.schemas.common import SuccessEnvelope
from app.schemas.supplier import SupplierCreate, SupplierResponse, SupplierUpdate
from app.services.supplier_service import SupplierService

router = APIRouter(prefix="/api/v1/suppliers", tags=["Suppliers"])


@router.post(
    "",
    response_model=SuccessEnvelope[SupplierResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_supplier(
    data: SupplierCreate,
    request: Request,
    auth_context: AuthContext = Depends(require_permission("suppliers.write")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[SupplierResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await SupplierService.create_supplier(
        session=session,
        auth_context=auth_context,
        data=data,
        request_id=request_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)


@router.get(
    "",
    response_model=SuccessEnvelope[PaginatedResponse[SupplierResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_suppliers(
    request: Request,
    search: Optional[str] = Query(None, description="Search by name or email"),
    is_active: Optional[bool] = Query(None, description="Filter active status"),
    limit: int = Query(25, ge=1, le=100, description="Items per page"),
    cursor: Optional[str] = Query(None, description="Pagination cursor"),
    auth_context: AuthContext = Depends(require_permission("suppliers.read")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[PaginatedResponse[SupplierResponse]]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await SupplierService.list_suppliers(
        session=session,
        auth_context=auth_context,
        search=search,
        is_active=is_active,
        limit=limit,
        cursor=cursor,
    )
    return SuccessEnvelope(data=result, request_id=request_id)


@router.get(
    "/{supplier_id}",
    response_model=SuccessEnvelope[SupplierResponse],
    status_code=status.HTTP_200_OK,
)
async def get_supplier(
    supplier_id: uuid.UUID,
    request: Request,
    auth_context: AuthContext = Depends(require_permission("suppliers.read")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[SupplierResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await SupplierService.get_supplier(
        session=session,
        auth_context=auth_context,
        supplier_id=supplier_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)


@router.put(
    "/{supplier_id}",
    response_model=SuccessEnvelope[SupplierResponse],
    status_code=status.HTTP_200_OK,
)
async def update_supplier(
    supplier_id: uuid.UUID,
    data: SupplierUpdate,
    request: Request,
    auth_context: AuthContext = Depends(require_permission("suppliers.write")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[SupplierResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await SupplierService.update_supplier(
        session=session,
        auth_context=auth_context,
        supplier_id=supplier_id,
        data=data,
        request_id=request_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)


@router.delete(
    "/{supplier_id}",
    response_model=SuccessEnvelope[SupplierResponse],
    status_code=status.HTTP_200_OK,
)
async def delete_supplier(
    supplier_id: uuid.UUID,
    request: Request,
    auth_context: AuthContext = Depends(require_permission("suppliers.write")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[SupplierResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await SupplierService.deactivate_supplier(
        session=session,
        auth_context=auth_context,
        supplier_id=supplier_id,
        request_id=request_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)
