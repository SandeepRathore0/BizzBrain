import pytest
from app.core.config import Settings


def test_config_defaults():
    s = Settings()
    assert s.SERVICE_NAME == "invoice-service"
    assert "invoice_app" in s.DATABASE_URL
    assert "invoice_owner" in s.MIGRATION_DATABASE_URL
    assert s.JWT_AUDIENCE == "bizbrain-api"
    assert s.INTERNAL_JWT_AUDIENCE == "invoice-service"


def test_production_validation_rejects_insecure_defaults():
    s = Settings(ENVIRONMENT="production", SERVICE_CLIENT_SECRET="invoice-service-secret")
    with pytest.raises(ValueError, match="SERVICE_CLIENT_SECRET"):
        s.validate_production()


def test_production_validation_rejects_localhost_db():
    s = Settings(
        ENVIRONMENT="production",
        SERVICE_CLIENT_SECRET="secure-prod-secret-123",
        DATABASE_URL="postgresql+asyncpg://invoice_app:invoice_app_pass@localhost:5432/invoice_db",
    )
    with pytest.raises(ValueError, match="production database credentials"):
        s.validate_production()
