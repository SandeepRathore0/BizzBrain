"""Outbox Service for BizBrain Payment Service.

Delivers PaymentCaptured outbox events to the Invoice Service with at-least-once semantics.
Maintains short claim transactions, validates against JSON Schema, executes HTTP outside of locks,
and safely updates states with full exponential backoff and circuit breaker protection.
"""
import copy
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
import httpx
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.user_service_client import UserServiceClient, get_user_service_client
from app.core.alerts import alert
from app.core.clock import Clock, RandomSource, get_clock, get_random_source
from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.core.logging import get_logger
from app.models.audit import AuditLog
from app.models.outbox import OutboxEvent
from app.models.payment import Payment
from app.services.state_machine import transition_payment

logger = get_logger("outbox_service")

# Resolve contract schema path
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "events" / "PaymentCaptured.v1.json"


class OutboxService:
    def __init__(
        self,
        clock: Optional[Clock] = None,
        random_source: Optional[RandomSource] = None,
        user_service_client: Optional[UserServiceClient] = None,
        schema_path: Optional[Path] = None,
    ):
        self.clock = clock or get_clock()
        self.random_source = random_source or get_random_source()
        self.user_service_client = user_service_client or get_user_service_client()
        self.schema_path = schema_path or SCHEMA_PATH

        # Load and compile schema at startup; refuse to start if missing or invalid
        if not self.schema_path.exists():
            raise RuntimeError(f"PaymentCaptured schema not found at {self.schema_path}. Outbox worker cannot start.")

        try:
            schema_data = json.loads(self.schema_path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema_data)
            self.validator = Draft202012Validator(schema_data, format_checker=FormatChecker())
        except Exception as exc:
            raise RuntimeError(f"Failed to compile PaymentCaptured schema: {exc}")

        # Circuit breaker state
        self.consecutive_failures: int = 0
        self.circuit_open_until: Optional[datetime] = None

    def is_circuit_open(self) -> bool:
        settings = get_settings()
        if self.consecutive_failures >= settings.OUTBOX_CIRCUIT_BREAKER_FAILURES:
            now = self.clock.now()
            if self.circuit_open_until and now < self.circuit_open_until:
                return True
            # Cooldown passed: reset
            self.consecutive_failures = 0
            self.circuit_open_until = None
        return False

    def record_circuit_failure(self) -> None:
        settings = get_settings()
        self.consecutive_failures += 1
        if self.consecutive_failures >= settings.OUTBOX_CIRCUIT_BREAKER_FAILURES:
            cooldown = timedelta(seconds=settings.OUTBOX_CIRCUIT_BREAKER_COOLDOWN_SECONDS)
            self.circuit_open_until = self.clock.now() + cooldown
            logger.warn(
                "outbox_circuit_breaker_opened",
                consecutive_failures=self.consecutive_failures,
                cooldown_seconds=settings.OUTBOX_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
            )

    def record_circuit_success(self) -> None:
        self.consecutive_failures = 0
        self.circuit_open_until = None

    def validate_payload(self, payload: dict[str, Any]) -> list[str]:
        """Validate payload against PaymentCaptured.v1.json with FormatChecker."""
        return [err.message for err in self.validator.iter_errors(payload)]

    async def claim_batch(
        self,
        batch_size: Optional[int] = None,
        visibility_timeout_seconds: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Claim a batch of pending or expired in-flight outbox events in a SHORT transaction."""
        settings = get_settings()
        batch = batch_size or settings.OUTBOX_BATCH_SIZE
        vis_timeout = visibility_timeout_seconds or settings.OUTBOX_VISIBILITY_TIMEOUT_SECONDS
        now = self.clock.now()
        visibility_deadline = now + timedelta(seconds=vis_timeout)

        session_factory = get_sessionmaker()
        claimed_events: list[dict[str, Any]] = []

        async with session_factory() as session:
            # SELECT FOR UPDATE SKIP LOCKED
            stmt = (
                select(OutboxEvent)
                .where(
                    ((OutboxEvent.status == "PENDING") & (OutboxEvent.next_retry_at <= now))
                    | ((OutboxEvent.status == "IN_FLIGHT") & (OutboxEvent.next_retry_at <= now))
                )
                .order_by(OutboxEvent.next_retry_at.asc(), OutboxEvent.created_at.asc())
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
            res = await session.execute(stmt)
            rows = res.scalars().all()

            for row in rows:
                row.status = "IN_FLIGHT"
                row.attempts += 1
                row.next_retry_at = visibility_deadline
                claimed_events.append({
                    "event_id": row.event_id,
                    "aggregate_id": row.aggregate_id,
                    "event_type": row.event_type,
                    "event_version": row.event_version,
                    "payload": copy.deepcopy(row.payload),
                    "attempts": row.attempts,
                    "status": "IN_FLIGHT",
                })

            await session.commit()

        return claimed_events

    async def process_event(self, event_data: dict[str, Any], http_client: Optional[httpx.AsyncClient] = None) -> str:
        """Process a single claimed event outside of database transactions."""
        event_id: uuid.UUID = event_data["event_id"]
        aggregate_id: uuid.UUID = event_data["aggregate_id"]
        payload: dict[str, Any] = event_data["payload"]
        attempts: int = event_data["attempts"]
        req_id: str = payload.get("request_id", "")

        log = logger.bind(event_id=str(event_id), request_id=req_id)

        # 1. Schema Validation BEFORE every send
        validation_errors = self.validate_payload(payload)
        if validation_errors:
            err_summary = "; ".join(validation_errors)[:500]
            log.error("outbox_payload_invalid", errors=err_summary)
            await self._mark_dead(
                event_id=event_id,
                aggregate_id=aggregate_id,
                error_msg=f"SCHEMA_VALIDATION_ERROR: {err_summary}",
                req_id=req_id,
            )
            alert("OUTBOX_PAYLOAD_INVALID", event_id=str(event_id), error=err_summary, request_id=req_id)
            return "DEAD_INVALID_SCHEMA"

        # 2. Authenticate with User Service service token
        service_token = await self.user_service_client.get_service_token()

        # 3. HTTP Delivery to Invoice Service
        settings = get_settings()
        url = f"{settings.INVOICE_SERVICE_BASE_URL}/internal/v1/payment-events"
        headers = {
            "Authorization": f"Bearer {service_token}",
            "X-Request-ID": req_id,
            "Idempotency-Key": str(event_id).lower(),
            "Content-Type": "application/json",
        }

        # Local helper for sending HTTP request
        async def _do_send(tok: str) -> httpx.Response:
            hdrs = dict(headers)
            hdrs["Authorization"] = f"Bearer {tok}"
            if http_client:
                return await http_client.post(url, json=payload, headers=hdrs, timeout=httpx.Timeout(5.0, connect=3.0))
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
                return await client.post(url, json=payload, headers=hdrs)

        response: Optional[httpx.Response] = None
        network_exc: Optional[Exception] = None

        try:
            response = await _do_send(service_token)
            # If 401: discard cached token, fetch new one, and retry once in the same attempt
            if response.status_code == 401:
                log.warn("outbox_service_token_401_refreshing")
                self.user_service_client.clear_token_cache()
                new_token = await self.user_service_client.get_service_token(force_refresh=True)
                response = await _do_send(new_token)
        except Exception as exc:
            network_exc = exc
            log.warn("outbox_http_network_exception", error=str(exc))

        # 4. Handle HTTP Response in a NEW short transaction
        return await self._handle_result(
            event_id=event_id,
            aggregate_id=aggregate_id,
            attempts=attempts,
            req_id=req_id,
            response=response,
            network_exc=network_exc,
        )

    async def _handle_result(
        self,
        event_id: uuid.UUID,
        aggregate_id: uuid.UUID,
        attempts: int,
        req_id: str,
        response: Optional[httpx.Response],
        network_exc: Optional[Exception],
    ) -> str:
        """Handle Invoice Service response according to Table in Section 3.5."""
        settings = get_settings()
        now = self.clock.now()

        # Network/Timeout exception
        if network_exc is not None or response is None:
            err_msg = f"NETWORK_ERROR: {str(network_exc)}"[:500]
            self.record_circuit_failure()
            await self._schedule_retry_or_dead(event_id, aggregate_id, attempts, err_msg, req_id)
            return "RETRY_SCHEDULED"

        status_code = response.status_code

        # HTTP 200: Parse outcome
        if status_code == 200:
            try:
                body = response.json()
                data = body.get("data", body)
                outcome = data.get("outcome") or data.get("status")
                reason_code = data.get("reason_code")
                if not outcome and body.get("error", {}).get("code") == "PAYMENT_REJECTED":
                    outcome = "REJECTED"
                    reason_code = body.get("error", {}).get("message")
            except Exception as parse_exc:
                outcome = None
                reason_code = None

            if outcome in ("APPLIED", "DUPLICATE_IGNORED"):
                # Outcome APPLIED or DUPLICATE_IGNORED: DELIVERED, allocation PENDING -> ALLOCATED
                self.record_circuit_success()
                delivered = await self._mark_delivered_and_allocate(
                    event_id=event_id,
                    payment_id=aggregate_id,
                    target_allocation="ALLOCATED",
                    req_id=req_id,
                )
                return "DELIVERED" if delivered else "ALREADY_HANDLED"

            elif outcome == "REJECTED":
                # Outcome REJECTED: DELIVERED, allocation PENDING -> REJECTED, alert ALLOCATION_REJECTED
                self.record_circuit_success()
                delivered = await self._mark_delivered_and_allocate(
                    event_id=event_id,
                    payment_id=aggregate_id,
                    target_allocation="REJECTED",
                    reason_code=reason_code or "REJECTED_BY_INVOICE_SERVICE",
                    req_id=req_id,
                )
                alert(
                    "ALLOCATION_REJECTED",
                    event_id=str(event_id),
                    payment_id=str(aggregate_id),
                    reason=reason_code or "REJECTED_BY_INVOICE_SERVICE",
                    request_id=req_id,
                )
                return "DELIVERED_REJECTED" if delivered else "ALREADY_HANDLED"

            else:
                # 200 with unknown or missing outcome: Treat as retryable failure
                truncated_body = response.text[:200]
                err_msg = f"UNKNOWN_200_OUTCOME: {truncated_body}"
                logger.warn("outbox_unknown_outcome_received", status_code=200, body=truncated_body)
                self.record_circuit_failure()
                await self._schedule_retry_or_dead(event_id, aggregate_id, attempts, err_msg, req_id)
                return "RETRY_SCHEDULED"

        # HTTP 401 (failed even after one refresh)
        if status_code == 401:
            err_msg = "AUTHENTICATION_FAILED_401"
            self.record_circuit_failure()
            alert("OUTBOX_AUTH_FAILURE", event_id=str(event_id), request_id=req_id)
            await self._schedule_retry_or_dead(event_id, aggregate_id, attempts, err_msg, req_id)
            return "RETRY_SCHEDULED"

        # HTTP 400 or 422: Non-retryable consumer rejection -> DEAD immediately
        if status_code in (400, 422):
            err_msg = f"CONSUMER_REJECTED_{status_code}: {response.text[:400]}"
            self.record_circuit_failure()
            await self._mark_dead(event_id, aggregate_id, err_msg, req_id)
            alert("OUTBOX_REJECTED_BY_CONSUMER", event_id=str(event_id), status_code=status_code, request_id=req_id)
            return "DEAD_CONSUMER_REJECTED"

        # HTTP 403 or 404: Retryable failure; alert after 3 consecutive attempts
        if status_code in (403, 404):
            err_msg = f"CONSUMER_HTTP_{status_code}"
            self.record_circuit_failure()
            if attempts >= 3:
                alert("OUTBOX_CONSUMER_UNAVAILABLE", event_id=str(event_id), status_code=status_code, attempts=attempts, request_id=req_id)
            await self._schedule_retry_or_dead(event_id, aggregate_id, attempts, err_msg, req_id)
            return "RETRY_SCHEDULED"

        # HTTP 409, 429, 5xx, or other: Retryable failure
        err_msg = f"HTTP_{status_code}: {response.text[:200]}"
        self.record_circuit_failure()
        await self._schedule_retry_or_dead(event_id, aggregate_id, attempts, err_msg, req_id)
        return "RETRY_SCHEDULED"

    async def _mark_delivered_and_allocate(
        self,
        event_id: uuid.UUID,
        payment_id: uuid.UUID,
        target_allocation: str,
        reason_code: Optional[str] = None,
        req_id: Optional[str] = None,
    ) -> bool:
        """In one transaction: guarded update outbox row to DELIVERED and update payment allocation_status."""
        session_factory = get_sessionmaker()
        now = self.clock.now()

        async with session_factory() as session:
            # Guarded update: WHERE event_id = :id AND status = 'IN_FLIGHT'
            stmt = (
                update(OutboxEvent)
                .where((OutboxEvent.event_id == event_id) & (OutboxEvent.status == "IN_FLIGHT"))
                .values(
                    status="DELIVERED",
                    delivered_at=now,
                    last_error=None,
                )
            )
            res = await session.execute(stmt)
            if res.rowcount == 0:
                logger.info("outbox_guarded_update_skipped_another_worker_handled", event_id=str(event_id))
                return False

            # Update Payment allocation_status
            stmt_pay = select(Payment).where(Payment.payment_id == payment_id).with_for_update()
            res_pay = await session.execute(stmt_pay)
            payment = res_pay.scalar_one_or_none()

            if payment:
                if payment.allocation_status == "PENDING":
                    await transition_payment(
                        db=session,
                        payment=payment,
                        new_status=payment.status,
                        new_allocation_status=target_allocation,
                        actor_type="SYSTEM",
                        status_reason=reason_code,
                        request_id=req_id,
                    )
                elif payment.allocation_status == target_allocation:
                    logger.info("payment_already_in_target_allocation", payment_id=str(payment_id), allocation=target_allocation)
                else:
                    logger.warn("payment_unexpected_allocation_status", payment_id=str(payment_id), current=payment.allocation_status, target=target_allocation)

                # Write audit row for outbox delivery
                audit = AuditLog(
                    audit_id=uuid.uuid4(),
                    business_id=payment.business_id,
                    actor_type="SYSTEM",
                    action=f"OUTBOX_DELIVERED_{target_allocation}",
                    entity_type="outbox_event",
                    entity_id=event_id,
                    before={"status": "IN_FLIGHT"},
                    after={"status": "DELIVERED", "delivered_at": now.isoformat()},
                    request_id=req_id,
                    occurred_at=now,
                )
                session.add(audit)

            await session.commit()
            return True

    async def _schedule_retry_or_dead(
        self,
        event_id: uuid.UUID,
        aggregate_id: uuid.UUID,
        attempts: int,
        error_msg: str,
        req_id: Optional[str] = None,
    ) -> None:
        """Calculate exponential backoff with full jitter, or mark DEAD if max_attempts reached."""
        settings = get_settings()
        now = self.clock.now()

        if attempts >= settings.OUTBOX_MAX_ATTEMPTS:
            # Exceeded max attempts -> DEAD
            await self._mark_dead(event_id, aggregate_id, error_msg, req_id)
            alert("OUTBOX_DEAD", event_id=str(event_id), attempts=attempts, error=error_msg, request_id=req_id)
            return

        # Full jitter exponential backoff capped at 600s
        # delay = random_between(0, min(600, 5 * 2^(attempts-1)))
        max_backoff = min(600.0, 5.0 * (2 ** (attempts - 1)))
        delay_seconds = self.random_source.uniform(0.0, max_backoff)
        next_retry = now + timedelta(seconds=delay_seconds)

        session_factory = get_sessionmaker()
        async with session_factory() as session:
            stmt = (
                update(OutboxEvent)
                .where((OutboxEvent.event_id == event_id) & (OutboxEvent.status == "IN_FLIGHT"))
                .values(
                    status="PENDING",
                    next_retry_at=next_retry,
                    last_error=error_msg[:500],
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def _mark_dead(
        self,
        event_id: uuid.UUID,
        aggregate_id: uuid.UUID,
        error_msg: str,
        req_id: Optional[str] = None,
    ) -> None:
        """Mark an outbox row as DEAD and record an audit row."""
        session_factory = get_sessionmaker()
        now = self.clock.now()

        async with session_factory() as session:
            stmt = (
                update(OutboxEvent)
                .where(OutboxEvent.event_id == event_id)
                .values(
                    status="DEAD",
                    last_error=error_msg[:500],
                )
            )
            await session.execute(stmt)

            # Find business_id for audit
            stmt_p = select(Payment.business_id).where(Payment.payment_id == aggregate_id)
            res_p = await session.execute(stmt_p)
            biz_id = res_p.scalar_one_or_none()

            if biz_id:
                audit = AuditLog(
                    audit_id=uuid.uuid4(),
                    business_id=biz_id,
                    actor_type="SYSTEM",
                    action="OUTBOX_EVENT_DEAD",
                    entity_type="outbox_event",
                    entity_id=event_id,
                    before={"status": "IN_FLIGHT"},
                    after={"status": "DEAD", "last_error": error_msg[:500]},
                    request_id=req_id,
                    occurred_at=now,
                )
                session.add(audit)

            await session.commit()

    async def get_queue_stats(self) -> dict[str, int]:
        """Return counts of pending, in-flight, and dead outbox rows."""
        session_factory = get_sessionmaker()
        async with session_factory() as session:
            stmt = select(OutboxEvent.status, func.count(OutboxEvent.event_id)).group_by(OutboxEvent.status)
            res = await session.execute(stmt)
            counts = {status: count for status, count in res.all()}
            return {
                "pending": counts.get("PENDING", 0),
                "in_flight": counts.get("IN_FLIGHT", 0),
                "delivered": counts.get("DELIVERED", 0),
                "dead": counts.get("DEAD", 0),
            }


_outbox_service: Optional[OutboxService] = None


def get_outbox_service() -> OutboxService:
    global _outbox_service
    if _outbox_service is None:
        _outbox_service = OutboxService()
    return _outbox_service
