"""HTTP middleware components for Request ID tracing and context binding."""

import time
import uuid
from typing import Callable
import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import get_logger

logger = get_logger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware that:
    1. Extracts or generates X-Request-ID.
    2. Binds request_id to structlog contextvars for structured JSON logging.
    3. Sets request.state.request_id for downstream route handlers.
    4. Attaches X-Request-ID header to the HTTP response.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Clear contextvars from previous requests on the same worker thread
        structlog.contextvars.clear_contextvars()

        incoming_request_id = request.headers.get("X-Request-ID")
        if incoming_request_id and len(incoming_request_id.strip()) > 0:
            request_id = incoming_request_id.strip()
        else:
            request_id = str(uuid.uuid4())

        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start_time = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

        response.headers["X-Request-ID"] = request_id

        # Log request completion with duration (excluding health checks to avoid noise)
        if not request.url.path.endswith("/health"):
            logger.info(
                "http_request_finished",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )

        return response
