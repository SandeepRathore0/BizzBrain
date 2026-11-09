from typing import Optional
import uuid
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.permissions import AuthContext, get_auth_context, require_permission
from app.core.pagination import PaginatedResponse
from app.schemas.common import SuccessEnvelope
from app.schemas.customer import CustomerCreate, CustomerResponse, CustomerUpdate
from app.services.customer_service import CustomerService

router = APIRouter(prefix="/api/v1/customers", tags=["Customers"])


@router.post(
    "",
    response_model=SuccessEnvelope[CustomerResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_customer(
    data: CustomerCreate,
    request: Request,
    auth_context: AuthContext = Depends(require_permission("customers.write")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[CustomerResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await CustomerService.create_customer(
        session=session,
        auth_context=auth_context,
        data=data,
        request_id=request_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)


@router.get(
    "",
    response_model=SuccessEnvelope[PaginatedResponse[CustomerResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_customers(
    request: Request,
    search: Optional[str] = Query(None, description="Search by name or email"),
    is_active: Optional[bool] = Query(None, description="Filter active status"),
    limit: int = Query(25, ge=1, le=100, description="Items per page"),
    cursor: Optional[str] = Query(None, description="Pagination cursor"),
    auth_context: AuthContext = Depends(require_permission("customers.read")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[PaginatedResponse[CustomerResponse]]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await CustomerService.list_customers(
        session=session,
        auth_context=auth_context,
        search=search,
        is_active=is_active,
        limit=limit,
        cursor=cursor,
    )
    return SuccessEnvelope(data=result, request_id=request_id)


@router.get(
    "/{customer_id}",
    response_model=SuccessEnvelope[CustomerResponse],
    status_code=status.HTTP_200_OK,
)
async def get_customer(
    customer_id: uuid.UUID,
    request: Request,
    auth_context: AuthContext = Depends(require_permission("customers.read")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[CustomerResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await CustomerService.get_customer(
        session=session,
        auth_context=auth_context,
        customer_id=customer_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)


@router.put(
    "/{customer_id}",
    response_model=SuccessEnvelope[CustomerResponse],
    status_code=status.HTTP_200_OK,
)
async def update_customer(
    customer_id: uuid.UUID,
    data: CustomerUpdate,
    request: Request,
    auth_context: AuthContext = Depends(require_permission("customers.write")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[CustomerResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await CustomerService.update_customer(
        session=session,
        auth_context=auth_context,
        customer_id=customer_id,
        data=data,
        request_id=request_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)


@router.delete(
    "/{customer_id}",
    response_model=SuccessEnvelope[CustomerResponse],
    status_code=status.HTTP_200_OK,
)
async def delete_customer(
    customer_id: uuid.UUID,
    request: Request,
    auth_context: AuthContext = Depends(require_permission("customers.write")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[CustomerResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await CustomerService.deactivate_customer(
        session=session,
        auth_context=auth_context,
        customer_id=customer_id,
        request_id=request_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)
