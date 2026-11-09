from decimal import Decimal
import pytest
from app.core.errors import ValidationException
from app.services.calculation import (
    ItemCalculationInput,
    calculate_invoice_totals,
)

# Table of 21 hand-worked test cases: (items, expected_subtotal, expected_tax, expected_total)
CALCULATION_TEST_CASES = [
    # 1. Standard single item, 10% tax
    (
        [ItemCalculationInput(1, "Item", Decimal("2"), Decimal("100.00"), Decimal("0.1000"))],
        Decimal("200.00"), Decimal("20.00"), Decimal("220.00"),
    ),
    # 2. Fractional quantity 1.5 @ 20.00, 5% tax
    (
        [ItemCalculationInput(1, "Item", Decimal("1.5"), Decimal("20.00"), Decimal("0.0500"))],
        Decimal("30.00"), Decimal("1.50"), Decimal("31.50"),
    ),
    # 3. Fractional quantity 3.3333 @ 10.00, 18% tax (subtotal 33.33, tax 6.00)
    (
        [ItemCalculationInput(1, "Item", Decimal("3.3333"), Decimal("10.00"), Decimal("0.1800"))],
        Decimal("33.33"), Decimal("6.00"), Decimal("39.33"),
    ),
    # 4. Rounding exact half up at .005: qty=1, price=0.005 -> 0.01
    (
        [ItemCalculationInput(1, "Item", Decimal("1"), Decimal("0.005"), Decimal("0.0000"))],
        Decimal("0.01"), Decimal("0.00"), Decimal("0.01"),
    ),
    # 5. Rounding below half at .0049: qty=1, price=0.0049 -> 0.00
    (
        [ItemCalculationInput(1, "Item", Decimal("1"), Decimal("0.0049"), Decimal("0.0000"))],
        Decimal("0.00"), Decimal("0.00"), Decimal("0.00"),
    ),
    # 6. Tax rounding at half up: 10.00 * 0.0005 = 0.005 -> 0.01
    (
        [ItemCalculationInput(1, "Item", Decimal("1"), Decimal("10.00"), Decimal("0.0005"))],
        Decimal("10.00"), Decimal("0.01"), Decimal("10.01"),
    ),
    # 7. Zero tax rate: 5 @ 25.50
    (
        [ItemCalculationInput(1, "Item", Decimal("5"), Decimal("25.50"), Decimal("0.0000"))],
        Decimal("127.50"), Decimal("0.00"), Decimal("127.50"),
    ),
    # 8. Tiny unit price with large quantity: 10000 @ 0.0012 = 12.00, tax 1.20
    (
        [ItemCalculationInput(1, "Item", Decimal("10000"), Decimal("0.0012"), Decimal("0.1000"))],
        Decimal("12.00"), Decimal("1.20"), Decimal("13.20"),
    ),
    # 9. Tiny unit price resulting in 0 subtotal: 1 @ 0.001
    (
        [ItemCalculationInput(1, "Item", Decimal("1"), Decimal("0.001"), Decimal("0.2000"))],
        Decimal("0.00"), Decimal("0.00"), Decimal("0.00"),
    ),
    # 10. Fractional price: 3 @ 33.3333 = 99.9999 -> 100.00, tax 12% = 12.00
    (
        [ItemCalculationInput(1, "Item", Decimal("3"), Decimal("33.3333"), Decimal("0.1200"))],
        Decimal("100.00"), Decimal("12.00"), Decimal("112.00"),
    ),
    # 11. Large enterprise value: 100 @ 500000.00, 18% tax
    (
        [ItemCalculationInput(1, "Item", Decimal("100"), Decimal("500000.00"), Decimal("0.1800"))],
        Decimal("50000000.00"), Decimal("9000000.00"), Decimal("59000000.00"),
    ),
    # 12. Multi-line independent rounding:
    # Line 1: 1 * 10.005 -> 10.01; tax = 10.01 * 0.05 = 0.5005 -> 0.50; total = 10.51
    # Line 2: 2 * 20.00 -> 40.00; tax = 40.00 * 0.18 = 7.20; total = 47.20
    (
        [
            ItemCalculationInput(1, "Line 1", Decimal("1"), Decimal("10.005"), Decimal("0.0500")),
            ItemCalculationInput(2, "Line 2", Decimal("2"), Decimal("20.00"), Decimal("0.1800")),
        ],
        Decimal("50.01"), Decimal("7.70"), Decimal("57.71"),
    ),
    # 13. Tax rounding up on 0.0050: 1.00 * 0.0050 = 0.0050 -> 0.01
    (
        [ItemCalculationInput(1, "Item", Decimal("1"), Decimal("1.00"), Decimal("0.0050"))],
        Decimal("1.00"), Decimal("0.01"), Decimal("1.01"),
    ),
    # 14. Tax rounding down on 0.0049: 1.00 * 0.0049 = 0.0049 -> 0.00
    (
        [ItemCalculationInput(1, "Item", Decimal("1"), Decimal("1.00"), Decimal("0.0049"))],
        Decimal("1.00"), Decimal("0.00"), Decimal("1.00"),
    ),
    # 15. Fractional consulting hours: 7.75 @ 150.00 = 1162.50, tax 8.25% = 95.90625 -> 95.91
    (
        [ItemCalculationInput(1, "Hours", Decimal("7.75"), Decimal("150.00"), Decimal("0.0825"))],
        Decimal("1162.50"), Decimal("95.91"), Decimal("1258.41"),
    ),
    # 16. Zero price item (free sample)
    (
        [ItemCalculationInput(1, "Free", Decimal("1"), Decimal("0.00"), Decimal("0.1800"))],
        Decimal("0.00"), Decimal("0.00"), Decimal("0.00"),
    ),
    # 17. Many small line items proving line-level rounding sum:
    # 5 items each 0.01 @ 10% tax -> each line tax=0.00 -> sum tax=0.00
    (
        [
            ItemCalculationInput(i, f"Item {i}", Decimal("1"), Decimal("0.01"), Decimal("0.1000"))
            for i in range(1, 6)
        ],
        Decimal("0.05"), Decimal("0.00"), Decimal("0.05"),
    ),
    # 18. Luxury tax 50%: 2 @ 75.25 = 150.50, tax = 75.25
    (
        [ItemCalculationInput(1, "Luxury", Decimal("2"), Decimal("75.25"), Decimal("0.5000"))],
        Decimal("150.50"), Decimal("75.25"), Decimal("225.75"),
    ),
    # 19. European VAT 20%: 4 @ 19.99 = 79.96, tax = 15.992 -> 15.99
    (
        [ItemCalculationInput(1, "Retail", Decimal("4"), Decimal("19.99"), Decimal("0.2000"))],
        Decimal("79.96"), Decimal("15.99"), Decimal("95.95"),
    ),
    # 20. Odd cents rounding: 3 @ 1.33 = 3.99, tax 7% = 0.2793 -> 0.28
    (
        [ItemCalculationInput(1, "Snack", Decimal("3"), Decimal("1.33"), Decimal("0.0700"))],
        Decimal("3.99"), Decimal("0.28"), Decimal("4.27"),
    ),
    # 21. High precision unit price: 120 @ 0.1234 = 14.808 -> 14.81, tax 5% = 0.7405 -> 0.74
    (
        [ItemCalculationInput(1, "Parts", Decimal("120"), Decimal("0.1234"), Decimal("0.0500"))],
        Decimal("14.81"), Decimal("0.74"), Decimal("15.55"),
    ),
]


