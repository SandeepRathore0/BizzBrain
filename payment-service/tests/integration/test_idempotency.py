import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableEntityException
from app.core.idempotency import check_idempotency, record_idempotency


@pytest.mark.asyncio
async def test_idempotency_workflow(db_session: AsyncSession):
    business_id = uuid.uuid4()
    key = str(uuid.uuid4())
    endpoint = "/api/v1/payments/orders"
    payload = {"invoice_id": str(uuid.uuid4()), "amount": "100.00"}
    response_body = {"success": True, "data": {"payment_id": str(uuid.uuid4())}}

    # 1. First check: not replayed
    is_replayed, status, body = await check_idempotency(db_session, business_id, key, endpoint, payload)
    assert is_replayed is False
    assert status is None
    assert body is None

    # 2. Record idempotency
    await record_idempotency(db_session, business_id, key, endpoint, payload, 201, response_body)
    await db_session.commit()

    # 3. Second check with same payload: returns replayed
    is_replayed, status, body = await check_idempotency(db_session, business_id, key, endpoint, payload)
    assert is_replayed is True
    assert status == 201
    assert body == response_body

    # 4. Third check with different payload: raises 422 IDEMPOTENCY_KEY_REUSED
    different_payload = {"invoice_id": str(uuid.uuid4()), "amount": "200.00"}
    with pytest.raises(UnprocessableEntityException) as exc:
        await check_idempotency(db_session, business_id, key, endpoint, different_payload)
    assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"
