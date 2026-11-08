from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import UserContext
from app.core.database import get_db
from app.core.errors import ConflictException, NotFoundException
from app.core.logging import request_id_ctx
from app.core.permissions import require_permission
from app.repositories.audit_repo import record_audit_log
from app.repositories.tax_rate_repo import (
    create_tax_rate,
    get_tax_rate_by_id,
    list_tax_rates,
    update_tax_rate,
)
from app.schemas.envelope import SuccessResponse
from app.schemas.tax_rate import TaxRateCreate, TaxRateResponse, TaxRateUpdate

router = APIRouter(prefix="/api/v1/tax-rates", tags=["Tax Rates"])


@router.get("", response_model=SuccessResponse[List[TaxRateResponse]])
async def get_tax_rates(
    active_only: bool = False,
    user: UserContext = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
):
    tax_rates = await list_tax_rates(db, user.business_id, active_only=active_only)
    req_id = request_id_ctx.get()
    return SuccessResponse(
        success=True,
        data=[TaxRateResponse.from_orm_model(tr) for tr in tax_rates],
        request_id=req_id,
    )


@router.post("", response_model=SuccessResponse[TaxRateResponse], status_code=status.HTTP_201_CREATED)
async def post_tax_rate(
    payload: TaxRateCreate,
    user: UserContext = Depends(require_permission("settings.manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        tax_rate = await create_tax_rate(
            session=db,
            business_id=user.business_id,
            name=payload.name,
            rate=payload.rate,
        )
        req_id = request_id_ctx.get()
        await record_audit_log(
            session=db,
            business_id=user.business_id,
            actor_type="USER",
            actor_id=str(user.user_id),
            action="TAX_RATE_CREATED",
            entity_type="TAX_RATE",
            entity_id=tax_rate.tax_rate_id,
            before=None,
            after={"name": tax_rate.name, "rate": str(tax_rate.rate)},
            request_id=req_id,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictException("A tax rate with this name and rate already exists for this business")

    req_id = request_id_ctx.get()
    return SuccessResponse(
        success=True,
        data=TaxRateResponse.from_orm_model(tax_rate),
        request_id=req_id,
    )


@router.patch("/{tax_rate_id}", response_model=SuccessResponse[TaxRateResponse])
async def patch_tax_rate(
    tax_rate_id: UUID,
    payload: TaxRateUpdate,
    user: UserContext = Depends(require_permission("settings.manage")),
    db: AsyncSession = Depends(get_db),
):
    current = await get_tax_rate_by_id(db, user.business_id, tax_rate_id)
    if not current:
        raise NotFoundException("Tax rate not found")

    before_dict = {"name": current.name, "is_active": current.is_active}

    try:
        updated = await update_tax_rate(
            session=db,
            business_id=user.business_id,
            tax_rate_id=tax_rate_id,
            name=payload.name,
            is_active=payload.is_active,
        )
        req_id = request_id_ctx.get()
        await record_audit_log(
            session=db,
            business_id=user.business_id,
            actor_type="USER",
            actor_id=str(user.user_id),
            action="TAX_RATE_UPDATED",
            entity_type="TAX_RATE",
            entity_id=tax_rate_id,
            before=before_dict,
            after={"name": updated.name, "is_active": updated.is_active},
            request_id=req_id,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictException("Tax rate update conflicts with existing configuration")

    return SuccessResponse(
        success=True,
        data=TaxRateResponse.from_orm_model(updated),
        request_id=req_id,
    )
