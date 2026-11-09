import os
from typing import List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENVIRONMENT: str = Field(default="development")
    SERVICE_NAME: str = Field(default="invoice-service")
    DEBUG: bool = Field(default=False)

    # Database URLs
    # Runtime URL (uses restricted invoice_app role)
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://invoice_app:invoice_app_pass@localhost:5432/invoice_db"
    )
    # Migration URL (uses table owner / migration role invoice_owner)
    MIGRATION_DATABASE_URL: str = Field(
        default="postgresql+psycopg://invoice_owner:invoice_owner_pass@localhost:5432/invoice_db"
    )

    # Inter-service URLs
    USER_SERVICE_URL: str = Field(default="http://localhost:8001")
    FINANCIAL_DATA_SERVICE_URL: str = Field(default="http://localhost:8002")

    # Service Credentials for internal calls (C5)
    SERVICE_CLIENT_ID: str = Field(default="invoice-service")
    SERVICE_CLIENT_SECRET: str = Field(default="invoice-service-secret")

    # JWT Settings (RS256 verification via User Service JWKS)
    JWT_ISSUER: str = Field(default="bizbrain-user-service")
    JWT_AUDIENCE: str = Field(default="bizbrain-api")
    INTERNAL_JWT_AUDIENCE: str = Field(default="invoice-service")
    JWKS_URL: Optional[str] = None
    JWKS_CACHE_TTL_SECONDS: int = Field(default=3600)

    # Internal callers allow-list
    ALLOWED_INTERNAL_PAYMENT_CALLERS: List[str] = Field(default=["payment-service"])

    @field_validator("JWKS_URL", mode="after")
    @classmethod
    def assemble_jwks_url(cls, v: Optional[str], info) -> str:
        if v:
            return v
        user_service = info.data.get("USER_SERVICE_URL", "http://localhost:8001")
        return f"{user_service.rstrip('/')}/.well-known/jwks.json"

    def validate_production(self) -> None:
        if self.ENVIRONMENT == "production":
            if self.SERVICE_CLIENT_SECRET in ("invoice-service-secret", ""):
                raise ValueError("In production, SERVICE_CLIENT_SECRET must be configured with a secure secret.")
            if "localhost" in self.DATABASE_URL or "invoice_app_pass" in self.DATABASE_URL:
                raise ValueError("In production, secure production database credentials must be provided.")


settings = Settings()
