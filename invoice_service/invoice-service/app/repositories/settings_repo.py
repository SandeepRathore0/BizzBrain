from uuid import UUID
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.invoice import InvoiceSettings
from app.schemas.settings import InvoiceSettingsUpdate


async def get_or_create_settings(
    session: AsyncSession,
    business_id: UUID,
) -> InvoiceSettings:
    """Fetches invoice settings for the business, or creates defaults atomically if not existing."""
    stmt = (
        insert(InvoiceSettings)
        .values(
            business_id=business_id,
            invoice_prefix="INV-",
            default_due_days=30,
        )
        .on_conflict_do_nothing(index_elements=["business_id"])
    )
    await session.execute(stmt)

    select_stmt = select(InvoiceSettings).where(InvoiceSettings.business_id == business_id)
    res = await session.execute(select_stmt)
    return res.scalar_one()


async def update_settings(
    session: AsyncSession,
    business_id: UUID,
    update_data: InvoiceSettingsUpdate,
) -> InvoiceSettings:
    """Updates invoice settings for the specified business."""
    settings = await get_or_create_settings(session, business_id)

    if update_data.invoice_prefix is not None:
        settings.invoice_prefix = update_data.invoice_prefix
    if update_data.seller_name is not None:
        settings.seller_name = update_data.seller_name
    if update_data.seller_address is not None:
        settings.seller_address = update_data.seller_address
    if update_data.seller_tax_id is not None:
        settings.seller_tax_id = update_data.seller_tax_id
    if update_data.default_due_days is not None:
        settings.default_due_days = update_data.default_due_days

    await session.flush()
    return settings
