import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_audit_log_immutable_trigger_blocks_update_and_delete(migrator_session):
    """Trigger must block UPDATE and DELETE on audit_log even for owner role."""
    audit_id = uuid.uuid4()
    # Insert row
    await migrator_session.execute(
        text("""
            INSERT INTO audit_log (audit_id, actor_type, action, entity_type, occurred_at)
            VALUES (:id, 'SYSTEM', 'TEST_ACTION', 'TEST', now())
        """),
        {"id": audit_id},
    )
    await migrator_session.commit()

    # Attempt UPDATE as owner role
    with pytest.raises(DBAPIError) as exc_update:
        await migrator_session.execute(
            text("UPDATE audit_log SET action = 'MODIFIED' WHERE audit_id = :id"),
            {"id": audit_id},
        )
    await migrator_session.rollback()
    assert "audit_log is append-only" in str(exc_update.value)

    # Attempt DELETE as owner role
    with pytest.raises(DBAPIError) as exc_delete:
        await migrator_session.execute(
            text("DELETE FROM audit_log WHERE audit_id = :id"),
            {"id": audit_id},
        )
    await migrator_session.rollback()
    assert "audit_log is append-only" in str(exc_delete.value)


@pytest.mark.asyncio
async def test_payment_app_role_cannot_delete(db_session: AsyncSession):
    """Runtime role payment_app must have DELETE revoked on all business tables, including outbox_events."""
    tables = ["payments", "payment_provider_references", "idempotency_keys", "audit_log", "outbox_events"]
    for table in tables:
        with pytest.raises(ProgrammingError) as exc:
            await db_session.execute(text(f"DELETE FROM {table}"))
        await db_session.rollback()
        assert "permission denied" in str(exc.value).lower(), f"Expected permission denied for DELETE on {table}"


@pytest.mark.asyncio
async def test_payments_amount_check_constraint(db_session: AsyncSession):
    """Payments amount must be strictly > 0."""
    business_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("""
                INSERT INTO payments (
                    payment_id, business_id, invoice_id, amount, currency, status,
                    allocation_status, idempotency_key, initiated_by_type, expires_at
                ) VALUES (
                    :pid, :bid, :iid, 0.00, 'USD', 'CREATED',
                    'NOT_APPLICABLE', :ikey, 'USER', now() + interval '30 minutes'
                )
            """),
            {
                "pid": uuid.uuid4(),
                "bid": business_id,
                "iid": invoice_id,
                "ikey": uuid.uuid4(),
            },
        )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_payments_allocation_status_coherence_constraint(db_session: AsyncSession):
    """allocation_status <> 'NOT_APPLICABLE' only when status IN ('COMPLETED','REFUNDED')."""
    business_id = uuid.uuid4()
    invoice_id = uuid.uuid4()

    # Invalid: CREATED with PENDING allocation
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("""
                INSERT INTO payments (
                    payment_id, business_id, invoice_id, amount, currency, status,
                    allocation_status, idempotency_key, initiated_by_type, expires_at
                ) VALUES (
                    :pid, :bid, :iid, 100.00, 'USD', 'CREATED',
                    'PENDING', :ikey, 'USER', now() + interval '30 minutes'
                )
            """),
            {
                "pid": uuid.uuid4(),
                "bid": business_id,
                "iid": invoice_id,
                "ikey": uuid.uuid4(),
            },
        )
    await db_session.rollback()

    # Invalid: COMPLETED with NOT_APPLICABLE allocation
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("""
                INSERT INTO payments (
                    payment_id, business_id, invoice_id, amount, currency, status,
                    allocation_status, idempotency_key, initiated_by_type, expires_at
                ) VALUES (
                    :pid, :bid, :iid, 100.00, 'USD', 'COMPLETED',
                    'NOT_APPLICABLE', :ikey, 'USER', now() + interval '30 minutes'
                )
            """),
            {
                "pid": uuid.uuid4(),
                "bid": business_id,
                "iid": invoice_id,
                "ikey": uuid.uuid4(),
            },
        )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_partial_unique_index_one_active_checkout_per_invoice(db_session: AsyncSession):
    """Partial unique index allows only one active checkout per invoice."""
    business_id = uuid.uuid4()
    invoice_id = uuid.uuid4()

    # 1. First active payment (CREATED)
    pid1 = uuid.uuid4()
    await db_session.execute(
        text("""
            INSERT INTO payments (
                payment_id, business_id, invoice_id, amount, currency, status,
                allocation_status, idempotency_key, initiated_by_type, expires_at
            ) VALUES (
                :pid, :bid, :iid, 50.00, 'USD', 'CREATED',
                'NOT_APPLICABLE', :ikey, 'USER', now() + interval '30 minutes'
            )
        """),
        {
            "pid": pid1,
            "bid": business_id,
            "iid": invoice_id,
            "ikey": uuid.uuid4(),
        },
    )
    await db_session.commit()

    # 2. Second active payment (PAYER_APPROVED) for same invoice must fail
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("""
                INSERT INTO payments (
                    payment_id, business_id, invoice_id, amount, currency, status,
                    allocation_status, idempotency_key, initiated_by_type, expires_at
                ) VALUES (
                    :pid, :bid, :iid, 50.00, 'USD', 'PAYER_APPROVED',
                    'NOT_APPLICABLE', :ikey, 'USER', now() + interval '30 minutes'
                )
            """),
            {
                "pid": uuid.uuid4(),
                "bid": business_id,
                "iid": invoice_id,
                "ikey": uuid.uuid4(),
            },
        )
    await db_session.rollback()

    # 3. Transition first payment to CANCELLED (inactive)
    await db_session.execute(
        text("UPDATE payments SET status = 'CANCELLED' WHERE payment_id = :pid"),
        {"pid": pid1},
    )
    await db_session.commit()

    # 4. Now a new active payment for the same invoice must succeed
    pid2 = uuid.uuid4()
    await db_session.execute(
        text("""
            INSERT INTO payments (
                payment_id, business_id, invoice_id, amount, currency, status,
                allocation_status, idempotency_key, initiated_by_type, expires_at
            ) VALUES (
                :pid, :bid, :iid, 50.00, 'USD', 'CREATED',
                'NOT_APPLICABLE', :ikey, 'USER', now() + interval '30 minutes'
            )
        """),
        {
            "pid": pid2,
            "bid": business_id,
            "iid": invoice_id,
            "ikey": uuid.uuid4(),
        },
    )
    await db_session.commit()
