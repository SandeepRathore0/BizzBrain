from decimal import Decimal
import hashlib
from typing import Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.errors import NotFoundException
from app.core.money import format_money
from app.models.invoice import Invoice
from app.repositories.allocation_repo import create_payment_allocation
from app.repositories.audit_repo import record_audit_log
from app.repositories.event_repo import get_processed_event, record_processed_event
from app.repositories.invoice_repo import (
    get_invoice_by_id,
    get_invoice_by_id_any_business,
    get_invoice_by_pay_token_hash,
)
from app.repositories.reconciliation_repo import create_reconciliation_exception
from app.schemas.internal import (
    PayableDetailsResponse,
    PaymentEventRequest,
    PaymentEventResponse,
    ResolvePayTokenResponse,
)
from app.services.state_machine import (
    InvoiceStatus,
    TransitionTrigger,
    transition_invoice_status,
)


class AllocationService:
    @staticmethod
    async def get_payable_details(
        session: AsyncSession,
        business_id: UUID,
        invoice_id: UUID,
    ) -> PayableDetailsResponse:
        """Contract C2: Called by Payment Service at order creation and before capture."""
        inv = await get_invoice_by_id(session, business_id, invoice_id)
        if not inv:
            raise NotFoundException("Invoice not found")

        outstanding = inv.total_amount - inv.amount_paid
        is_payable = (
            inv.status in (InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID)
            and outstanding > Decimal("0.0000")
        )

        return PayableDetailsResponse.from_invoice(inv, payable=is_payable)

    @staticmethod
    async def resolve_pay_token(
        session: AsyncSession,
        raw_token: str,
    ) -> ResolvePayTokenResponse:
        """Contract C4: Resolves a pay token from a public pay link.
        Uniform 404 for any invalid, expired, voided, paid, or rotated token.
        """
        token_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
        inv = await get_invoice_by_pay_token_hash(session, token_hash)

        # Uniform 404 to prevent timing attacks or status leaks
        if not inv:
            raise NotFoundException("Pay token is invalid, expired, or invoice is not payable")

        if inv.status in (InvoiceStatus.DRAFT, InvoiceStatus.VOID, InvoiceStatus.PAID):
            raise NotFoundException("Pay token is invalid, expired, or invoice is not payable")

        if (inv.total_amount - inv.amount_paid) <= Decimal("0.0000"):
            raise NotFoundException("Pay token is invalid, expired, or invoice is not payable")

        return ResolvePayTokenResponse(invoice_id=inv.invoice_id, business_id=inv.business_id)

    @staticmethod
    async def handle_payment_captured_event(
        session: AsyncSession,
        event: PaymentEventRequest,
        request_id: Optional[str] = None,
    ) -> PaymentEventResponse:
        """Contract C3: PaymentCaptured event handler with inbox deduplication,
        row lock, status validation, allocation creation, state machine transition,
        and reconciliation exceptions.
        Runs in a single atomic transaction.
        """
        # 1. Inbox deduplication check
        existing = await get_processed_event(session, event.event_id)
        if existing:
            return PaymentEventResponse(
                outcome="DUPLICATE_IGNORED",
                event_id=event.event_id,
            )

        # 2. Acquire SELECT ... FOR UPDATE row lock on invoice
        inv = await get_invoice_by_id_any_business(
            session=session,
            invoice_id=event.invoice_id,
            for_update=True,
        )

        # Helper to reject and record exception
        async def _reject_event(reason_code: str, details: dict) -> PaymentEventResponse:
            await create_reconciliation_exception(
                session=session,
                business_id=event.business_id,
                event_id=event.event_id,
                payment_id=event.payment_id,
                reason_code=reason_code,
                details=details,
                invoice_id=event.invoice_id if inv else None,
            )
            await record_processed_event(
                session=session,
                event_id=event.event_id,
                event_type=event.event_type,
                outcome="REJECTED",
            )
            return PaymentEventResponse(
                outcome="REJECTED",
                event_id=event.event_id,
                reason_code=reason_code,
                details=details,
            )

        # 3. Handle non-existent invoice
        if not inv:
            return await _reject_event(
                "INVOICE_NOT_FOUND",
                {
                    "message": "Invoice not found",
                    "invoice_id": str(event.invoice_id),
                    "business_id": str(event.business_id),
                },
            )

        # 4. Validate business ownership
        if inv.business_id != event.business_id:
            return await _reject_event(
                "BUSINESS_MISMATCH",
                {
                    "invoice_business_id": str(inv.business_id),
                    "event_business_id": str(event.business_id),
                },
            )

        # 5. Validate currency
        if inv.currency != event.currency:
            return await _reject_event(
                "CURRENCY_MISMATCH",
                {
                    "invoice_currency": inv.currency,
                    "event_currency": event.currency,
                },
            )

        # 6. Validate status
        if inv.status == InvoiceStatus.VOID:
            return await _reject_event(
                "INVOICE_VOID",
                {"status": inv.status},
            )

        if inv.status not in (InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID):
            return await _reject_event(
                "INVOICE_NOT_PAYABLE",
                {"status": inv.status},
            )

        # 7. Validate amount
        if event.amount <= Decimal("0.0000"):
            return await _reject_event(
                "INVALID_AMOUNT",
                {"amount": str(event.amount)},
            )

        outstanding = inv.total_amount - inv.amount_paid
        if event.amount > outstanding:
            return await _reject_event(
                "AMOUNT_EXCEEDS_BALANCE",
                {
                    "amount": str(event.amount),
                    "outstanding_balance": str(outstanding),
                },
            )

        # 8. All validations passed: Apply allocation
        await create_payment_allocation(
            session=session,
            business_id=inv.business_id,
            invoice_id=inv.invoice_id,
            payment_id=event.payment_id,
            event_id=event.event_id,
            allocated_amount=event.amount,
        )

        old_amount_paid = inv.amount_paid
        old_status = inv.status
        inv.amount_paid += event.amount
        remaining = inv.total_amount - inv.amount_paid

        # Determine target status
        target_status = (
            InvoiceStatus.PAID
            if remaining == Decimal("0.0000")
            else InvoiceStatus.PARTIALLY_PAID
        )

        # Transition status via state machine
        transition_invoice_status(
            inv, target_status, TransitionTrigger.PAYMENT_ALLOCATION, has_allocations=True
        )

        # Record processed event in inbox
        await record_processed_event(
            session=session,
            event_id=event.event_id,
            event_type=event.event_type,
            outcome="APPLIED",
        )

        # Record audit log
        await record_audit_log(
            session=session,
            business_id=inv.business_id,
            actor_type="SERVICE",
            actor_id="payment-service",
            action="PAYMENT_ALLOCATED",
            entity_type="INVOICE",
            entity_id=inv.invoice_id,
            before={
                "amount_paid": str(old_amount_paid),
                "status": old_status,
            },
            after={
                "amount_paid": str(inv.amount_paid),
                "status": inv.status,
                "payment_id": str(event.payment_id),
                "allocated_amount": str(event.amount),
            },
            request_id=request_id,
        )

        return PaymentEventResponse(
            outcome="APPLIED",
            event_id=event.event_id,
            invoice_id=inv.invoice_id,
            status=inv.status,
            outstanding_balance=format_money(remaining),
        )
