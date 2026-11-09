from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Union
from app.core.errors import ValidationException

# Currency minor unit decimals: USD=2, INR=2
DEFAULT_MINOR_UNITS = 2
ZERO_MONEY = Decimal("0.00")


def round_money(val: Decimal, minor_units: int = DEFAULT_MINOR_UNITS) -> Decimal:
    """Rounds a Decimal value to the currency's minor units using ROUND_HALF_UP."""
    if not isinstance(val, Decimal):
        raise ValidationException("Money calculation requires Decimal type, float is strictly forbidden")
    exp = Decimal(10) ** -minor_units
    return val.quantize(exp, rounding=ROUND_HALF_UP)


def format_money(val: Decimal, minor_units: int = DEFAULT_MINOR_UNITS) -> str:
    """Formats a Decimal value to a string with exact minor unit decimal places."""
    return f"{round_money(val, minor_units):.{minor_units}f}"


def validate_not_float(v: Any) -> Decimal:
    """Validates that a monetary or numerical value is NOT a Python float.
    Accepts Decimal, str, or int.
    """
    if isinstance(v, float):
        raise ValidationException("Floating point numbers are strictly forbidden for financial values. Use string or Decimal.")
    if isinstance(v, Decimal):
        return v
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, str):
        try:
            return Decimal(v)
        except Exception:
            raise ValidationException(f"Invalid decimal string format: {v}")
    raise ValidationException(f"Unsupported number format: {type(v)}")
