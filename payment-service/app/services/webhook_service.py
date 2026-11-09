import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.paypal_client import get_paypal_client
from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.core.errors import BadRequestException
from app.core.logging import get_logger
from app.core.money import format_amount
from app.models.audit import AuditLog
from app.models.outbox import OutboxEvent
from app.models.payment import Payment
from app.models.provider_reference import PaymentProviderReference
from app.models.webhook_event import PaymentWebhookEvent
from app.repositories.payment_repository import PaymentRepository
from app.services.completion_service import complete_capture
from app.services.state_machine import transition_payment

logger = get_logger("webhook_service")


class WebhookService:
    def __init__(self, paypal_client=None):
        self.paypal_client = paypal_client or get_paypal_client()

    async def handle_paypal_webhook(
        self,
        raw_body: str,
        headers: dict[str, str],
        request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        settings = get_settings()
        req_id = request_id or str(uuid.uuid4())
        session_factory = get_sessionmaker()

        # Step 1: Signature Verification via PayPal
        is_verified = await self.paypal_client.verify_webhook_signature(
            headers=headers,
            raw_body=raw_body,
            webhook_id=settings.PAYPAL_WEBHOOK_ID,
        )

        if not is_verified:
            # Failed verification: write to audit_log (no payload trust) and do NOT store in payment_webhook_events
            async with session_factory() as session:
                audit = AuditLog(
                    audit_id=uuid.uuid4(),
                    business_id=None,
                    actor_type="EXTERNAL_WEBHOOK",
                    actor_id=None,
                    action="WEBHOOK_SIGNATURE_VERIFICATION_FAILED",
                    entity_type="webhook",
                    entity_id=None,
                    before=None,
                    after={
                        "headers": {
                            k: v for k, v in headers.items() if "auth" not in k.lower() and "sig" not in k.lower()
                        }
                    },
                    request_id=req_id,
                    occurred_at=datetime.now(timezone.utc),
                )
                session.add(audit)
                await session.commit()

            logger.warn("paypal_webhook_signature_verification_failed", request_id=req_id)
            raise BadRequestException(
                code="INVALID_WEBHOOK_SIGNATURE",
                message="PayPal webhook signature verification failed.",
            )

        # Step 2: Parse verified event
        try:
            event_payload = json.loads(raw_body)
        except Exception:
            raise BadRequestException(
                code="MALFORMED_WEBHOOK_BODY",
                message="Failed to parse webhook JSON payload.",
            )

        provider_event_id = event_payload.get("id")
        event_type = event_payload.get("event_type", "UNKNOWN")
        resource = event_payload.get("resource", {})

        if not provider_event_id:
            raise BadRequestException(
                code="MISSING_EVENT_ID",
                message="Webhook payload is missing 'id'.",
            )

        # Step 3: Insert verified event with status RECEIVED or handle duplicate
        async with session_factory() as session:
            stmt_dup = select(PaymentWebhookEvent).where(
                PaymentWebhookEvent.provider_event_id == provider_event_id
            )
            res_dup = await session.execute(stmt_dup)
            existing_event = res_dup.scalar_one_or_none()

            if existing_event:
                logger.info("webhook_event_already_processed", provider_event_id=provider_event_id)
                return {"status": "ALREADY_PROCESSED", "event_id": provider_event_id}

            webhook_event = PaymentWebhookEvent(
                id=uuid.uuid4(),
                provider_event_id=provider_event_id,
                event_type=event_type,
                payment_id=None,
                processing_status="RECEIVED",
                payload=event_payload,
                received_at=datetime.now(timezone.utc),
            )
            session.add(webhook_event)
            await session.commit()

        # Step 4: Correlate with Payment
        # Extract potential identifiers
        resource_id = resource.get("id")  # order_id or capture_id
        # Check supplementary data for related order ID
        supp_data = resource.get("supplementary_data", {})
        related_order_id = supp_data.get("related_ids", {}).get("order_id")
        custom_id = resource.get("custom_id")

        target_payment_id: Optional[uuid.UUID] = None

        async with session_factory() as session:
            # 1. Search by provider_capture_id == resource_id
            stmt = select(PaymentProviderReference).where(
                PaymentProviderReference.provider_capture_id == resource_id
            )
            res = await session.execute(stmt)
            ref = res.scalar_one_or_none()

            # 2. Search by provider_order_id == resource_id or related_order_id
            if not ref:
                candidate_orders = [o for o in [resource_id, related_order_id] if o]
                if candidate_orders:
                    stmt2 = select(PaymentProviderReference).where(
                        PaymentProviderReference.provider_order_id.in_(candidate_orders)
                    )
                    res2 = await session.execute(stmt2)
                    ref = res2.scalar_one_or_none()

            # 3. Search by custom_id
            if not ref and custom_id:
                try:
                    cid_uuid = uuid.UUID(custom_id)
                    stmt3 = select(Payment).where(Payment.payment_id == cid_uuid)
                    res3 = await session.execute(stmt3)
                    p_match = res3.scalar_one_or_none()
                    if p_match:
                        target_payment_id = p_match.payment_id
                except ValueError:
                    pass

            if ref:
                target_payment_id = ref.payment_id

        # If payment is unknown, mark event IGNORED
        if not target_payment_id:
            async with session_factory() as session:
                stmt_wh = select(PaymentWebhookEvent).where(
                    PaymentWebhookEvent.provider_event_id == provider_event_id
                )
                res_wh = await session.execute(stmt_wh)
                wh = res_wh.scalar_one_or_none()
                if wh:
                    wh.processing_status = "IGNORED"
                    wh.processed_at = datetime.now(timezone.utc)
                    await session.commit()
            logger.info("webhook_ignored_unknown_payment", event_type=event_type, resource_id=resource_id)
            return {"status": "IGNORED_UNKNOWN_PAYMENT", "event_id": provider_event_id}

        # Step 5: Process Event Idempotently
        async with session_factory() as session:
            # Lock payment row
            stmt_p = select(Payment).where(Payment.payment_id == target_payment_id).with_for_update()
            res_p = await session.execute(stmt_p)
            payment = res_p.scalar_one_or_none()
            if not payment:
                return {"status": "PAYMENT_NOT_FOUND"}

            stmt_wh = select(PaymentWebhookEvent).where(
                PaymentWebhookEvent.provider_event_id == provider_event_id
            )
            res_wh = await session.execute(stmt_wh)
            wh = res_wh.scalar_one_or_none()
            if wh:
                wh.payment_id = payment.payment_id

            # 5A. CHECKOUT.ORDER.APPROVED
            if event_type == "CHECKOUT.ORDER.APPROVED":
                if payment.status == "CREATED":
                    await transition_payment(
                        db=session,
                        payment=payment,
                        new_status="PAYER_APPROVED",
                        actor_type="WEBHOOK",
                        status_reason="CHECKOUT.ORDER.APPROVED_WEBHOOK",
                        request_id=req_id,
                    )
                if wh:
                    wh.processing_status = "PROCESSED"
                    wh.processed_at = datetime.now(timezone.utc)
                await session.commit()
                return {"status": "PROCESSED", "payment_status": payment.status}

            # 5B. PAYMENT.CAPTURE.COMPLETED
            elif event_type == "PAYMENT.CAPTURE.COMPLETED":
                amount_obj = resource.get("amount", {})
                rec_val = amount_obj.get("value") or ""
                rec_curr = amount_obj.get("currency_code") or ""
                capture_id = resource_id or ""

                res = await complete_capture(
                    session=session,
                    business_id=payment.business_id,
                    payment_id=payment.payment_id,
                    captured_amount=rec_val,
                    captured_currency=rec_curr,
                    provider_capture_id=capture_id,
                    raw_response=resource,
                    actor_type="WEBHOOK",
                    request_id=req_id,
                )

                if res.is_mismatch:
                    if wh:
                        wh.processing_status = "FAILED"
                        wh.processed_at = datetime.now(timezone.utc)
                    await session.commit()
                    return {"status": "AMOUNT_MISMATCH", "payment_status": res.status}

                if wh:
                    wh.processing_status = "PROCESSED"
                    wh.processed_at = datetime.now(timezone.utc)
                await session.commit()
                return {"status": "PROCESSED", "payment_status": res.status}

            # 5C. PAYMENT.CAPTURE.DENIED or DECLINED
            elif event_type in ("PAYMENT.CAPTURE.DENIED", "PAYMENT.CAPTURE.DECLINED"):
                if payment.status in ("CAPTURE_PENDING", "REQUIRES_REVIEW"):
                    await transition_payment(
                        db=session,
                        payment=payment,
                        new_status="FAILED",
                        actor_type="WEBHOOK",
                        status_reason=f"WEBHOOK_{event_type}",
                        request_id=req_id,
                    )
                if wh:
                    wh.processing_status = "PROCESSED"
                    wh.processed_at = datetime.now(timezone.utc)
                await session.commit()
                return {"status": "PROCESSED", "payment_status": payment.status}

            # 5D. PAYMENT.CAPTURE.REFUNDED or REVERSED
            elif event_type in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"):
                if payment.status == "COMPLETED":
                    await transition_payment(
                        db=session,
                        payment=payment,
                        new_status="REFUNDED",
                        actor_type="WEBHOOK",
                        status_reason=f"WEBHOOK_{event_type}",
                        request_id=req_id,
                    )
                    logger.warn(
                        "payment_refunded_alert",
                        payment_id=str(payment.payment_id),
                        event_type=event_type,
                        request_id=req_id,
                    )
                if wh:
                    wh.processing_status = "PROCESSED"
                    wh.processed_at = datetime.now(timezone.utc)
                await session.commit()
                return {"status": "PROCESSED", "payment_status": payment.status}

            # 5E. Unknown Event Type
            else:
                if wh:
                    wh.processing_status = "IGNORED"
                    wh.processed_at = datetime.now(timezone.utc)
                await session.commit()
                return {"status": "IGNORED", "event_type": event_type}


_webhook_service = WebhookService()


def get_webhook_service() -> WebhookService:
    return _webhook_service
