import time
import uuid
from collections import defaultdict
from typing import Callable, Optional
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.errors import APIException, create_error_response


class RequestTracingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Extract or generate X-Request-ID
        req_id = request.headers.get("X-Request-ID")
        if not req_id:
            req_id = str(uuid.uuid4())
        
        request.state.request_id = req_id

        # Bind request_id to structlog contextvars
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=req_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response


class PublicPayRateLimiterMiddleware(BaseHTTPMiddleware):
    """Rate limiter for /api/v1/public/pay endpoints (20 req/min per IP)."""
    _instance: Optional["PublicPayRateLimiterMiddleware"] = None

    def __init__(self, app: FastAPI, max_requests: int = 20, window_seconds: float = 60.0):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.ip_hits: dict[str, list[float]] = defaultdict(list)
        PublicPayRateLimiterMiddleware._instance = self

    @classmethod
    def reset(cls) -> None:
        if cls._instance:
            cls._instance.ip_hits.clear()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path.startswith("/api/v1/public/pay"):
            client_ip = request.client.host if request.client else "unknown"
            now = time.time()
            window_start = now - self.window_seconds

            # Filter old timestamps
            self.ip_hits[client_ip] = [t for t in self.ip_hits[client_ip] if t > window_start]

            if len(self.ip_hits[client_ip]) >= self.max_requests:
                req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
                return create_error_response(
                    status_code=429,
                    code="RATE_LIMIT_EXCEEDED",
                    message="Rate limit exceeded. Please try again later.",
                    request_id=req_id,
                )

            self.ip_hits[client_ip].append(now)

        return await call_next(request)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIException)
    async def api_exception_handler(request: Request, exc: APIException) -> JSONResponse:
        req_id = getattr(request.state, "request_id", None)
        return create_error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
            request_id=req_id,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        req_id = getattr(request.state, "request_id", None)
        return create_error_response(
            status_code=422,
            code="VALIDATION_ERROR",
            message="Request validation failed.",
            details=exc.errors(),
            request_id=req_id,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        req_id = getattr(request.state, "request_id", None)
        logger = structlog.get_logger("unhandled_exception")
        logger.error("unhandled_error", error=str(exc), request_id=req_id)
        return create_error_response(
            status_code=500,
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected server error occurred.",
            request_id=req_id,
        )
