from uuid import UUID
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def allocate_invoice_number(
    session: AsyncSession, business_id: UUID, prefix: str = "INV-"
) -> str:
    """Atomically allocates the next sequential invoice number for a business.
    Guarantees gap-free numbers without race conditions or locks on the entire table.
    DO NOT use MAX() + 1.
    """
    stmt = text(
        """
        INSERT INTO invoice_counters (business_id, last_number)
        VALUES (:business_id, 1)
        ON CONFLICT (business_id)
        DO UPDATE SET last_number = invoice_counters.last_number + 1
        RETURNING last_number;
        """
    )
    result = await session.execute(stmt, {"business_id": business_id})
    last_number = result.scalar_one()
    # Format prefix + 6-digit zero-padded number (e.g. INV-000001)
    clean_prefix = prefix.strip() if prefix else "INV-"
    return f"{clean_prefix}{last_number:06d}"
