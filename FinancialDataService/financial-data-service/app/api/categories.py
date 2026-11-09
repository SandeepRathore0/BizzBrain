from typing import List
import uuid
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.permissions import AuthContext, require_permission
from app.schemas.category import CategoryCreate, CategoryResponse, CategoryUpdate
from app.schemas.common import SuccessEnvelope
from app.services.category_service import CategoryService

router = APIRouter(prefix="/api/v1/expense-categories", tags=["Expense Categories"])


@router.post(
    "",
    response_model=SuccessEnvelope[CategoryResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_category(
    data: CategoryCreate,
    request: Request,
    auth_context: AuthContext = Depends(require_permission("categories.write")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[CategoryResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await CategoryService.create_category(
        session=session,
        auth_context=auth_context,
        data=data,
        request_id=request_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)


@router.get(
    "",
    response_model=SuccessEnvelope[List[CategoryResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_categories(
    request: Request,
    auth_context: AuthContext = Depends(require_permission("categories.read")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[List[CategoryResponse]]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await CategoryService.list_categories(
        session=session,
        auth_context=auth_context,
    )
    return SuccessEnvelope(data=result, request_id=request_id)


@router.patch(
    "/{category_id}",
    response_model=SuccessEnvelope[CategoryResponse],
    status_code=status.HTTP_200_OK,
)
async def update_category(
    category_id: uuid.UUID,
    data: CategoryUpdate,
    request: Request,
    auth_context: AuthContext = Depends(require_permission("categories.write")),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[CategoryResponse]:
    request_id = getattr(request.state, "request_id", "unknown")
    result = await CategoryService.update_category(
        session=session,
        auth_context=auth_context,
        category_id=category_id,
        data=data,
        request_id=request_id,
    )
    return SuccessEnvelope(data=result, request_id=request_id)
