import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_paypal_env_strictly_sandbox():
    # Valid
    s = Settings(PAYPAL_ENV="sandbox")
    assert s.PAYPAL_ENV == "sandbox"

    # Reject non-sandbox
    with pytest.raises(ValidationError):
        Settings(PAYPAL_ENV="live")

    with pytest.raises(ValidationError):
        Settings(PAYPAL_ENV="production")


def test_paypal_live_url_rejected():
    with pytest.raises(ValidationError):
        Settings(
            PAYPAL_ENV="sandbox",
            PAYPAL_BASE_URL="https://api-m.paypal.com",
        )

    with pytest.raises(ValidationError):
        Settings(
            PAYPAL_ENV="sandbox",
            PAYPAL_BASE_URL="https://api.paypal.com",
        )


def test_production_missing_secrets_rejected():
    with pytest.raises(ValidationError):
        Settings(
            ENVIRONMENT="production",
            PAYPAL_CLIENT_ID="placeholder",
            PAYPAL_CLIENT_SECRET="placeholder",
        )
