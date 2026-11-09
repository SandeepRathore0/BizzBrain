import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictException
from app.models.audit import AuditLog
from app.models.payment import Payment
from app.services.state_machine import ALLOWED_TRANSITIONS, transition_payment


def create_dummy_payment(status: str = "CREATED", allocation_status: str = "NOT_APPLICABLE") -> Payment:
    now = datetime.now(timezone.utc)
    return Payment(
        payment_id=uuid.uuid4(),
        business_id=uuid.uuid4(),
        invoice_id=uuid.uuid4(),
        amount=Decimal("100.00"),
        currency="USD",
        provider="PAYPAL",
        status=status,
        allocation_status=allocation_status,
        idempotency_key=uuid.uuid4(),
        initiated_by_type="USER",
        expires_at=now,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_allowed_state_transitions(db_session: AsyncSession):
    # Test valid transitions from CREATED
    for next_status in ["PAYER_APPROVED", "CANCELLED", "EXPIRED", "FAILED"]:
        p = create_dummy_payment(status="CREATED")
        db_session.add(p)
        await transition_payment(
            db=db_session,
            payment=p,
            new_status=next_status,
            actor_type="USER",
            request_id="req-1",
        )
        assert p.status == next_status

    # Test valid transitions from PAYER_APPROVED
    for next_status in ["CAPTURE_PENDING", "CANCELLED", "EXPIRED", "FAILED"]:
        p = create_dummy_payment(status="PAYER_APPROVED")
        db_session.add(p)
        await transition_payment(
            db=db_session,
            payment=p,
            new_status=next_status,
            actor_type="USER",
        )
        assert p.status == next_status

    # Test valid transitions from CAPTURE_PENDING
    for next_status in ["COMPLETED", "FAILED", "REQUIRES_REVIEW"]:
        p = create_dummy_payment(status="CAPTURE_PENDING")
        db_session.add(p)
        alloc = "PENDING" if next_status == "COMPLETED" else "NOT_APPLICABLE"
        await transition_payment(
            db=db_session,
            payment=p,
            new_status=next_status,
            actor_type="USER",
            new_allocation_status=alloc,
        )
        assert p.status == next_status

    # Test valid transitions from REQUIRES_REVIEW
    for next_status in ["COMPLETED", "FAILED"]:
        p = create_dummy_payment(status="REQUIRES_REVIEW")
        db_session.add(p)
        alloc = "PENDING" if next_status == "COMPLETED" else "NOT_APPLICABLE"
        await transition_payment(
            db=db_session,
            payment=p,
            new_status=next_status,
            actor_type="SYSTEM",
            new_allocation_status=alloc,
        )
        assert p.status == next_status

    # Test COMPLETED -> REFUNDED
    p = create_dummy_payment(status="COMPLETED", allocation_status="PENDING")
    db_session.add(p)
    await transition_payment(
        db=db_session,
        payment=p,
        new_status="REFUNDED",
        actor_type="SYSTEM",
    )
    assert p.status == "REFUNDED"


@pytest.mark.asyncio
async def test_forbidden_state_transitions(db_session: AsyncSession):
    # CREATED -> COMPLETED forbidden
    p = create_dummy_payment(status="CREATED")
    with pytest.raises(ConflictException) as exc:
        await transition_payment(db_session, p, "COMPLETED", "USER")
    assert exc.value.code == "INVALID_STATE_TRANSITION"

    # Terminal states cannot transition to anything
    for term_status in ["FAILED", "CANCELLED", "EXPIRED", "REFUNDED"]:
        p = create_dummy_payment(status=term_status)
        for target in ["CREATED", "PAYER_APPROVED", "COMPLETED", "FAILED"]:
            with pytest.raises(ConflictException):
                await transition_payment(db_session, p, target, "USER")


@pytest.mark.asyncio
async def test_allocation_status_transitions(db_session: AsyncSession):
    p = create_dummy_payment(status="CAPTURE_PENDING", allocation_status="NOT_APPLICABLE")
    # Valid: move to COMPLETED with PENDING allocation
    await transition_payment(db_session, p, "COMPLETED", "SYSTEM", new_allocation_status="PENDING")
    assert p.allocation_status == "PENDING"

    # Valid: PENDING -> ALLOCATED
    await transition_payment(db_session, p, "COMPLETED", "SYSTEM", new_allocation_status="ALLOCATED")
    assert p.allocation_status == "ALLOCATED"

    # Invalid: ALLOCATED -> REJECTED (terminal)
    with pytest.raises(ConflictException):
        await transition_payment(db_session, p, "COMPLETED", "SYSTEM", new_allocation_status="REJECTED")


@pytest.mark.asyncio
async def test_transition_writes_audit_log(db_session: AsyncSession):
    p = create_dummy_payment(status="CREATED")
    db_session.add(p)
    req_id = "test-req-audit-123"

    await transition_payment(
        db=db_session,
        payment=p,
        new_status="PAYER_APPROVED",
        actor_type="USER",
        actor_id="user-123",
        status_reason="Payer approved on PayPal",
        request_id=req_id,
    )
    await db_session.flush()

    # Verify audit row created
    stmt = select(AuditLog).where(AuditLog.entity_id == p.payment_id)
    result = await db_session.execute(stmt)
    audit = result.scalar_one_or_none()
    assert audit is not None
    assert audit.action == "PAYMENT_STATUS_TRANSITION_PAYER_APPROVED"
    assert audit.actor_type == "USER"
    assert audit.actor_id == "user-123"
    assert audit.before["status"] == "CREATED"
    assert audit.after["status"] == "PAYER_APPROVED"
    assert audit.request_id == req_id
