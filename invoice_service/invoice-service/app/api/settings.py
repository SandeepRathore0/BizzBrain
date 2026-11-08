from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import UserContext
from app.core.database import get_db
from app.core.logging import request_id_ctx
from app.core.permissions import require_permission
from app.repositories.audit_repo import record_audit_log
from app.repositories.settings_repo import get_or_create_settings, update_settings
from app.schemas.envelope import SuccessResponse
from app.schemas.settings import InvoiceSettingsResponse, InvoiceSettingsUpdate

router = APIRouter(prefix="/api/v1/invoice-settings", tags=["Settings"])


@router.get("", response_model=SuccessResponse[InvoiceSettingsResponse])
async def get_invoice_settings(
    user: UserContext = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
):
    settings = await get_or_create_settings(db, user.business_id)
    req_id = request_id_ctx.get()
    return SuccessResponse(
        success=True,
        data=InvoiceSettingsResponse.model_validate(settings),
        request_id=req_id,
    )


@router.put("", response_model=SuccessResponse[InvoiceSettingsResponse])
async def put_invoice_settings(
    update_data: InvoiceSettingsUpdate,
    user: UserContext = Depends(require_permission("settings.manage")),
    db: AsyncSession = Depends(get_db),
):
    current = await get_or_create_settings(db, user.business_id)
    before_dict = {
        "invoice_prefix": current.invoice_prefix,
        "seller_name": current.seller_name,
        "seller_address": current.seller_address,
        "seller_tax_id": current.seller_tax_id,
        "default_due_days": current.default_due_days,
    }

    updated = await update_settings(db, user.business_id, update_data)

    after_dict = {
        "invoice_prefix": updated.invoice_prefix,
        "seller_name": updated.seller_name,
        "seller_address": updated.seller_address,
        "seller_tax_id": updated.seller_tax_id,
        "default_due_days": updated.default_due_days,
    }

    req_id = request_id_ctx.get()
    await record_audit_log(
        session=db,
        business_id=user.business_id,
        actor_type="USER",
        actor_id=str(user.user_id),
        action="INVOICE_SETTINGS_UPDATED",
        entity_type="INVOICE_SETTINGS",
        entity_id=user.business_id,
        before=before_dict,
        after=after_dict,
        request_id=req_id,
    )
    await db.commit()

    return SuccessResponse(
        success=True,
        data=InvoiceSettingsResponse.model_validate(updated),
        request_id=req_id,
    )
