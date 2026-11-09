"""Unified, atomic, and idempotent capture completion service.

This is the SINGLE transactional completion path for Payment Service.
Shared by CaptureService, WebhookService, and ReconciliationWorker.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.alerts import alert
from app.core.database import get_sessionmaker
from app.core.errors import ConflictException, NotFoundException
from app.core.logging import get_logger
from app.core.money import format_amount
from app.models.outbox import OutboxEvent
from app.models.payment import Payment
from app.models.provider_reference import PaymentProviderReference
from app.repositories.payment_repository import PaymentRepository
from app.services.state_machine import transition_payment

logger = get_logger("completion_service")


@dataclass
class CompletionResult:
    payment_id: uuid.UUID
    status: str
    allocation_status: str
    amount: str
    currency: str
    provider_capture_id: Optional[str]
    outbox_event_id: Optional[uuid.UUID] = None
    already_completed: bool = False
    is_mismatch: bool = False
    is_late_capture_after_failed: bool = False


async def complete_capture(
    business_id: uuid.UUID,
    payment_id: uuid.UUID,
    captured_amount: str,
    captured_currency: str,
    provider_capture_id: str,
    raw_response: Optional[dict[str, Any]] = None,
    actor_type: str = "SYSTEM",
    actor_id: Optional[str] = None,
    request_id: Optional[str] = None,
    session: Optional[AsyncSession] = None,
) -> CompletionResult:
    """Atomically and idempotently complete a payment capture.

    1. Locks the payment row with FOR UPDATE.
    2. If already COMPLETED, returns the existing result with no side effects.
    3. If FAILED (e.g. late webhook after reconciliation FAILED), emits RECON_LATE_CAPTURE_AFTER_FAILED
       alert, does not throw, and leaves status FAILED.
    4. Verifies amount and currency against our payment record:
       Mismatch moves payment to REQUIRES_REVIEW, creates NO outbox row, writes audit,
       and raises alert AMOUNT_MISMATCH.
    5. Transitions payment to COMPLETED and allocation_status to PENDING through the state machine.
    6. Stores provider_capture_id and updates provider reference.
    7. Inserts PaymentCaptured outbox row (ON CONFLICT (event_type, aggregate_id) DO NOTHING).
    """
    if session is not None:
        return await _execute_complete_capture(
            session=session,
            business_id=business_id,
            payment_id=payment_id,
            captured_amount=captured_amount,
            captured_currency=captured_currency,
            provider_capture_id=provider_capture_id,
            raw_response=raw_response,
            actor_type=actor_type,
            actor_id=actor_id,
            request_id=request_id,
        )

    session_factory = get_sessionmaker()
    async with session_factory() as local_session:
        result = await _execute_complete_capture(
            session=local_session,
            business_id=business_id,
            payment_id=payment_id,
            captured_amount=captured_amount,
            captured_currency=captured_currency,
            provider_capture_id=provider_capture_id,
            raw_response=raw_response,
            actor_type=actor_type,
            actor_id=actor_id,
            request_id=request_id,
        )
        await local_session.commit()
        return result


async def _execute_complete_capture(
    session: AsyncSession,
    business_id: uuid.UUID,
    payment_id: uuid.UUID,
    captured_amount: str,
    captured_currency: str,
    provider_capture_id: str,
    raw_response: Optional[dict[str, Any]],
    actor_type: str,
    actor_id: Optional[str],
    request_id: Optional[str],
) -> CompletionResult:
    repo = PaymentRepository(session)
    payment = await repo.get_payment_by_id(business_id=business_id, payment_id=payment_id, for_update=True)
    if not payment:
        raise NotFoundException(
            code="PAYMENT_NOT_FOUND",
            message=f"Payment {payment_id} not found.",
        )

    expected_amount_str = format_amount(payment.amount, payment.currency)
    clean_capture_id = str(provider_capture_id).strip() if provider_capture_id else ""
    clean_request_id = (request_id.strip().replace(" ", "_") if request_id and request_id.strip() else f"req-{uuid.uuid4().hex[:12]}")

    # 1. Idempotency check: if already COMPLETED, return stored result with no side effects
    if payment.status == "COMPLETED":
        logger.info("payment_already_completed_idempotent", payment_id=str(payment_id))
        cap_id = payment.provider_reference.provider_capture_id if payment.provider_reference else clean_capture_id
        return CompletionResult(
            payment_id=payment.payment_id,
            status=payment.status,
            allocation_status=payment.allocation_status,
            amount=expected_amount_str,
            currency=payment.currency,
            provider_capture_id=cap_id,
            already_completed=True,
        )

    # 2. Check if terminal FAILED (late capture arrival after reconciliation marked FAILED)
    if payment.status == "FAILED":
        logger.warn(
            "late_capture_after_payment_failed",
            payment_id=str(payment_id),
            provider_capture_id=clean_capture_id,
        )
        alert(
            "RECON_LATE_CAPTURE_AFTER_FAILED",
            payment_id=str(payment_id),
            business_id=str(business_id),
            provider_capture_id=clean_capture_id,
            request_id=clean_request_id,
        )
        return CompletionResult(
            payment_id=payment.payment_id,
            status=payment.status,
            allocation_status=payment.allocation_status,
            amount=expected_amount_str,
            currency=payment.currency,
            provider_capture_id=clean_capture_id,
            is_late_capture_after_failed=True,
        )

    # 3. Amount and currency verification
    curr_match = (captured_currency.upper() == payment.currency.upper())
    amount_match = (captured_amount == expected_amount_str)

    if not (curr_match and amount_match):
        reason = f"AMOUNT_MISMATCH: expected {expected_amount_str} {payment.currency}, got {captured_amount} {captured_currency}"
        logger.error(
            "payment_capture_amount_mismatch",
            payment_id=str(payment_id),
            expected_amount=expected_amount_str,
            captured_amount=captured_amount,
            expected_currency=payment.currency,
            captured_currency=captured_currency,
        )
        if payment.status in ("CREATED", "PAYER_APPROVED", "CAPTURE_PENDING", "REQUIRES_REVIEW"):
            if payment.status == "CREATED":
                await transition_payment(
                    db=session,
                    payment=payment,
                    new_status="PAYER_APPROVED",
                    actor_type=actor_type,
                    actor_id=actor_id,
                    request_id=clean_request_id,
                )
            if payment.status == "PAYER_APPROVED":
                await transition_payment(
                    db=session,
                    payment=payment,
                    new_status="CAPTURE_PENDING",
                    actor_type=actor_type,
                    actor_id=actor_id,
                    request_id=clean_request_id,
                )
            await transition_payment(
                db=session,
                payment=payment,
                new_status="REQUIRES_REVIEW",
                actor_type=actor_type,
                actor_id=actor_id,
                status_reason=reason,
                request_id=clean_request_id,
            )
            if payment.provider_reference and raw_response:
                payment.provider_reference.raw_last_response = raw_response

        # Raise alert AMOUNT_MISMATCH
        alert(
            "AMOUNT_MISMATCH",
            payment_id=str(payment_id),
            business_id=str(business_id),
            expected_amount=expected_amount_str,
            captured_amount=captured_amount,
            expected_currency=payment.currency,
            captured_currency=captured_currency,
            request_id=clean_request_id,
        )

        return CompletionResult(
            payment_id=payment.payment_id,
            status=payment.status,
            allocation_status=payment.allocation_status,
            amount=expected_amount_str,
            currency=payment.currency,
            provider_capture_id=clean_capture_id,
            is_mismatch=True,
        )

    # 4. Transition to COMPLETED through the state machine
    if payment.status == "CREATED":
        await transition_payment(
            db=session,
            payment=payment,
            new_status="PAYER_APPROVED",
            actor_type=actor_type,
            actor_id=actor_id,
            request_id=clean_request_id,
        )
    if payment.status == "PAYER_APPROVED":
        await transition_payment(
            db=session,
            payment=payment,
            new_status="CAPTURE_PENDING",
            actor_type=actor_type,
            actor_id=actor_id,
            request_id=clean_request_id,
        )
    if payment.status in ("CAPTURE_PENDING", "REQUIRES_REVIEW"):
        await transition_payment(
            db=session,
            payment=payment,
            new_status="COMPLETED",
            new_allocation_status="PENDING",
            actor_type=actor_type,
            actor_id=actor_id,
            request_id=clean_request_id,
        )

    # 5. Update provider reference
    stmt_ref = select(PaymentProviderReference).where(
        PaymentProviderReference.payment_id == payment.payment_id
    )
    res_ref = await session.execute(stmt_ref)
    ref = res_ref.scalar_one_or_none()
    if ref:
        ref.provider_capture_id = clean_capture_id
        ref.provider_status = "COMPLETED"
        if raw_response:
            ref.raw_last_response = raw_response

    # 6. Insert Outbox row (PaymentCaptured) idempotently
    event_id = uuid.uuid4()
    now_utc = datetime.now(timezone.utc)
    occurred_at_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    outbox_payload = {
        "event_id": str(event_id).lower(),
        "event_type": "PaymentCaptured",
        "event_version": 1,
        "occurred_at": occurred_at_str,
        "request_id": clean_request_id,
        "business_id": str(payment.business_id).lower(),
        "payment_id": str(payment.payment_id).lower(),
        "invoice_id": str(payment.invoice_id).lower(),
        "amount": expected_amount_str,
        "currency": payment.currency.upper(),
        "provider_capture_id": clean_capture_id,
    }

    insert_stmt = (
        pg_insert(OutboxEvent)
        .values(
            event_id=event_id,
            aggregate_id=payment.payment_id,
            event_type="PaymentCaptured",
            event_version=1,
            payload=outbox_payload,
            status="PENDING",
            attempts=0,
            next_retry_at=now_utc,
            created_at=now_utc,
        )
        .on_conflict_do_nothing(
            index_elements=["event_type", "aggregate_id"]
        )
    )
    await session.execute(insert_stmt)

    return CompletionResult(
        payment_id=payment.payment_id,
        status="COMPLETED",
        allocation_status="PENDING",
        amount=expected_amount_str,
        currency=payment.currency,
        provider_capture_id=clean_capture_id,
        outbox_event_id=event_id,
    )
