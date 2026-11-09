from dataclasses import dataclass
from decimal import Decimal
from typing import List, Sequence
from app.core.errors import ValidationException
from app.core.money import round_money, validate_not_float


@dataclass(frozen=True)
class ItemCalculationInput:
    position: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal


@dataclass(frozen=True)
class CalculatedLineItem:
    position: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal
    line_subtotal: Decimal
    line_tax: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class CalculatedInvoiceTotals:
    items: List[CalculatedLineItem]
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal


def calculate_invoice_totals(
    items: Sequence[ItemCalculationInput], minor_units: int = 2
) -> CalculatedInvoiceTotals:
    """Calculates invoice line items and totals per the BizBrain specification:
    - Applied per line to the minor unit (ROUND_HALF_UP).
    - line_subtotal = round(quantity * unit_price)
    - line_tax = round(line_subtotal * tax_rate)
    - line_total = line_subtotal + line_tax
    - Invoice subtotal, tax_amount, total_amount are the sums of the rounded line values.
    - Never recomputes totals from the invoice level.
    """
    calculated_items: List[CalculatedLineItem] = []
    running_subtotal = Decimal("0.00")
    running_tax = Decimal("0.00")
    running_total = Decimal("0.00")

    for item in items:
        qty = validate_not_float(item.quantity)
        price = validate_not_float(item.unit_price)
        rate = validate_not_float(item.tax_rate)

        if qty <= 0:
            raise ValidationException(f"Line item {item.position}: quantity must be positive")
        if price < 0:
            raise ValidationException(f"Line item {item.position}: unit_price cannot be negative")
        if rate < 0:
            raise ValidationException(f"Line item {item.position}: tax_rate cannot be negative")

        # Line calculations
        raw_subtotal = qty * price
        line_subtotal = round_money(raw_subtotal, minor_units)

        raw_tax = line_subtotal * rate
        line_tax = round_money(raw_tax, minor_units)

        line_total = line_subtotal + line_tax

        calculated_items.append(
            CalculatedLineItem(
                position=item.position,
                description=item.description,
                quantity=qty,
                unit_price=price,
                tax_rate=rate,
                line_subtotal=line_subtotal,
                line_tax=line_tax,
                line_total=line_total,
            )
        )

        running_subtotal += line_subtotal
        running_tax += line_tax
        running_total += line_total

    return CalculatedInvoiceTotals(
        items=calculated_items,
        subtotal=running_subtotal,
        tax_amount=running_tax,
        total_amount=running_total,
    )
