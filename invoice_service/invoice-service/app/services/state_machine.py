from typing import Optional
from app.core.errors import (
    ConflictException,
    InvalidStateTransitionException,
    InvoiceHasPaymentsException,
)
from app.models.invoice import Invoice


class InvoiceStatus:
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    VOID = "VOID"


class TransitionTrigger:
    ISSUE = "ISSUE"
    VOID = "VOID"
    PAYMENT_ALLOCATION = "PAYMENT_ALLOCATION"


def transition_invoice_status(
    invoice: Invoice,
    target_status: str,
    trigger: str,
    has_allocations: bool = False,
) -> str:
    """The SINGLE state-machine function for all invoice status transitions.
    Enforces Section 7 of the BizBrain specification.
    Returns the new status.
    """
    current_status = invoice.status
    target = target_status.upper()

    # Terminal states cannot transition
    if current_status in (InvoiceStatus.PAID, InvoiceStatus.VOID):
        raise InvalidStateTransitionException(
            f"Cannot transition from terminal status '{current_status}' to '{target}'"
        )

    # Transition matrix
    if current_status == InvoiceStatus.DRAFT:
        if target == InvoiceStatus.ISSUED and trigger == TransitionTrigger.ISSUE:
            invoice.status = InvoiceStatus.ISSUED
            return InvoiceStatus.ISSUED
        elif target == InvoiceStatus.VOID and trigger == TransitionTrigger.VOID:
            invoice.status = InvoiceStatus.VOID
            return InvoiceStatus.VOID
        else:
            raise InvalidStateTransitionException(
                f"Invalid transition from '{current_status}' to '{target}' via trigger '{trigger}'"
            )

    elif current_status == InvoiceStatus.ISSUED:
        if target == InvoiceStatus.PARTIALLY_PAID and trigger == TransitionTrigger.PAYMENT_ALLOCATION:
            invoice.status = InvoiceStatus.PARTIALLY_PAID
            return InvoiceStatus.PARTIALLY_PAID
        elif target == InvoiceStatus.PAID and trigger == TransitionTrigger.PAYMENT_ALLOCATION:
            invoice.status = InvoiceStatus.PAID
            return InvoiceStatus.PAID
        elif target == InvoiceStatus.VOID and trigger == TransitionTrigger.VOID:
            # Check: only allowed if no allocations exist and amount_paid == 0
            if has_allocations or invoice.amount_paid > 0:
                raise InvoiceHasPaymentsException(
                    "Cannot void issued invoice that has payments or allocations"
                )
            invoice.status = InvoiceStatus.VOID
            return InvoiceStatus.VOID
        else:
            raise InvalidStateTransitionException(
                f"Invalid transition from '{current_status}' to '{target}' via trigger '{trigger}'"
            )

    elif current_status == InvoiceStatus.PARTIALLY_PAID:
        if target == InvoiceStatus.PARTIALLY_PAID and trigger == TransitionTrigger.PAYMENT_ALLOCATION:
            # Remains in PARTIALLY_PAID status after an additional partial payment allocation
            return InvoiceStatus.PARTIALLY_PAID
        elif target == InvoiceStatus.PAID and trigger == TransitionTrigger.PAYMENT_ALLOCATION:
            invoice.status = InvoiceStatus.PAID
            return InvoiceStatus.PAID
        elif target == InvoiceStatus.VOID:
            # Section 7 & IS-C09: A PARTIALLY_PAID invoice cannot be voided
            raise InvoiceHasPaymentsException(
                "A PARTIALLY_PAID invoice cannot be voided in the MVP. Credit notes and refunds are Stage 2."
            )
        else:
            raise InvalidStateTransitionException(
                f"Invalid transition from '{current_status}' to '{target}' via trigger '{trigger}'"
            )

    raise InvalidStateTransitionException(
        f"Unknown or invalid transition from '{current_status}' to '{target}'"
    )
