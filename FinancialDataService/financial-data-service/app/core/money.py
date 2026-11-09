from decimal import Decimal
from typing import Dict
from app.core.errors import ValidationException

# Map of currency to minor unit decimal places
CURRENCY_MINOR_UNITS: Dict[str, int] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "INR": 2,
    "CAD": 2,
    "AUD": 2,
    "JPY": 0,
}


def validate_expense_amount(amount: Decimal, currency: str) -> None:
    if amount <= Decimal("0"):
        raise ValidationException(
            code="INVALID_AMOUNT",
            message="Expense amount must be greater than zero",
        )

    expected_decimals = CURRENCY_MINOR_UNITS.get(currency.upper(), 2)

    # Check decimal places
    # as_tuple().exponent gives negative of decimal places, e.g. Decimal("10.50").as_tuple().exponent is -2
    exponent = amount.as_tuple().exponent
    decimal_places = -exponent if exponent < 0 else 0

    if decimal_places > expected_decimals:
        raise ValidationException(
            code="INVALID_PRECISION",
            message=f"Currency '{currency}' allows at most {expected_decimals} decimal places, received {decimal_places}",
        )
