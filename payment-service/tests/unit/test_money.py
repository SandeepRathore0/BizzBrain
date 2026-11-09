from decimal import Decimal
import pytest

from app.core.errors import UnprocessableEntityException
from app.core.money import format_amount, parse_and_validate_amount, validate_currency


def test_parse_and_validate_amount_valid():
    assert parse_and_validate_amount("100.50", "USD") == Decimal("100.50")
    assert parse_and_validate_amount("50", "USD") == Decimal("50.00")
    assert parse_and_validate_amount(Decimal("25.00"), "EUR") == Decimal("25.00")
    assert parse_and_validate_amount("1000", "JPY") == Decimal("1000")
    assert parse_and_validate_amount("10.125", "BHD") == Decimal("10.125")


def test_parse_and_validate_amount_rejects_float():
    with pytest.raises(UnprocessableEntityException) as exc:
        parse_and_validate_amount(100.5, "USD")
    assert exc.value.code == "INVALID_AMOUNT"


def test_parse_and_validate_amount_rejects_negative_or_zero():
    with pytest.raises(UnprocessableEntityException) as exc:
        parse_and_validate_amount("0", "USD")
    assert exc.value.code == "AMOUNT_MUST_BE_POSITIVE"

    with pytest.raises(UnprocessableEntityException) as exc:
        parse_and_validate_amount("-15.00", "USD")
    assert exc.value.code == "AMOUNT_MUST_BE_POSITIVE"


def test_parse_and_validate_amount_rejects_invalid_precision():
    with pytest.raises(UnprocessableEntityException) as exc:
        parse_and_validate_amount("10.555", "USD")
    assert exc.value.code == "INVALID_AMOUNT_PRECISION"

    with pytest.raises(UnprocessableEntityException) as exc:
        parse_and_validate_amount("10.5", "JPY")
    assert exc.value.code == "INVALID_AMOUNT_PRECISION"


def test_format_amount():
    assert format_amount(Decimal("100.5"), "USD") == "100.50"
    assert format_amount(Decimal("1000"), "JPY") == "1000"
    assert format_amount(Decimal("12.345"), "BHD") == "12.345"


def test_validate_currency():
    assert validate_currency("usd") == "USD"
    assert validate_currency("EUR") == "EUR"

    with pytest.raises(UnprocessableEntityException):
        validate_currency("US")

    with pytest.raises(UnprocessableEntityException):
        validate_currency("US1")