@pytest.mark.parametrize("items, expected_subtotal, expected_tax, expected_total", CALCULATION_TEST_CASES)
def test_calculation_table_driven_cases(items, expected_subtotal, expected_tax, expected_total):
    result = calculate_invoice_totals(items)
    assert result.subtotal == expected_subtotal
    assert result.tax_amount == expected_tax
    assert result.total_amount == expected_total
    # Total must always equal subtotal + tax_amount
    assert result.total_amount == result.subtotal + result.tax_amount


def test_calculation_strictly_rejects_floats():
    with pytest.raises(ValidationException, match="strictly forbidden"):
        # Passing float for quantity
        calculate_invoice_totals([ItemCalculationInput(1, "Bad", 1.5, Decimal("10.00"), Decimal("0.18"))])

    with pytest.raises(ValidationException, match="strictly forbidden"):
        # Passing float for unit_price
        calculate_invoice_totals([ItemCalculationInput(1, "Bad", Decimal("1"), 10.5, Decimal("0.18"))])

    with pytest.raises(ValidationException, match="strictly forbidden"):
        # Passing float for tax_rate
        calculate_invoice_totals([ItemCalculationInput(1, "Bad", Decimal("1"), Decimal("10.00"), 0.18)])


def test_calculation_rejects_invalid_inputs():
    with pytest.raises(ValidationException, match="quantity must be positive"):
        calculate_invoice_totals([ItemCalculationInput(1, "Bad", Decimal("0"), Decimal("10.00"), Decimal("0.18"))])

    with pytest.raises(ValidationException, match="unit_price cannot be negative"):
        calculate_invoice_totals([ItemCalculationInput(1, "Bad", Decimal("1"), Decimal("-1.00"), Decimal("0.18"))])

    with pytest.raises(ValidationException, match="tax_rate cannot be negative"):
        calculate_invoice_totals([ItemCalculationInput(1, "Bad", Decimal("1"), Decimal("10.00"), Decimal("-0.05"))])
