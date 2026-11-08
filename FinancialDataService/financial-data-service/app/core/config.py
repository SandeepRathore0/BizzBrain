from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8002

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://financial_user:financial_pass@localhost:5432/financial_db"
    DATABASE_ECHO: bool = False

    # User Service
    USER_SERVICE_BASE_URL: str = "http://localhost:8001"
    SERVICE_NAME: str = "financial-data-service"
    SERVICE_CLIENT_ID: str = "financial-data-service"
    SERVICE_CLIENT_SECRET: str = "change-me-in-production"
    JWT_AUDIENCE: str = "bizbrain"
    JWKS_CACHE_TTL_SECONDS: int = 3600
    BUSINESS_CACHE_TTL_SECONDS: int = 300

    # Storage (S3 / MinIO)
    S3_ENDPOINT_URL: Optional[str] = "http://localhost:9000"
    S3_ACCESS_KEY: Optional[str] = "minioadmin"
    S3_SECRET_KEY: Optional[str] = "minioadmin"
    S3_BUCKET_NAME: str = "receipts"
    S3_REGION: str = "us-east-1"

    # CORS
    ALLOWED_ORIGINS: List[str] = Field(default_factory=lambda: ["http://localhost:3000"])


settings = Settings()
