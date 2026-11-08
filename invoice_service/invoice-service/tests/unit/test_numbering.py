import asyncio
from uuid import uuid4
import pytest
from app.services.numbering import allocate_invoice_number


@pytest.mark.asyncio
async def test_numbering_sequential_allocation(app_async_session):
    biz_id = uuid4()
    # First number should be 000001
    num1 = await allocate_invoice_number(app_async_session, biz_id, "INV-")
    await app_async_session.commit()
    assert num1 == "INV-000001"

    # Second number should be 000002
    num2 = await allocate_invoice_number(app_async_session, biz_id, "INV-")
    await app_async_session.commit()
    assert num2 == "INV-000002"

    # Custom prefix
    num3 = await allocate_invoice_number(app_async_session, biz_id, "BILL-")
    await app_async_session.commit()
    assert num3 == "BILL-000003"


@pytest.mark.asyncio
async def test_numbering_multi_tenant_isolation(app_async_session):
    biz1 = uuid4()
    biz2 = uuid4()

    n1_biz1 = await allocate_invoice_number(app_async_session, biz1, "INV-")
    n1_biz2 = await allocate_invoice_number(app_async_session, biz2, "INV-")
    await app_async_session.commit()

    # Both businesses should start at 000001 independently
    assert n1_biz1 == "INV-000001"
    assert n1_biz2 == "INV-000001"


@pytest.mark.asyncio
async def test_numbering_concurrent_gap_free():
    from app.core.database import async_session_factory
    biz_id = uuid4()

    async def _allocate():
        async with async_session_factory() as session:
            num = await allocate_invoice_number(session, biz_id, "INV-")
            await session.commit()
            return num

    # 10 concurrent requests
    results = await asyncio.gather(*[_allocate() for _ in range(10)])
    numbers = [int(r.replace("INV-", "")) for r in results]

    # Must be 10 unique, strictly sequential numbers 1 to 10
    assert len(set(numbers)) == 10
    assert sorted(numbers) == list(range(1, 11))
