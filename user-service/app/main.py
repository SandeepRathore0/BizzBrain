"""Main FastAPI application entrypoint for BizBrain User Service."""

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.jwks import router as jwks_router
from app.api.v1.auth import router as auth_router
from app.api.v1.businesses import router as businesses_router
from app.api.internal.v1.internal import router as internal_router
from app.core.config import get_settings
from app.core.database import close_database_connections
from app.core.errors import register_error_handlers
from app.core.logging import get_logger, setup_logging
from app.core.middleware import RequestIDMiddleware

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown lifespan management."""
    settings = get_settings()
    setup_logging(debug=settings.DEBUG)
    logger.info(
        "service_starting",
        service=settings.APP_NAME,
        environment=settings.ENVIRONMENT,
        debug=settings.DEBUG,
    )
    yield
    logger.info("service_stopping", service=settings.APP_NAME)
    await close_database_connections()
    logger.info("service_stopped", service=settings.APP_NAME)


def create_app() -> FastAPI:
    """Application factory for BizBrain User Service."""
    settings = get_settings()

    app = FastAPI(
        title="BizBrain User Service",
        description=(
            "Identity and access authority for the BizBrain multi-tenant small-business finance platform. "
            "Manages users, businesses, memberships, roles, sessions, and RS256 token issuance."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Middleware: Request ID tracing and log context binding
    app.add_middleware(RequestIDMiddleware)

    # Global error handlers
    register_error_handlers(app)

    # Health Check
    @app.get("/health", tags=["Health"])
    async def health_check(request: Request) -> Dict[str, Any]:
        request_id = getattr(request.state, "request_id", "unknown")
        return {
            "success": True,
            "data": {
                "status": "healthy",
                "service": settings.APP_NAME,
                "environment": settings.ENVIRONMENT,
            },
            "request_id": request_id,
        }

    # Public JWKS endpoint
    app.include_router(jwks_router)

    # Public API routes (v1)
    app.include_router(auth_router)
    app.include_router(businesses_router)

    # Internal routes (not exposed publicly – rely on network-layer controls + service token auth)
    app.include_router(internal_router)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
