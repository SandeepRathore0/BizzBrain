"""Unit tests for configuration, timezone, currency, and key generation."""

from app.core.config import Settings, get_settings


def test_settings_initialization():
    settings = get_settings()
    assert settings.APP_NAME == "bizbrain-user-service"
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == 10
    assert settings.REFRESH_TOKEN_EXPIRE_DAYS == 14
    assert settings.SERVICE_TOKEN_EXPIRE_MINUTES == 5


def test_currency_validation():
    settings = get_settings()
    # Valid ISO 4217 currencies
    assert settings.is_valid_currency("USD") is True
    assert settings.is_valid_currency("EUR") is True
    assert settings.is_valid_currency("INR") is True
    assert settings.is_valid_currency("gbp") is True  # case insensitive

    # Invalid currencies
    assert settings.is_valid_currency("XYZ") is False
    assert settings.is_valid_currency("FAKE") is False
    assert settings.is_valid_currency("123") is False


def test_timezone_validation():
    settings = get_settings()
    # Valid IANA timezones
    assert settings.is_valid_timezone("UTC") is True
    assert settings.is_valid_timezone("America/New_York") is True
    assert settings.is_valid_timezone("Asia/Kolkata") is True
    assert settings.is_valid_timezone("Europe/London") is True

    # Invalid timezones
    assert settings.is_valid_timezone("Mars/Olympus_Mons") is False
    assert settings.is_valid_timezone("Invalid/Zone") is False
    assert settings.is_valid_timezone("") is False


def test_transient_keypair_generation():
    settings = get_settings()
    private_pem = settings.get_private_key_pem()
    public_pem = settings.get_public_key_pem()

    assert "PRIVATE KEY" in private_pem and "-----BEGIN" in private_pem
    assert "PUBLIC KEY" in public_pem and "-----BEGIN" in public_pem
