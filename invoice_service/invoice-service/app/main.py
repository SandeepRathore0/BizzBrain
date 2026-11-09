from contextlib import asynccontextmanager
from fastapi import FastAPI, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text
from app.core.config import settings
from app.core.database import engine
from app.core.errors import (
    AppException,
    app_exception_handler,
    generic_exception_handler,
    validation_exception_handler,
)
from app.core.logging import logger, setup_logging
from app.core.middleware import RequestIDMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.DEBUG)
    logger.info(
        "service_starting",
        service=settings.SERVICE_NAME,
        environment=settings.ENVIRONMENT,
    )
    # Refuse to start in production if improperly configured
    settings.validate_production()

    yield

    logger.info("service_stopping", service=settings.SERVICE_NAME)
    await engine.dispose()


app = FastAPI(
    title="BizBrain Invoice Service",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Middleware
app.add_middleware(RequestIDMiddleware)

# Exception handlers
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)


# Routers
from app.api.invoices import router as invoices_router
from app.api.settings import router as settings_router
from app.api.tax_rates import router as tax_rates_router
from app.api.internal import router as internal_router

app.include_router(invoices_router)
app.include_router(settings_router)
app.include_router(tax_rates_router)
app.include_router(internal_router)


@app.get("/health", status_code=status.HTTP_200_OK)
async def health():
    return {"status": "healthy", "service": settings.SERVICE_NAME}


@app.get("/ready", status_code=status.HTTP_200_OK)
async def ready():
    # Check DB connectivity
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception as e:
        logger.error("readiness_check_failed", error=str(e))
        return {"status": "not_ready", "database": "disconnected"}
