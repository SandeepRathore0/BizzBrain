import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional, Tuple
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.invoice_client import InvoiceClient, get_invoice_client
from app.clients.paypal_client import PayPalClientInterface, get_paypal_client
from app.core.database import get_sessionmaker
from app.core.errors import (
    APIException,
    BadGatewayException,
    ConflictException,
    ServiceUnavailableException,
    UnprocessableEntityException,
)
from app.core.logging import get_logger
from app.core.money import format_amount, parse_and_validate_amount
from app.repositories.payment_repository import PaymentRepository
from app.schemas.payment import OrderCreationResponse
from app.services.state_machine import transition_payment

logger = get_logger("payment_service")


class PaymentService:
    def __init__(
        self,
        invoice_client: Optional[InvoiceClient] = None,
        paypal_client: Optional[PayPalClientInterface] = None,
    ):
        self.invoice_client = invoice_client or get_invoice_client()
        self.paypal_client = paypal_client or get_paypal_client()

    async def create_payment_order(
        self,
        business_id: uuid.UUID,
        invoice_id: uuid.UUID,
        requested_amount_str: Optional[str],
        idempotency_key: uuid.UUID,
        initiated_by_type: str,
        initiated_by_id: Optional[uuid.UUID],
        request_id: Optional[str] = None,
    ) -> OrderCreationResponse:
        """Create payment attempt and PayPal order following section 7 workflow."""
        # 1. Call Invoice Service C2 GET /internal/v1/invoices/{id}/payable-details
        invoice = await self.invoice_client.get_payable_details(
            business_id=business_id,
            invoice_id=invoice_id,
            request_id=request_id,
        )

        if not invoice.payable:
            raise ConflictException(
                code="INVOICE_NOT_PAYABLE",
                message="Invoice is not in a payable state.",
                details={"invoice_id": str(invoice_id)},
            )

        currency = invoice.currency

        # 2. Amount Rules (Step 3)
        outstanding = invoice.outstanding_balance
        if requested_amount_str is None:
            final_amount = outstanding
        else:
            req_dec = parse_and_validate_amount(requested_amount_str, currency)
            min_installment = invoice.min_installment_amount

            # If amount is requested, min_installment_amount must be set on the invoice
            if min_installment is None:
                # If installment is not supported, amount must match full outstanding balance
                if req_dec != outstanding:
                    raise UnprocessableEntityException(
                        code="AMOUNT_NOT_ALLOWED",
                        message="Installment payments are not enabled for this invoice; full balance required.",
                        details={"outstanding_balance": str(outstanding)},
                    )
                final_amount = req_dec
            else:
                # min_installment is set. Remaining balance smaller than minimum may be paid in full.
                if outstanding < min_installment:
                    if req_dec != outstanding:
                        raise UnprocessableEntityException(
                            code="AMOUNT_NOT_ALLOWED",
                            message="Remaining balance is below minimum installment; must be paid in full.",
                            details={"outstanding_balance": str(outstanding)},
                        )
                else:
                    if not (min_installment <= req_dec <= outstanding):
                        raise UnprocessableEntityException(
                            code="AMOUNT_NOT_ALLOWED",
                            message=f"Requested amount must be between {min_installment} and {outstanding}.",
                            details={
                                "min_installment_amount": str(min_installment),
                                "outstanding_balance": str(outstanding),
                            },
                        )
                final_amount = req_dec

        # 3. DB Transaction 1: Expire stale CREATED payments & Insert payment row (Step 4)
        session_factory = get_sessionmaker()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=30)
        paypal_req_id = uuid.uuid4()
        capture_req_id = uuid.uuid4()

        payment_id: uuid.UUID
        async with session_factory() as session:
            repo = PaymentRepository(session)
            # Expire any stale CREATED payment for this invoice whose expires_at has passed
            await repo.expire_stale_created_payments(
                business_id=business_id,
                invoice_id=invoice_id,
                now=now,
                request_id=request_id,
            )

            # Insert payment row
            try:
                payment = await repo.create_payment(
                    business_id=business_id,
                    invoice_id=invoice_id,
                    invoice_number=invoice.invoice_number,
                    amount=final_amount,
                    currency=currency,
                    idempotency_key=idempotency_key,
                    initiated_by_type=initiated_by_type,
                    initiated_by_id=initiated_by_id,
                    expires_at=expires_at,
                    request_id=request_id,
                )
                payment_id = payment.payment_id
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                logger.warn("checkout_already_active_conflict", error=str(exc))
                raise ConflictException(
                    code="CHECKOUT_ALREADY_ACTIVE",
                    message="An active payment checkout is already in progress for this invoice.",
                    details={"invoice_id": str(invoice_id)},
                )

        # 4. Call PayPal create order (Step 5)
        try:
            order_data = await self.paypal_client.create_order(
                amount=final_amount,
                currency=currency,
                paypal_request_id=paypal_req_id,
                reference_id=str(payment_id),
            )
        except (BadGatewayException, ServiceUnavailableException, APIException) as exc:
            # If PayPal fails: mark payment FAILED in a NEW transaction so invoice is not blocked
            logger.error("paypal_create_failed_marking_failed", error=str(exc))
            async with session_factory() as fail_session:
                repo_fail = PaymentRepository(fail_session)
                p_fail = await repo_fail.get_payment_by_id(business_id, payment_id, for_update=True)
                if p_fail:
                    await transition_payment(
                        db=fail_session,
                        payment=p_fail,
                        new_status="FAILED",
                        actor_type="SYSTEM",
                        status_reason=f"PAYPAL_ORDER_CREATION_FAILED: {exc.code}",
                        request_id=request_id,
                    )
                    await fail_session.commit()
            raise

        provider_order_id = order_data["id"]

        # Extract approval URL
        links = order_data.get("links", [])
        approve_link = next((l["href"] for l in links if l.get("rel") == "approve"), None)
        if not approve_link:
            approve_link = f"https://www.sandbox.paypal.com/checkoutnow?token={provider_order_id}"

        # 5. DB Transaction 2: Store Provider Reference
        async with session_factory() as session:
            repo = PaymentRepository(session)
            await repo.save_provider_reference(
                payment_id=payment_id,
                provider_order_id=provider_order_id,
                paypal_request_id=paypal_req_id,
                capture_request_id=capture_req_id,
                provider_status=order_data.get("status", "CREATED"),
                raw_last_response=order_data,
            )
            await session.commit()

        return OrderCreationResponse(
            payment_id=payment_id,
            approve_url=approve_link,
            status="CREATED",
            amount=format_amount(final_amount, currency),
            currency=currency,
        )


_payment_service = PaymentService()


def get_payment_service() -> PaymentService:
    return _payment_service
