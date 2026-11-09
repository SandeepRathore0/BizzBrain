from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator
from fastapi import Depends, FastAPI, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.database import engine, get_db_session
from app.core.errors import (
    AppException,
    app_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.core.logging import logger, setup_logging
from app.core.middleware import RequestIDMiddleware
from app.api.customers import router as customers_router
from app.api.suppliers import router as suppliers_router
from app.api.categories import router as categories_router
from app.api.expenses import router as expenses_router
from app.api.summary import router as summary_router
from app.api.internal import router as internal_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    setup_logging(settings.LOG_LEVEL)
    logger.info(
        "service_starting",
        service=settings.SERVICE_NAME,
        environment=settings.ENVIRONMENT,
    )
    yield
    logger.info("service_shutting_down", service=settings.SERVICE_NAME)
    await engine.dispose()


app = FastAPI(
    title="BizBrain Financial Data Service",
    description="Microservice managing customers, suppliers, expenses, categories, receipts and financial summaries.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Middleware
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Idempotent-Replayed"],
)

# Exception handlers
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

# Routers
app.include_router(customers_router)
app.include_router(suppliers_router)
app.include_router(categories_router)
app.include_router(expenses_router)
app.include_router(summary_router)
app.include_router(internal_router)


@app.get("/health", status_code=status.HTTP_200_OK, tags=["Health"])
async def health_check() -> dict[str, Any]:
    return {"status": "ok", "service": settings.SERVICE_NAME}


@app.get("/ready", status_code=status.HTTP_200_OK, tags=["Health"])
async def ready_check(session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    # Check DB connection
    db_status = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception as e:
        logger.error("ready_check_db_failed", error=str(e))
        db_status = "unhealthy"

    is_ready = db_status == "ok"
    return {
        "status": "ready" if is_ready else "not_ready",
        "service": settings.SERVICE_NAME,
        "checks": {
            "database": db_status,
        },
    }
