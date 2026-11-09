from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Optional
from app.core.errors import UnprocessableEntityException

# Standard currency exponent mapping (ISO 4217)
# 0 decimals: JPY, KRW, etc.
# 3 decimals: BHD, KWD, OMR, TND, JOD
# 2 decimals: USD, EUR, GBP, CAD, AUD, INR, etc.
CURRENCY_EXPONENTS: dict[str, int] = {
    "JPY": 0,
    "KRW": 0,
    "BHD": 3,
    "KWD": 3,
    "OMR": 3,
    "TND": 3,
    "JOD": 3,
}


def get_currency_decimals(currency: str) -> int:
    return CURRENCY_EXPONENTS.get(currency.upper(), 2)


def parse_and_validate_amount(amount_val: str | Decimal | int, currency: str) -> Decimal:
    """Parse amount to Decimal and validate it is positive and conforms to currency precision."""
    try:
        if isinstance(amount_val, float):
            raise ValueError("Float amounts are not permitted; provide a string or Decimal.")
        dec = Decimal(str(amount_val).strip())
    except (InvalidOperation, ValueError):
        raise UnprocessableEntityException(
            code="INVALID_AMOUNT",
            message="Amount must be a valid numeric decimal string.",
        )

    if dec <= Decimal("0"):
        raise UnprocessableEntityException(
            code="AMOUNT_MUST_BE_POSITIVE",
            message="Payment amount must be strictly greater than zero.",
        )

    # Check precision
    decimals = get_currency_decimals(currency)
    quantized = dec.quantize(Decimal(f"1e-{decimals}"), rounding=ROUND_HALF_UP)
    if dec != quantized:
        raise UnprocessableEntityException(
            code="INVALID_AMOUNT_PRECISION",
            message=f"Amount exceeds maximum precision of {decimals} decimal places for {currency.upper()}.",
        )

    return dec


def format_amount(amount: Decimal, currency: str) -> str:
    """Format decimal amount as string with currency-specific decimal places."""
    decimals = get_currency_decimals(currency)
    quantized = amount.quantize(Decimal(f"1e-{decimals}"), rounding=ROUND_HALF_UP)
    if decimals == 0:
        return f"{int(quantized)}"
    return f"{quantized:.{decimals}f}"


def validate_currency(currency: str) -> str:
    curr = currency.strip().upper()
    if len(curr) != 3 or not curr.isalpha():
        raise UnprocessableEntityException(
            code="INVALID_CURRENCY",
            message="Currency must be a valid 3-letter ISO 4217 code.",
        )
    return curr
