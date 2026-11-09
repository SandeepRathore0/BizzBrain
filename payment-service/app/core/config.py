import os
from functools import lru_cache
from typing import Literal
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENVIRONMENT: Literal["development", "test", "production"] = "development"
    LOG_LEVEL: str = "INFO"

    # Database
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://payment_app:app_runtime_secure_pass_123@localhost:5432/payment_db"
    )
    MIGRATION_DATABASE_URL: str = Field(
        default="postgresql+asyncpg://payment_migrator:migrator_secure_pass_123@localhost:5432/payment_db"
    )

    # PayPal (Strictly Sandbox in Stage 1)
    PAYPAL_ENV: Literal["sandbox"] = "sandbox"
    PAYPAL_BASE_URL: str = "https://api-m.sandbox.paypal.com"
    PAYPAL_CLIENT_ID: str = "sandbox_client_id_placeholder"
    PAYPAL_CLIENT_SECRET: str = "sandbox_client_secret_placeholder"
    PAYPAL_WEBHOOK_ID: str = "sandbox_webhook_id_placeholder"

    # External Services
    USER_SERVICE_BASE_URL: str = "http://localhost:8001"
    USER_SERVICE_JWKS_URL: str = "http://localhost:8001/.well-known/jwks.json"
    USER_SERVICE_CLIENT_ID: str = "payment-service"
    USER_SERVICE_CLIENT_SECRET: str = "payment_service_secret_placeholder"
    INVOICE_SERVICE_BASE_URL: str = "http://localhost:8002"

    # Worker Settings
    OUTBOX_POLL_INTERVAL_SECONDS: float = 2.0
    OUTBOX_POLL_INTERVAL_WORK: float = 2.0
    OUTBOX_POLL_INTERVAL_IDLE: float = 5.0
    OUTBOX_BATCH_SIZE: int = 20
    OUTBOX_VISIBILITY_TIMEOUT_SECONDS: int = 60
    OUTBOX_MAX_ATTEMPTS: int = 10
    OUTBOX_CIRCUIT_BREAKER_FAILURES: int = 5
    OUTBOX_CIRCUIT_BREAKER_COOLDOWN_SECONDS: float = 30.0

    # Reconciliation Worker Settings
    RECON_INTERVAL_SECONDS: float = 60.0
    RECONCILIATION_INTERVAL_SECONDS: float = 60.0
    RECON_BATCH_SIZE: int = 50
    RECON_MIN_AGE_SECONDS: int = 120
    RECON_NO_CAPTURE_WINDOW_SECONDS: int = 900
    RECON_CAPTURE_PENDING_ALERT_SECONDS: int = 1800
    RECON_ALLOCATION_STUCK_SECONDS: int = 900
    RECON_REVIEW_STUCK_SECONDS: int = 3600
    RECON_DEAD_OUTBOX_ALERT_INTERVAL_SECONDS: int = 3600

    # Currency verification check: default supported currency list for sandbox
    SUPPORTED_CURRENCIES: tuple[str, ...] = ("USD", "EUR", "GBP", "CAD", "AUD", "INR")

    @field_validator("PAYPAL_ENV")
    @classmethod
    def validate_paypal_env(cls, v: str) -> str:
        if v.lower() != "sandbox":
            raise ValueError(f"PAYPAL_ENV must be 'sandbox' in this environment; live is prohibited. Got: {v}")
        return v.lower()

    @field_validator("PAYPAL_BASE_URL")
    @classmethod
    def validate_paypal_base_url(cls, v: str) -> str:
        prohibited_live_hosts = ["api.paypal.com", "api-m.paypal.com"]
        for host in prohibited_live_hosts:
            if host in v and "sandbox" not in v:
                raise ValueError(f"Live PayPal host {host} is prohibited. Base URL must be sandbox.")
        return v

    @model_validator(mode="after")
    def validate_production_and_test_config(self) -> "Settings":
        # In non-development environments refuse to start when JWKS, service credentials,
        # PayPal credentials, webhook id, or invoice service URL are missing / placeholder.
        if self.ENVIRONMENT not in ("development", "test"):
            placeholders = ["placeholder", "mock", "test"]
            if not self.PAYPAL_CLIENT_ID or any(p in self.PAYPAL_CLIENT_ID.lower() for p in placeholders):
                raise ValueError("PAYPAL_CLIENT_ID must be configured for non-development environments.")
            if not self.PAYPAL_CLIENT_SECRET or any(p in self.PAYPAL_CLIENT_SECRET.lower() for p in placeholders):
                raise ValueError("PAYPAL_CLIENT_SECRET must be configured for non-development environments.")
            if not self.PAYPAL_WEBHOOK_ID or any(p in self.PAYPAL_WEBHOOK_ID.lower() for p in placeholders):
                raise ValueError("PAYPAL_WEBHOOK_ID must be configured for non-development environments.")
            if not self.USER_SERVICE_CLIENT_SECRET or any(p in self.USER_SERVICE_CLIENT_SECRET.lower() for p in placeholders):
                raise ValueError("USER_SERVICE_CLIENT_SECRET must be configured for non-development environments.")
            if not self.INVOICE_SERVICE_BASE_URL or any(p in self.INVOICE_SERVICE_BASE_URL.lower() for p in placeholders):
                raise ValueError("INVOICE_SERVICE_BASE_URL must be configured for non-development environments.")
                raise ValueError("USER_SERVICE_CLIENT_SECRET must be configured for production.")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
