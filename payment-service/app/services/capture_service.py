import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.invoice_client import InvoiceClient, get_invoice_client
from app.clients.paypal_client import PayPalClientInterface, get_paypal_client
from app.core.database import get_sessionmaker
from app.core.errors import (
    BadGatewayException,
    ConflictException,
    NotFoundException,
    ServiceUnavailableException,
    UnprocessableEntityException,
)
from app.core.logging import get_logger
from app.core.money import format_amount
from app.models.audit import AuditLog
from app.models.outbox import OutboxEvent
from app.models.payment import Payment
from app.models.provider_reference import PaymentProviderReference
from app.repositories.payment_repository import PaymentRepository
from app.schemas.payment import CaptureResponse
from app.services.completion_service import complete_capture
from app.services.state_machine import transition_payment

logger = get_logger("capture_service")


class CaptureService:
    def __init__(
        self,
        invoice_client: Optional[InvoiceClient] = None,
        paypal_client: Optional[PayPalClientInterface] = None,
    ):
        self.invoice_client = invoice_client or get_invoice_client()
        self.paypal_client = paypal_client or get_paypal_client()

    async def capture_payment(
        self,
        business_id: uuid.UUID,
        payment_id: uuid.UUID,
        actor_type: str,
        actor_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> CaptureResponse:
        session_factory = get_sessionmaker()

        # Step 1: Load payment with lock (business-scoped)
        provider_order_id: str
        capture_req_id: uuid.UUID
        payment_amount: Decimal
        payment_currency: str
        invoice_id: uuid.UUID

        async with session_factory() as session:
            repo = PaymentRepository(session)
            payment = await repo.get_payment_by_id(business_id, payment_id, for_update=True)
            if not payment:
                raise NotFoundException(
                    code="PAYMENT_NOT_FOUND",
                    message=f"Payment {payment_id} not found.",
                )

            # If already COMPLETED, return stored result (Step 1)
            if payment.status == "COMPLETED":
                ref = payment.provider_reference
                return CaptureResponse(
                    payment_id=payment.payment_id,
                    status=payment.status,
                    amount=format_amount(payment.amount, payment.currency),
                    currency=payment.currency,
                    provider_capture_id=ref.provider_capture_id if ref else None,
                )

            # If another capture is already pending or requires review, return 409
            if payment.status not in ("CREATED", "PAYER_APPROVED"):
                raise ConflictException(
                    code="INVALID_STATE_TRANSITION",
                    message=f"Payment status is {payment.status}; cannot initiate capture.",
                    details={"status": payment.status},
                )

            ref = payment.provider_reference
            if not ref or not ref.provider_order_id:
                raise ConflictException(
                    code="PROVIDER_ORDER_MISSING",
                    message="Provider order reference is missing for this payment.",
                )

            provider_order_id = ref.provider_order_id
            capture_req_id = ref.capture_request_id
            payment_amount = payment.amount
            payment_currency = payment.currency
            invoice_id = payment.invoice_id

            # Step 2: Fetch order from PayPal; must be APPROVED
            try:
                order_data = await self.paypal_client.get_order(provider_order_id)
            except Exception as exc:
                logger.error("paypal_get_order_failed", order_id=provider_order_id, error=str(exc))
                raise BadGatewayException(
                    code="PAYPAL_GET_ORDER_FAILED",
                    message=f"Failed to fetch order status from PayPal: {exc}",
                )

            order_status = order_data.get("status")
            if order_status != "APPROVED":
                raise ConflictException(
                    code="PAYER_NOT_APPROVED",
                    message=f"Payer has not approved this order on PayPal (status: {order_status}).",
                    details={"provider_status": order_status},
                )

            # If CREATED, transition to PAYER_APPROVED
            if payment.status == "CREATED":
                await transition_payment(
                    db=session,
                    payment=payment,
                    new_status="PAYER_APPROVED",
                    actor_type=actor_type,
                    actor_id=actor_id,
                    status_reason="PAYER_APPROVED_VERIFIED_ON_PAYPAL",
                    request_id=request_id,
                )
                await session.commit()

        # Step 3: Re-call Invoice Service C2 and confirm payable, currency, amount <= balance
        try:
            invoice = await self.invoice_client.get_payable_details(
                business_id=business_id,
                invoice_id=invoice_id,
                request_id=request_id,
            )
        except ServiceUnavailableException:
            # If C2 is unreachable, return 503 and do not capture; payment stays approvable
            raise
        except Exception as exc:
            logger.error("invoice_c2_error_before_capture", error=str(exc))
            raise ServiceUnavailableException(
                code="INVOICE_SERVICE_UNAVAILABLE",
                message=f"Could not verify invoice status prior to capture: {exc}",
            )

        # Validate conditions
        is_payable = invoice.payable
        currency_match = (invoice.currency.upper() == payment_currency.upper())
        balance_ok = (payment_amount <= invoice.outstanding_balance)

        if not (is_payable and currency_match and balance_ok):
            reason = "INVOICE_NO_LONGER_PAYABLE"
            if not is_payable:
                reason = "INVOICE_VOIDED_OR_SETTLED"
            elif not currency_match:
                reason = "CURRENCY_MISMATCH"
            elif not balance_ok:
                reason = "AMOUNT_EXCEEDS_OUTSTANDING_BALANCE"

            async with session_factory() as session:
                repo = PaymentRepository(session)
                p = await repo.get_payment_by_id(business_id, payment_id, for_update=True)
                if p and p.status in ("CREATED", "PAYER_APPROVED"):
                    await transition_payment(
                        db=session,
                        payment=p,
                        new_status="CANCELLED",
                        actor_type="SYSTEM",
                        status_reason=reason,
                        request_id=request_id,
                    )
                    await session.commit()

            raise ConflictException(
                code="INVOICE_NO_LONGER_PAYABLE",
                message=f"Invoice cannot be captured: {reason}",
                details={"reason": reason},
            )

        # Step 4: Move to CAPTURE_PENDING and COMMIT before calling PayPal
        async with session_factory() as session:
            repo = PaymentRepository(session)
            p = await repo.get_payment_by_id(business_id, payment_id, for_update=True)
            if not p or p.status != "PAYER_APPROVED":
                raise ConflictException(
                    code="INVALID_STATE_TRANSITION",
                    message="Payment status changed concurrently before capture.",
                )
            await transition_payment(
                db=session,
                payment=p,
                new_status="CAPTURE_PENDING",
                actor_type=actor_type,
                actor_id=actor_id,
                request_id=request_id,
            )
            await session.commit()

        # Step 5: Call PayPal capture with stored capture_request_id
        capture_response: Optional[dict[str, Any]] = None
        capture_error: Optional[Exception] = None

        try:
            capture_response = await self.paypal_client.capture_order(
                order_id=provider_order_id,
                capture_request_id=capture_req_id,
            )
        except Exception as exc:
            capture_error = exc
            logger.warn("paypal_capture_exception", error=str(exc))

        # Check PayPal response
        # Extract capture details
        if capture_response and not capture_error:
            try:
                # Find captures array
                pus = capture_response.get("purchase_units", [{}])
                captures = pus[0].get("payments", {}).get("captures", [])
                cap_obj = captures[0] if captures else {}
                cap_id = cap_obj.get("id")
                cap_status = cap_obj.get("status")
                cap_amount_obj = cap_obj.get("amount", {})
                cap_amount_val = cap_amount_obj.get("value")
                cap_curr_val = cap_amount_obj.get("currency_code")
            except Exception as parse_exc:
                logger.error("paypal_capture_response_parse_failed", error=str(parse_exc))
                cap_id = None
                cap_status = None
                cap_amount_val = None
                cap_curr_val = None
        else:
            cap_id = None
            cap_status = None
            cap_amount_val = None
            cap_curr_val = None

        expected_amount_str = format_amount(payment_amount, payment_currency)

        # SCENARIO A: Confirmed COMPLETED capture response from PayPal
        if cap_status == "COMPLETED" and cap_id:
            res = await complete_capture(
                business_id=business_id,
                payment_id=payment_id,
                captured_amount=cap_amount_val or "",
                captured_currency=cap_curr_val or "",
                provider_capture_id=cap_id,
                raw_response=capture_response,
                actor_type=actor_type,
                actor_id=actor_id,
                request_id=request_id,
            )
            if res.is_mismatch:
                logger.error("payment_placed_in_requires_review", payment_id=str(payment_id), reason="AMOUNT_MISMATCH")
                raise BadGatewayException(
                    code="PAYMENT_REQUIRES_REVIEW",
                    message="Payment capture outcome is uncertain and has been placed in review for automated reconciliation.",
                    details={
                        "payment_id": str(payment_id),
                        "reason": f"PAYPAL_AMOUNT_MISMATCH: expected {expected_amount_str} {payment_currency}, got {cap_amount_val} {cap_curr_val}",
                    },
                )

            return CaptureResponse(
                payment_id=payment_id,
                status="COMPLETED",
                amount=res.amount,
                currency=res.currency,
                provider_capture_id=res.provider_capture_id,
            )

        # SCENARIO B: Provider Declines (DECLINED or FAILED)
        if cap_status in ("DECLINED", "FAILED"):
            async with session_factory() as session:
                repo = PaymentRepository(session)
                p = await repo.get_payment_by_id(business_id, payment_id, for_update=True)
                if p:
                    await transition_payment(
                        db=session,
                        payment=p,
                        new_status="FAILED",
                        actor_type="SYSTEM",
                        status_reason=f"PAYPAL_CAPTURE_{cap_status}",
                        request_id=request_id,
                    )
                    if p.provider_reference:
                        p.provider_reference.provider_status = cap_status
                        p.provider_reference.raw_last_response = capture_response
                    await session.commit()

            raise ConflictException(
                code="PAYPAL_CAPTURE_DECLINED",
                message=f"PayPal declined payment capture with status {cap_status}.",
                details={"status": cap_status},
            )

        # SCENARIO C: Timeout, 5xx, malformed response, or amount/currency mismatch -> REQUIRES_REVIEW
        reason = "PAYPAL_CAPTURE_TIMEOUT_OR_ERROR"
        if capture_response and (cap_curr_val != payment_currency or cap_amount_val != expected_amount_str):
            reason = f"PAYPAL_AMOUNT_MISMATCH: expected {expected_amount_str} {payment_currency}, got {cap_amount_val} {cap_curr_val}"

        async with session_factory() as session:
            repo = PaymentRepository(session)
            p = await repo.get_payment_by_id(business_id, payment_id, for_update=True)
            if p and p.status in ("CAPTURE_PENDING", "PAYER_APPROVED"):
                await transition_payment(
                    db=session,
                    payment=p,
                    new_status="REQUIRES_REVIEW",
                    actor_type="SYSTEM",
                    status_reason=reason,
                    request_id=request_id,
                )
                if p.provider_reference and capture_response:
                    p.provider_reference.raw_last_response = capture_response
                await session.commit()

        logger.error("payment_placed_in_requires_review", payment_id=str(payment_id), reason=reason)
        raise BadGatewayException(
            code="PAYMENT_REQUIRES_REVIEW",
            message="Payment capture outcome is uncertain and has been placed in review for automated reconciliation.",
            details={"payment_id": str(payment_id), "reason": reason},
        )


_capture_service = CaptureService()


def get_capture_service() -> CaptureService:
    return _capture_service
