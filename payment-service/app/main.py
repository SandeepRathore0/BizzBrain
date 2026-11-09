from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.payments import router as payments_router
from app.api.public_pay import router as public_pay_router
from app.api.webhooks import router as webhooks_router
from app.core.config import get_settings
from app.core.database import get_engine
from app.core.logging import get_logger, setup_logging
from app.core.middleware import (
    PublicPayRateLimiterMiddleware,
    RequestTracingMiddleware,
    register_error_handlers,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    logger = get_logger("payment_main")
    logger.info(
        "service_starting",
        environment=settings.ENVIRONMENT,
        paypal_env=settings.PAYPAL_ENV,
    )
    yield
    engine = get_engine()
    await engine.dispose()
    logger.info("service_shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="BizBrain Payment Service",
        version="1.0.0",
        description="Payment lifecycle, PayPal integration, and outbox event dispatch.",
        lifespan=lifespan,
    )

    # Middlewares (Order: outer first)
    app.add_middleware(RequestTracingMiddleware)
    app.add_middleware(PublicPayRateLimiterMiddleware, max_requests=20, window_seconds=60.0)

    # Register Global Exception Handlers
    register_error_handlers(app)

    # Mount Routers
    app.include_router(health_router)
    app.include_router(payments_router, prefix="/api/v1")
    app.include_router(public_pay_router, prefix="/api/v1")
    app.include_router(webhooks_router, prefix="/api/v1")

    return app


app = create_app()
