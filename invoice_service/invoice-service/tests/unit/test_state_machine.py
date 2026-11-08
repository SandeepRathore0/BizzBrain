from decimal import Decimal
from uuid import uuid4
import pytest
from app.core.errors import InvalidStateTransitionException, InvoiceHasPaymentsException
from app.models.invoice import Invoice
from app.services.state_machine import (
    InvoiceStatus,
    TransitionTrigger,
    transition_invoice_status,
)


def _make_dummy_invoice(status: str, amount_paid: Decimal = Decimal("0.00")) -> Invoice:
    return Invoice(
        invoice_id=uuid4(),
        business_id=uuid4(),
        customer_id=uuid4(),
        status=status,
        amount_paid=amount_paid,
        total_amount=Decimal("100.00"),
    )


def test_state_machine_allowed_transitions():
    # 1. DRAFT -> ISSUED
    inv = _make_dummy_invoice(InvoiceStatus.DRAFT)
    assert transition_invoice_status(inv, InvoiceStatus.ISSUED, TransitionTrigger.ISSUE) == InvoiceStatus.ISSUED

    # 2. DRAFT -> VOID
    inv = _make_dummy_invoice(InvoiceStatus.DRAFT)
    assert transition_invoice_status(inv, InvoiceStatus.VOID, TransitionTrigger.VOID) == InvoiceStatus.VOID

    # 3. ISSUED -> PARTIALLY_PAID
    inv = _make_dummy_invoice(InvoiceStatus.ISSUED, amount_paid=Decimal("50.00"))
    assert transition_invoice_status(inv, InvoiceStatus.PARTIALLY_PAID, TransitionTrigger.PAYMENT_ALLOCATION) == InvoiceStatus.PARTIALLY_PAID

    # 4. ISSUED -> PAID
    inv = _make_dummy_invoice(InvoiceStatus.ISSUED, amount_paid=Decimal("100.00"))
    assert transition_invoice_status(inv, InvoiceStatus.PAID, TransitionTrigger.PAYMENT_ALLOCATION) == InvoiceStatus.PAID

    # 5. PARTIALLY_PAID -> PAID
    inv = _make_dummy_invoice(InvoiceStatus.PARTIALLY_PAID, amount_paid=Decimal("100.00"))
    assert transition_invoice_status(inv, InvoiceStatus.PAID, TransitionTrigger.PAYMENT_ALLOCATION) == InvoiceStatus.PAID

    # 6. ISSUED -> VOID (only when no allocations and amount_paid == 0)
    inv = _make_dummy_invoice(InvoiceStatus.ISSUED, amount_paid=Decimal("0.00"))
    assert transition_invoice_status(inv, InvoiceStatus.VOID, TransitionTrigger.VOID, has_allocations=False) == InvoiceStatus.VOID


def test_state_machine_forbidden_transitions():
    # DRAFT cannot jump to PAID or PARTIALLY_PAID
    inv = _make_dummy_invoice(InvoiceStatus.DRAFT)
    with pytest.raises(InvalidStateTransitionException):
        transition_invoice_status(inv, InvoiceStatus.PAID, TransitionTrigger.PAYMENT_ALLOCATION)

    with pytest.raises(InvalidStateTransitionException):
        transition_invoice_status(inv, InvoiceStatus.PARTIALLY_PAID, TransitionTrigger.PAYMENT_ALLOCATION)

    # ISSUED cannot go back to DRAFT
    inv = _make_dummy_invoice(InvoiceStatus.ISSUED)
    with pytest.raises(InvalidStateTransitionException):
        transition_invoice_status(inv, InvoiceStatus.DRAFT, "MANUAL")

    # ISSUED cannot be voided if amount_paid > 0
    inv = _make_dummy_invoice(InvoiceStatus.ISSUED, amount_paid=Decimal("10.00"))
    with pytest.raises(InvoiceHasPaymentsException):
        transition_invoice_status(inv, InvoiceStatus.VOID, TransitionTrigger.VOID, has_allocations=False)

    # ISSUED cannot be voided if has_allocations is True
    inv = _make_dummy_invoice(InvoiceStatus.ISSUED, amount_paid=Decimal("0.00"))
    with pytest.raises(InvoiceHasPaymentsException):
        transition_invoice_status(inv, InvoiceStatus.VOID, TransitionTrigger.VOID, has_allocations=True)

    # PARTIALLY_PAID cannot be voided in MVP
    inv = _make_dummy_invoice(InvoiceStatus.PARTIALLY_PAID, amount_paid=Decimal("50.00"))
    with pytest.raises(InvoiceHasPaymentsException):
        transition_invoice_status(inv, InvoiceStatus.VOID, TransitionTrigger.VOID)

    # PARTIALLY_PAID cannot go back to DRAFT or ISSUED
    with pytest.raises(InvalidStateTransitionException):
        transition_invoice_status(inv, InvoiceStatus.DRAFT, "MANUAL")

    with pytest.raises(InvalidStateTransitionException):
        transition_invoice_status(inv, InvoiceStatus.ISSUED, "MANUAL")

    # Terminal states: PAID cannot transition anywhere
    paid_inv = _make_dummy_invoice(InvoiceStatus.PAID, amount_paid=Decimal("100.00"))
    for target in [InvoiceStatus.DRAFT, InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.VOID]:
        with pytest.raises(InvalidStateTransitionException, match="terminal"):
            transition_invoice_status(paid_inv, target, "ANY")

    # Terminal states: VOID cannot transition anywhere
    void_inv = _make_dummy_invoice(InvoiceStatus.VOID)
    for target in [InvoiceStatus.DRAFT, InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.PAID]:
        with pytest.raises(InvalidStateTransitionException, match="terminal"):
            transition_invoice_status(void_inv, target, "ANY")
