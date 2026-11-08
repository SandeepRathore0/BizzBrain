from typing import Any, List, Optional
from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.core.logging import logger, request_id_ctx


class AppException(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        details: Optional[List[Any]] = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []
        super().__init__(message)


class BadRequestException(AppException):
    def __init__(self, message: str, code: str = "BAD_REQUEST", details: Optional[List[Any]] = None):
        super().__init__(code=code, message=message, status_code=status.HTTP_400_BAD_REQUEST, details=details)


class UnauthorizedException(AppException):
    def __init__(self, message: str = "Authentication required", code: str = "UNAUTHORIZED"):
        super().__init__(code=code, message=message, status_code=status.HTTP_401_UNAUTHORIZED)


class ForbiddenException(AppException):
    def __init__(self, message: str = "Access forbidden", code: str = "FORBIDDEN"):
        super().__init__(code=code, message=message, status_code=status.HTTP_403_FORBIDDEN)


class NotFoundException(AppException):
    def __init__(self, message: str = "Resource not found", code: str = "NOT_FOUND"):
        super().__init__(code=code, message=message, status_code=status.HTTP_404_NOT_FOUND)


class ConflictException(AppException):
    def __init__(self, message: str, code: str = "CONFLICT", details: Optional[List[Any]] = None):
        super().__init__(code=code, message=message, status_code=status.HTTP_409_CONFLICT, details=details)


class InvalidStateTransitionException(ConflictException):
    def __init__(self, message: str = "Invalid invoice state transition"):
        super().__init__(message=message, code="INVALID_STATE_TRANSITION")


class InvoiceHasPaymentsException(ConflictException):
    def __init__(self, message: str = "Cannot void invoice with existing payments"):
        super().__init__(message=message, code="INVOICE_HAS_PAYMENTS")


class ValidationException(AppException):
    def __init__(self, message: str = "Validation failed", code: str = "VALIDATION_ERROR", details: Optional[List[Any]] = None):
        super().__init__(code=code, message=message, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, details=details)


class FieldNotEditableException(ValidationException):
    def __init__(self, message: str = "Field is not editable"):
        super().__init__(message=message, code="FIELD_NOT_EDITABLE")


class IdempotencyException(AppException):
    def __init__(self, message: str = "Idempotency key reused with different request payload"):
        super().__init__(code="IDEMPOTENCY_KEY_REUSED", message=message, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)


class DependencyException(AppException):
    def __init__(self, message: str = "Downstream service unavailable"):
        super().__init__(code="SERVICE_UNAVAILABLE", message=message, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    req_id = request_id_ctx.get() or request.headers.get("X-Request-ID", "unknown")
    logger.warning(
        "app_exception",
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
        details=exc.details,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
            "request_id": req_id,
        },
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    req_id = request_id_ctx.get() or request.headers.get("X-Request-ID", "unknown")
    details = []
    for err in exc.errors():
        details.append({
            "field": ".".join(str(loc) for loc in err.get("loc", [])),
            "message": err.get("msg", "Invalid value"),
            "type": err.get("type", "value_error"),
        })
    logger.warning("validation_error", details=details)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "success": False,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": details,
            },
            "request_id": req_id,
        },
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    req_id = request_id_ctx.get() or request.headers.get("X-Request-ID", "unknown")
    # Log the full exception internally, but NEVER expose internal stack trace or SQL to client
    logger.error("unhandled_exception", error=str(exc), exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An internal server error occurred",
                "details": [],
            },
            "request_id": req_id,
        },
    )
