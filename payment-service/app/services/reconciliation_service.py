"""Reconciliation Service for BizBrain Payment Service.

Repairs uncertain states, expires stale checkouts, reconciles capture outcomes with PayPal,
and monitors stuck allocations or review payments.
Only READS from PayPal. Never calls PayPal create or capture.
Reuses the canonical transactional completion function (complete_capture).
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.paypal_client import PayPalClientInterface, get_paypal_client
from app.core.alerts import alert
from app.core.clock import Clock, get_clock
from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.core.logging import get_logger
from app.core.money import format_amount
from app.models.audit import AuditLog
from app.models.outbox import OutboxEvent
from app.models.payment import Payment
from app.models.provider_reference import PaymentProviderReference
from app.services.completion_service import complete_capture
from app.services.state_machine import transition_payment

logger = get_logger("reconciliation_service")


class ReconciliationService:
    def __init__(
        self,
        paypal_client: Optional[PayPalClientInterface] = None,
        clock: Optional[Clock] = None,
    ):
        self.paypal_client = paypal_client or get_paypal_client()
        self.clock = clock or get_clock()
        self.paypal_consecutive_failures: int = 0
        # In-memory tracking of alerted dead outbox rows: event_id -> last_alerted_at
        self._dead_outbox_alerted_at: dict[uuid.UUID, datetime] = {}

    async def reconcile_expired_payments(
        self,
        batch_size: Optional[int] = None,
        business_id: Optional[uuid.UUID] = None,
    ) -> int:
        """Job A: Expire checkouts in CREATED or PAYER_APPROVED where expires_at <= now.

        Enforces per-payment transaction with FOR UPDATE SKIP LOCKED.
        Does NOT contact PayPal. Releases the partial unique index.
        """
        settings = get_settings()
        batch = batch_size or settings.RECON_BATCH_SIZE
        now = self.clock.now()

        session_factory = get_sessionmaker()
        expired_count = 0

        # Step 1: Query candidate payment IDs
        candidate_ids: list[tuple[uuid.UUID, uuid.UUID]] = []
        async with session_factory() as session:
            stmt = (
                select(Payment.payment_id, Payment.business_id)
                .where(
                    (Payment.status.in_(["CREATED", "PAYER_APPROVED"]))
                    & (Payment.expires_at <= now)
                )
            )
            if business_id:
                stmt = stmt.where(Payment.business_id == business_id)
            stmt = stmt.limit(batch)
            res = await session.execute(stmt)
            candidate_ids = res.all()

        # Step 2: Process each candidate in its own short transaction
        for pid, bid in candidate_ids:
            async with session_factory() as session:
                # Lock row with SKIP LOCKED
                stmt_lock = (
                    select(Payment)
                    .where(
                        (Payment.payment_id == pid)
                        & (Payment.business_id == bid)
                        & (Payment.status.in_(["CREATED", "PAYER_APPROVED"]))
                    )
                    .with_for_update(skip_locked=True)
                )
                res_lock = await session.execute(stmt_lock)
                payment = res_lock.scalar_one_or_none()

                # If locked by another worker or status changed (e.g. moved to CAPTURE_PENDING), skip
                if not payment:
                    continue

                # Re-verify expires_at <= now inside the lock
                if payment.expires_at > now:
                    continue

                await transition_payment(
                    db=session,
                    payment=payment,
                    new_status="EXPIRED",
                    actor_type="SYSTEM",
                    status_reason="CHECKOUT_EXPIRED",
                    request_id=f"recon-exp-{uuid.uuid4().hex[:8]}",
                )
                await session.commit()
                expired_count += 1
                logger.info("payment_expired_by_reconciliation", payment_id=str(pid), business_id=str(bid))

        return expired_count

    async def reconcile_uncertain_captures(
        self,
        batch_size: Optional[int] = None,
        business_id: Optional[uuid.UUID] = None,
    ) -> dict[str, int]:
        """Job B: Reconcile payments in CAPTURE_PENDING or REQUIRES_REVIEW with PayPal.

        Only READS from PayPal (get_order, get_capture). Never calls create or capture.
        Reuses canonical complete_capture function for completed captures.
        """
        settings = get_settings()
        batch = batch_size or settings.RECON_BATCH_SIZE
        now = self.clock.now()
        min_age_cutoff = now - timedelta(seconds=settings.RECON_MIN_AGE_SECONDS)
        no_capture_cutoff = now - timedelta(seconds=settings.RECON_NO_CAPTURE_WINDOW_SECONDS)

        session_factory = get_sessionmaker()
        results = {"completed": 0, "failed": 0, "requires_review": 0, "unchanged": 0}

        # Step 1: Query candidate payments older than min_age
        candidates: list[dict[str, Any]] = []
        async with session_factory() as session:
            stmt = (
                select(
                    Payment.payment_id,
                    Payment.business_id,
                    Payment.amount,
                    Payment.currency,
                    Payment.status,
                    Payment.created_at,
                    Payment.updated_at,
                    PaymentProviderReference.provider_order_id,
                    PaymentProviderReference.provider_capture_id,
                )
                .join(PaymentProviderReference, Payment.payment_id == PaymentProviderReference.payment_id)
                .where(
                    (Payment.status.in_(["CAPTURE_PENDING", "REQUIRES_REVIEW"]))
                    & (Payment.updated_at <= min_age_cutoff)
                )
            )
            if business_id:
                stmt = stmt.where(Payment.business_id == business_id)
            stmt = stmt.limit(batch)
            res = await session.execute(stmt)
            for row in res.all():
                candidates.append({
                    "payment_id": row.payment_id,
                    "business_id": row.business_id,
                    "amount": row.amount,
                    "currency": row.currency,
                    "status": row.status,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                    "provider_order_id": row.provider_order_id,
                    "provider_capture_id": row.provider_capture_id,
                })

        # Step 2: Reconcile each candidate
        for item in candidates:
            pid = item["payment_id"]
            bid = item["business_id"]
            order_id = item["provider_order_id"]
            stored_capture_id = item["provider_capture_id"]
            payment_amount = item["amount"]
            payment_currency = item["currency"]
            payment_status = item["status"]
            payment_updated_at = item["updated_at"]

            expected_amount_str = format_amount(payment_amount, payment_currency)

            # Check and lock payment in short transaction to ensure not concurrently modified
            async with session_factory() as session:
                stmt_chk = (
                    select(Payment.status)
                    .where((Payment.payment_id == pid) & (Payment.business_id == bid))
                    .with_for_update(skip_locked=True)
                )
                res_chk = await session.execute(stmt_chk)
                locked_status = res_chk.scalar_one_or_none()
                if not locked_status or locked_status not in ("CAPTURE_PENDING", "REQUIRES_REVIEW"):
                    continue

            # HTTP CALL OUTSIDE OF LOCK
            order_data: Optional[dict[str, Any]] = None
            paypal_error: Optional[Exception] = None
            order_not_found: bool = False

            try:
                order_data = await self.paypal_client.get_order(order_id)
                self.paypal_consecutive_failures = 0
            except Exception as exc:
                paypal_error = exc
                err_str = str(exc)
                if "404" in err_str or "NOT_FOUND" in err_str:
                    order_not_found = True
                    self.paypal_consecutive_failures = 0
                else:
                    self.paypal_consecutive_failures += 1
                    logger.warn(
                        "reconciliation_paypal_call_failed",
                        order_id=order_id,
                        failures=self.paypal_consecutive_failures,
                        error=err_str,
                    )
                    if self.paypal_consecutive_failures >= 5:
                        alert("RECON_PAYPAL_UNAVAILABLE", consecutive_failures=self.paypal_consecutive_failures)
                    results["unchanged"] += 1
                    continue

            # Case: Order not found at PayPal
            if order_not_found:
                logger.warn("reconciliation_order_not_found_at_paypal", payment_id=str(pid), order_id=order_id)
                alert("RECON_ORDER_NOT_FOUND", payment_id=str(pid), provider_order_id=order_id)
                async with session_factory() as session:
                    stmt_upd = (
                        select(Payment)
                        .where((Payment.payment_id == pid) & (Payment.business_id == bid))
                        .with_for_update(skip_locked=True)
                    )
                    p_row = (await session.execute(stmt_upd)).scalar_one_or_none()
                    if p_row and p_row.status == "CAPTURE_PENDING":
                        await transition_payment(
                            db=session,
                            payment=p_row,
                            new_status="REQUIRES_REVIEW",
                            actor_type="SYSTEM",
                            status_reason="ORDER_NOT_FOUND_AT_PAYPAL",
                        )
                        await session.commit()
                results["requires_review"] += 1
                continue

            # Extract capture data from order
            capture_obj: Optional[dict[str, Any]] = None
            if order_data:
                pus = order_data.get("purchase_units", [{}])
                captures = pus[0].get("payments", {}).get("captures", [])
                if captures:
                    capture_obj = captures[0]

            # If capture was not in order purchase_units but we have stored_capture_id, fetch it
            if not capture_obj and stored_capture_id:
                try:
                    capture_obj = await self.paypal_client.get_capture(stored_capture_id)
                except Exception:
                    capture_obj = None

            # DECISION LOGIC: What does PayPal show?
            if capture_obj:
                cap_id = capture_obj.get("id")
                cap_status = capture_obj.get("status")
                amount_obj = capture_obj.get("amount", {})
                cap_amount = amount_obj.get("value")
                cap_currency = amount_obj.get("currency_code")

                # 1. Capture status COMPLETED, amount and currency equal our payment
                if (
                    cap_status == "COMPLETED"
                    and cap_amount == expected_amount_str
                    and cap_currency == payment_currency
                ):
                    comp_res = await complete_capture(
                        business_id=bid,
                        payment_id=pid,
                        captured_amount=cap_amount,
                        captured_currency=cap_currency,
                        provider_capture_id=cap_id or stored_capture_id or f"CAP-{pid}",
                        raw_response=order_data,
                        actor_type="SYSTEM",
                        request_id=f"recon-cap-{uuid.uuid4().hex[:8]}",
                    )
                    if comp_res.already_completed:
                        results["unchanged"] += 1
                    else:
                        results["completed"] += 1
                        logger.info("reconciliation_completed_capture_success", payment_id=str(pid))
                    continue

                # 2. Capture COMPLETED but amount or currency differs
                elif cap_status == "COMPLETED":
                    reason = f"AMOUNT_MISMATCH: expected {expected_amount_str} {payment_currency}, got {cap_amount} {cap_currency}"
                    async with session_factory() as session:
                        stmt_upd = (
                            select(Payment)
                            .where((Payment.payment_id == pid) & (Payment.business_id == bid))
                            .with_for_update(skip_locked=True)
                        )
                        p_row = (await session.execute(stmt_upd)).scalar_one_or_none()
                        if p_row and p_row.status != "REQUIRES_REVIEW":
                            await transition_payment(
                                db=session,
                                payment=p_row,
                                new_status="REQUIRES_REVIEW",
                                actor_type="SYSTEM",
                                status_reason=reason,
                            )
                            await session.commit()

                    alert(
                        "RECON_AMOUNT_MISMATCH",
                        payment_id=str(pid),
                        business_id=str(bid),
                        expected_amount=expected_amount_str,
                        captured_amount=str(cap_amount),
                        expected_currency=payment_currency,
                        captured_currency=str(cap_currency),
                    )
                    results["requires_review"] += 1
                    continue

                # 3. Capture DECLINED, DENIED, or FAILED
                elif cap_status in ("DECLINED", "DENIED", "FAILED"):
                    async with session_factory() as session:
                        stmt_upd = (
                            select(Payment)
                            .where((Payment.payment_id == pid) & (Payment.business_id == bid))
                            .with_for_update(skip_locked=True)
                        )
                        p_row = (await session.execute(stmt_upd)).scalar_one_or_none()
                        if p_row and p_row.status in ("CAPTURE_PENDING", "REQUIRES_REVIEW"):
                            await transition_payment(
                                db=session,
                                payment=p_row,
                                new_status="FAILED",
                                actor_type="SYSTEM",
                                status_reason=f"PAYPAL_CAPTURE_{cap_status}",
                            )
                            pref_row = (await session.execute(
                                select(PaymentProviderReference).where(PaymentProviderReference.payment_id == pid)
                            )).scalar_one_or_none()
                            if pref_row:
                                pref_row.provider_status = cap_status
                                pref_row.raw_last_response = order_data
                            await session.commit()
                    results["failed"] += 1
                    logger.info("reconciliation_marked_payment_failed", payment_id=str(pid), status=cap_status)
                    continue

                # 4. Capture PENDING (PayPal still processing)
                elif cap_status == "PENDING":
                    # Leave unchanged; alert if older than 30 minutes
                    age_seconds = (now - payment_updated_at).total_seconds()
                    if age_seconds > settings.RECON_CAPTURE_PENDING_ALERT_SECONDS:
                        alert(
                            "RECON_CAPTURE_STILL_PENDING",
                            payment_id=str(pid),
                            provider_order_id=order_id,
                            provider_capture_id=cap_id,
                            age_seconds=int(age_seconds),
                        )
                    results["unchanged"] += 1
                    continue

            # 5. Order exists, NO capture found
            if payment_status == "CAPTURE_PENDING" and payment_updated_at <= no_capture_cutoff:
                # Been in CAPTURE_PENDING longer than RECON_NO_CAPTURE_WINDOW (15 min) -> FAILED
                async with session_factory() as session:
                    stmt_upd = (
                        select(Payment)
                        .where((Payment.payment_id == pid) & (Payment.business_id == bid))
                        .with_for_update(skip_locked=True)
                    )
                    p_row = (await session.execute(stmt_upd)).scalar_one_or_none()
                    if p_row and p_row.status == "CAPTURE_PENDING":
                        await transition_payment(
                            db=session,
                            payment=p_row,
                            new_status="FAILED",
                            actor_type="SYSTEM",
                            status_reason="NO_CAPTURE_AFTER_WINDOW",
                        )
                        pref_row = (await session.execute(
                            select(PaymentProviderReference).where(PaymentProviderReference.payment_id == pid)
                        )).scalar_one_or_none()
                        if pref_row:
                            pref_row.raw_last_response = order_data
                        await session.commit()
                results["failed"] += 1
                logger.info("reconciliation_no_capture_after_window_marked_failed", payment_id=str(pid))
                continue
            else:
                # Window not yet passed
                results["unchanged"] += 1

        return results

    async def monitor_system_health(self) -> dict[str, int]:
        """Job C: Monitoring only (no state changes).

        Raises alerts for:
        1. Payments COMPLETED with allocation_status = 'PENDING' > 15 minutes (ALLOCATION_STUCK).
        2. Payments in REQUIRES_REVIEW > 1 hour (REVIEW_STUCK).
        3. Outbox rows in DEAD (OUTBOX_DEAD_PRESENT, at most once per hour per row).
        """
        settings = get_settings()
        now = self.clock.now()
        alloc_cutoff = now - timedelta(seconds=settings.RECON_ALLOCATION_STUCK_SECONDS)
        review_cutoff = now - timedelta(seconds=settings.RECON_REVIEW_STUCK_SECONDS)

        session_factory = get_sessionmaker()
        alert_counts = {"allocation_stuck": 0, "review_stuck": 0, "dead_outbox": 0}

        async with session_factory() as session:
            # 1. Stuck Allocations
            stmt_alloc = (
                select(Payment.payment_id, Payment.business_id, Payment.amount, Payment.currency)
                .where(
                    (Payment.status == "COMPLETED")
                    & (Payment.allocation_status == "PENDING")
                    & (Payment.updated_at <= alloc_cutoff)
                )
            )
            alloc_rows = (await session.execute(stmt_alloc)).all()
            for row in alloc_rows:
                alert(
                    "ALLOCATION_STUCK",
                    payment_id=str(row.payment_id),
                    business_id=str(row.business_id),
                    amount=str(row.amount),
                    currency=row.currency,
                )
                alert_counts["allocation_stuck"] += 1

            # 2. Stuck Reviews
            stmt_rev = (
                select(Payment.payment_id, Payment.business_id, Payment.status_reason)
                .where(
                    (Payment.status == "REQUIRES_REVIEW")
                    & (Payment.updated_at <= review_cutoff)
                )
            )
            rev_rows = (await session.execute(stmt_rev)).all()
            for row in rev_rows:
                alert(
                    "REVIEW_STUCK",
                    payment_id=str(row.payment_id),
                    business_id=str(row.business_id),
                    status_reason=str(row.status_reason),
                )
                alert_counts["review_stuck"] += 1

            # 3. Dead Outbox Rows (at most once per hour per row)
            stmt_dead = select(OutboxEvent.event_id, OutboxEvent.aggregate_id, OutboxEvent.attempts, OutboxEvent.last_error).where(
                OutboxEvent.status == "DEAD"
            )
            dead_rows = (await session.execute(stmt_dead)).all()
            for row in dead_rows:
                eid = row.event_id
                last_alerted = self._dead_outbox_alerted_at.get(eid)
                if (
                    last_alerted is None
                    or (now - last_alerted).total_seconds() >= settings.RECON_DEAD_OUTBOX_ALERT_INTERVAL_SECONDS
                ):
                    alert(
                        "OUTBOX_DEAD_PRESENT",
                        event_id=str(eid),
                        payment_id=str(row.aggregate_id),
                        attempts=row.attempts,
                        last_error=str(row.last_error),
                    )
                    self._dead_outbox_alerted_at[eid] = now
                    alert_counts["dead_outbox"] += 1

        return alert_counts


_reconciliation_service: Optional[ReconciliationService] = None


def get_reconciliation_service() -> ReconciliationService:
    global _reconciliation_service
    if _reconciliation_service is None:
        _reconciliation_service = ReconciliationService()
    return _reconciliation_service
