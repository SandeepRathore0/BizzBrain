"""Application configuration using Pydantic Settings."""

import json
from functools import lru_cache
from typing import Any, Dict, Literal, Optional
from zoneinfo import available_timezones

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.logging import get_logger

logger = get_logger(__name__)

# Standard ISO 4217 3-letter active currency codes
ISO_4217_CODES = frozenset({
    "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN",
    "BAM", "BBD", "BDT", "BGN", "BHD", "BIF", "BMD", "BND", "BOB", "BRL",
    "BSD", "BTN", "BWP", "BYN", "BZD", "CAD", "CDF", "CHF", "CLP", "CNY",
    "COP", "CRC", "CUP", "CVE", "CZK", "DJF", "DKK", "DOP", "DZD", "EGP",
    "ERN", "ETB", "EUR", "FJD", "FKP", "GBP", "GEL", "GHS", "GIP", "GMD",
    "GNF", "GTQ", "GYD", "HKD", "HNL", "HRK", "HTG", "HUF", "IDR", "ILS",
    "INR", "IQD", "IRR", "ISK", "JMD", "JOD", "JPY", "KES", "KGS", "KHR",
    "KMF", "KPW", "KRW", "KWD", "KYD", "KZT", "LAK", "LBP", "LKR", "LRD",
    "LSL", "LYD", "MAD", "MDL", "MGA", "MKD", "MMK", "MNT", "MOP", "MRU",
    "MUR", "MVR", "MWK", "MXN", "MYR", "MZN", "NAD", "NGN", "NIO", "NOK",
    "NPR", "NZD", "OMR", "PAB", "PEN", "PGK", "PHP", "PKR", "PLN", "PYG",
    "QAR", "RON", "RSD", "RUB", "RWF", "SAR", "SBD", "SCR", "SDG", "SEK",
    "SGD", "SHP", "SLL", "SOS", "SRD", "STN", "SVC", "SYP", "SZL", "THB",
    "TJS", "TMT", "TND", "TOP", "TRY", "TTD", "TWD", "TZS", "UAH", "UGX",
    "USD", "UYU", "UZS", "VES", "VND", "VUV", "WST", "XAF", "XCD", "XDR",
    "XOF", "XPF", "YER", "ZAR", "ZMW", "ZWL"
})


class Settings(BaseSettings):
    """Configuration settings for BizBrain User Service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # General App Config
    ENVIRONMENT: Literal["development", "staging", "production", "test"] = "development"
    DEBUG: bool = False
    APP_NAME: str = "bizbrain-user-service"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database Configuration (Runtime role: bizbrain_app)
    DATABASE_URL: str = "postgresql+asyncpg://bizbrain_app:app_runtime_secure_pass_123@localhost:5432/user_db"
    # Migration Database Configuration (Owner role: bizbrain_migrator)
    MIGRATION_DATABASE_URL: Optional[str] = "postgresql+asyncpg://bizbrain_migrator:migrator_secure_pass_123@localhost:5432/user_db"
    DATABASE_ECHO: bool = False
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # RS256 JWT Settings
    JWT_PRIVATE_KEY_PEM: Optional[str] = None
    JWT_PUBLIC_KEY_PEM: Optional[str] = None
    JWT_KEY_ID: str = "bizbrain-user-key-1"
    JWT_ISSUER: str = "bizbrain-user-service"
    JWT_AUDIENCE: str = "bizbrain-services"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    SERVICE_TOKEN_EXPIRE_MINUTES: int = 5
    EMAIL_TOKEN_EXPIRE_HOURS: int = 24

    # Security & Throttling
    MAX_FAILED_LOGIN_ATTEMPTS: int = 5
    ACCOUNT_LOCKOUT_MINUTES: int = 15
    ALLOW_EMPLOYEE_BUSINESS_CREATION: bool = False

    # Internal Client Credentials for /internal/v1/service-token
    # Format: {"client_id": "client_secret"}
    INTERNAL_SERVICE_CLIENTS: Dict[str, str] = Field(
        default_factory=lambda: {
            "invoice-service": "secret-invoice-key-123",
            "financial-service": "secret-financial-key-123",
            "payment-service": "secret-payment-key-123",
        }
    )

    @field_validator("INTERNAL_SERVICE_CLIENTS", mode="before")
    @classmethod
    def parse_clients_json(cls, v: Any) -> Dict[str, str]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return {}
        if isinstance(v, dict):
            return v
        return {}

    @model_validator(mode="after")
    def validate_production_keys(self) -> "Settings":
        """In production or staging, refuse to start without explicit RSA keys."""
        if self.ENVIRONMENT not in ("development", "test"):
            if not self.JWT_PRIVATE_KEY_PEM or not self.JWT_PRIVATE_KEY_PEM.strip():
                raise ValueError(
                    f"JWT_PRIVATE_KEY_PEM must be provided when ENVIRONMENT='{self.ENVIRONMENT}'. "
                    "Transient keys are forbidden outside development and test."
                )
            if not self.JWT_KEY_ID or not self.JWT_KEY_ID.strip():
                raise ValueError("JWT_KEY_ID must be configured in production environments.")
        return self

    def get_private_key_pem(self) -> str:
        """Returns the private key PEM or auto-generates one for development/test environments."""
        if self.JWT_PRIVATE_KEY_PEM and self.JWT_PRIVATE_KEY_PEM.strip():
            return self.JWT_PRIVATE_KEY_PEM
        if self.ENVIRONMENT not in ("development", "test"):
            raise ValueError(f"Transient keys not permitted in ENVIRONMENT='{self.ENVIRONMENT}'")
        logger.warning(
            "using_transient_in_memory_rsa_key",
            message="Generating transient 2048-bit RSA key for development/test use. Do not use in production.",
        )
        priv, _ = _get_or_create_transient_keypair()
        return priv

    def get_public_key_pem(self) -> str:
        """Returns the public key PEM or auto-generates one for development/test environments."""
        if self.JWT_PUBLIC_KEY_PEM and self.JWT_PUBLIC_KEY_PEM.strip():
            return self.JWT_PUBLIC_KEY_PEM
        if self.ENVIRONMENT not in ("development", "test"):
            raise ValueError(f"Transient keys not permitted in ENVIRONMENT='{self.ENVIRONMENT}'")
        _, pub = _get_or_create_transient_keypair()
        return pub

    @staticmethod
    def is_valid_currency(currency_code: str) -> bool:
        """Check if a currency code is a valid ISO 4217 code."""
        return currency_code.upper() in ISO_4217_CODES

    @staticmethod
    def is_valid_timezone(tz_name: str) -> bool:
        """Check if a timezone name is a valid IANA timezone."""
        return tz_name in available_timezones()


_transient_private_pem: Optional[str] = None
_transient_public_pem: Optional[str] = None


def _get_or_create_transient_keypair() -> tuple[str, str]:
    """Generate an in-memory 2048-bit RSA key pair for local development or tests if not provided."""
    global _transient_private_pem, _transient_public_pem
    if _transient_private_pem is None or _transient_public_pem is None:
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )
        _transient_private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

        public_key = private_key.public_key()
        _transient_public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
    return _transient_private_pem, _transient_public_pem


@lru_cache()
def get_settings() -> Settings:
    """Cached singleton of settings."""
    return Settings()
