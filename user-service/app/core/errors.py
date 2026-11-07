"""Domain exception classes and global error handlers."""

from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)


class AppException(Exception):
    """Base application domain exception."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        details: Optional[List[Dict[str, Any]]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or []


class BadRequestException(AppException):
    """400 Bad Request."""

    def __init__(
        self,
        message: str = "Bad request",
        code: str = "BAD_REQUEST",
        details: Optional[List[Dict[str, Any]]] = None,
    ):
        super().__init__(message, code=code, status_code=status.HTTP_400_BAD_REQUEST, details=details)


class UnauthorizedException(AppException):
    """401 Unauthorized."""

    def __init__(
        self,
        message: str = "Authentication required",
        code: str = "UNAUTHORIZED",
        details: Optional[List[Dict[str, Any]]] = None,
    ):
        super().__init__(message, code=code, status_code=status.HTTP_401_UNAUTHORIZED, details=details)


class InvalidCredentialsException(UnauthorizedException):
    """401 Invalid Credentials - constant message to avoid leaking user existence."""

    def __init__(self, message: str = "Invalid email or password."):
        super().__init__(message=message, code="INVALID_CREDENTIALS")


class AccountLockedException(UnauthorizedException):
    """401 Account Locked due to excessive failed attempts."""

    def __init__(self, message: str = "Account is temporarily locked. Please try again later."):
        super().__init__(message=message, code="ACCOUNT_LOCKED")


class TokenReuseException(UnauthorizedException):
    """401 Refresh Token Reuse Detected."""

    def __init__(self, message: str = "Invalid or reused refresh token."):
        super().__init__(message=message, code="TOKEN_REUSE_DETECTED")


class ForbiddenException(AppException):
    """403 Forbidden."""

    def __init__(
        self,
        message: str = "You do not have permission to perform this action.",
        code: str = "FORBIDDEN",
        details: Optional[List[Dict[str, Any]]] = None,
    ):
        super().__init__(message, code=code, status_code=status.HTTP_403_FORBIDDEN, details=details)


class AccountInactiveException(ForbiddenException):
    """403 Inactive account."""

    def __init__(self, message: str = "Account is inactive."):
        super().__init__(message=message, code="ACCOUNT_INACTIVE")


class BusinessInactiveException(ForbiddenException):
    """403 Inactive business."""

    def __init__(self, message: str = "Business is inactive."):
        super().__init__(message=message, code="BUSINESS_INACTIVE")


class NotFoundException(AppException):
    """404 Not Found."""

    def __init__(
        self,
        message: str = "Resource not found",
        code: str = "NOT_FOUND",
        details: Optional[List[Dict[str, Any]]] = None,
    ):
        super().__init__(message, code=code, status_code=status.HTTP_404_NOT_FOUND, details=details)


class ConflictException(AppException):
    """409 Conflict."""

    def __init__(
        self,
        message: str = "Resource conflict",
        code: str = "CONFLICT",
        details: Optional[List[Dict[str, Any]]] = None,
    ):
        super().__init__(message, code=code, status_code=status.HTTP_409_CONFLICT, details=details)


class LastOwnerException(ConflictException):
    """409 Last active owner cannot be removed or demoted."""

    def __init__(self, message: str = "Cannot remove or demote the last active owner of a business."):
        super().__init__(message=message, code="LAST_OWNER_PROTECTED")


class UnprocessableEntityException(AppException):
    """422 Validation Error."""

    def __init__(
        self,
        message: str = "Unprocessable entity",
        code: str = "VALIDATION_ERROR",
        details: Optional[List[Dict[str, Any]]] = None,
    ):
        super().__init__(message, code=code, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, details=details)


class RateLimitException(AppException):
    """429 Rate Limit Exceeded."""

    def __init__(
        self,
        message: str = "Too many requests. Please slow down.",
        code: str = "RATE_LIMIT_EXCEEDED",
        details: Optional[List[Dict[str, Any]]] = None,
    ):
        super().__init__(message, code=code, status_code=status.HTTP_429_TOO_MANY_REQUESTS, details=details)


class ServiceUnavailableException(AppException):
    """503 Service Unavailable."""

    def __init__(
        self,
        message: str = "Service temporarily unavailable.",
        code: str = "SERVICE_UNAVAILABLE",
        details: Optional[List[Dict[str, Any]]] = None,
    ):
        super().__init__(message, code=code, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, details=details)


def create_error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    details: Optional[List[Dict[str, Any]]] = None,
) -> JSONResponse:
    """Build standardized error envelope."""
    payload = {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
        },
        "request_id": request_id,
    }
    return JSONResponse(
        status_code=status_code,
        content=payload,
        headers={"X-Request-ID": request_id},
    )


def register_error_handlers(app: FastAPI) -> None:
    """Registers exception handlers on the FastAPI application."""

    @app.exception_handler(AppException)
    async def handle_app_exception(request: Request, exc: AppException) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        if exc.status_code >= 500:
            logger.error(
                "application_error",
                code=exc.code,
                message=exc.message,
                status_code=exc.status_code,
                request_id=request_id,
            )
        else:
            logger.info(
                "client_error",
                code=exc.code,
                message=exc.message,
                status_code=exc.status_code,
                request_id=request_id,
            )
        return create_error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            request_id=request_id,
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        details = []
        for err in exc.errors():
            loc = [str(x) for x in err.get("loc", []) if x != "body"]
            field = ".".join(loc) if loc else "body"
            details.append({
                "field": field,
                "message": err.get("msg", "Invalid value"),
                "type": err.get("type", "value_error"),
            })
        return create_error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="VALIDATION_ERROR",
            message="Request validation failed.",
            request_id=request_id,
            details=details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        code_map = {
            400: "BAD_REQUEST",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            409: "CONFLICT",
            422: "VALIDATION_ERROR",
            429: "RATE_LIMIT_EXCEEDED",
            500: "INTERNAL_SERVER_ERROR",
            503: "SERVICE_UNAVAILABLE",
        }
        code = code_map.get(exc.status_code, f"HTTP_{exc.status_code}")
        return create_error_response(
            status_code=exc.status_code,
            code=code,
            message=str(exc.detail),
            request_id=request_id,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        # Never leak stack traces, SQL, or internals to the client
        logger.exception("unhandled_server_exception", error=str(exc), request_id=request_id)
        return create_error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected internal error occurred.",
            request_id=request_id,
        )
