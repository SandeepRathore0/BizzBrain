"""Contract tests for contracts/events/PaymentCaptured.v1.json.

Run from the service root:  pytest tests/unit/test_payment_captured_contract.py
Requires:  pip install jsonschema rfc3339-validator
Both the Payment Service (producer) and the Invoice Service (consumer) must keep an
identical copy of the schema and run this same test.
"""
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "events" / "PaymentCaptured.v1.json"

VALID = {
    "event_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "event_type": "PaymentCaptured",
    "event_version": 1,
    "occurred_at": "2026-11-07T10:30:00Z",
    "request_id": "req-1001",
    "business_id": "3f2b8c1e-5a4d-4e8f-9b7a-1c2d3e4f5a6b",
    "payment_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "invoice_id": "1b4e28ba-2fa1-11d2-883f-0016d3cca427",
    "amount": "50000.00",
    "currency": "INR",
    "provider_capture_id": "PAYPAL-CAPTURE-123",
}


@pytest.fixture(scope="module")
def validator():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def errors(validator, payload):
    return list(validator.iter_errors(payload))


def mutate(**changes):
    payload = copy.deepcopy(VALID)
    for key, value in changes.items():
        if value is ...:
            payload.pop(key)
        else:
            payload[key] = value
    return payload


def test_valid_payload_passes(validator):
    assert errors(validator, VALID) == []


@pytest.mark.parametrize("amount", ["0", "0.01", "1", "50000", "50000.00", "123456789012.3456", "99.9999"])
def test_valid_amounts(validator, amount):
    assert errors(validator, mutate(amount=amount)) == []


@pytest.mark.parametrize("occurred_at", ["2026-11-07T10:30:00Z", "2026-11-07T10:30:00.123Z", "2026-02-28T23:59:59Z"])
def test_valid_timestamps(validator, occurred_at):
    assert errors(validator, mutate(occurred_at=occurred_at)) == []


INVALID_CASES = {
    "extra_field": mutate(note="hello"),
    "float_amount": mutate(amount=50000.0),
    "int_amount": mutate(amount=50000),
    "negative_amount": mutate(amount="-1.00"),
    "plus_sign_amount": mutate(amount="+5.00"),
    "leading_zero_amount": mutate(amount="01.00"),
    "comma_amount": mutate(amount="1,000.00"),
    "too_many_decimals": mutate(amount="1.00001"),
    "empty_amount": mutate(amount=""),
    "exponent_amount": mutate(amount="1e3"),
    "lowercase_currency": mutate(currency="inr"),
    "long_currency": mutate(currency="INRR"),
    "numeric_currency": mutate(currency="356"),
    "non_uuid_event_id": mutate(event_id="evt-1001"),
    "uppercase_uuid": mutate(event_id="7C9E6679-7425-40DE-944B-E07FC1F90AE7"),
    "non_uuid_business": mutate(business_id="biz-001"),
    "non_uuid_payment": mutate(payment_id="pay-101"),
    "non_uuid_invoice": mutate(invoice_id="inv-001"),
    "wrong_event_type": mutate(event_type="PaymentRefunded"),
    "wrong_version": mutate(event_version=2),
    "string_version": mutate(event_version="1"),
    "timestamp_without_z": mutate(occurred_at="2026-11-07T10:30:00+05:30"),
    "timestamp_no_zone": mutate(occurred_at="2026-11-07T10:30:00"),
    "timestamp_bad_month": mutate(occurred_at="2026-13-07T10:30:00Z"),
    "empty_request_id": mutate(request_id=""),
    "trailing_newline_currency": mutate(currency="INR\n"),
    "trailing_newline_amount": mutate(amount="5\n"),
    "trailing_newline_event_id": mutate(event_id="7c9e6679-7425-40de-944b-e07fc1f90ae7\n"),
    "trailing_newline_timestamp": mutate(occurred_at="2026-11-07T10:30:00Z\n"),
    "leading_space_amount": mutate(amount=" 5.00"),
    "space_in_request_id": mutate(request_id="req 1001"),
    "space_in_capture_id": mutate(provider_capture_id="PAYPAL CAPTURE"),
    "empty_capture_id": mutate(provider_capture_id=""),
    "null_capture_id": mutate(provider_capture_id=None),
    "long_capture_id": mutate(provider_capture_id="X" * 65),
}
for _field in VALID:
    INVALID_CASES[f"missing_{_field}"] = mutate(**{_field: ...})


@pytest.mark.parametrize("name", sorted(INVALID_CASES))
def test_invalid_payload_rejected(validator, name):
    assert errors(validator, INVALID_CASES[name]), f"{name} should have been rejected"


def test_amount_zero_is_format_valid_but_semantically_rejected_by_consumer(validator):
    """Documented split of responsibility: the schema checks format only.
    '0' passes here; the Invoice Service handler rejects it with an INVALID_AMOUNT
    reconciliation exception."""
    assert errors(validator, mutate(amount="0")) == []
