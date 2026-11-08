from typing import Any, List, Optional
from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import structlog

logger = structlog.get_logger()


class AppException(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[List[Any]] = None,
    ):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []
        super().__init__(message)


class NotFoundException(AppException):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            code="NOT_FOUND",
            message=message,
        )


class UnauthorizedException(AppException):
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="UNAUTHORIZED",
            message=message,
        )


class ForbiddenException(AppException):
    def __init__(self, message: str = "Forbidden"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN",
            message=message,
        )


class ConflictException(AppException):
    def __init__(self, code: str = "CONFLICT", message: str = "Conflict"):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            code=code,
            message=message,
        )


class ValidationException(AppException):
    def __init__(self, code: str = "VALIDATION_ERROR", message: str = "Validation failed", details: Optional[List[Any]] = None):
        super().__init__(
            status_code=422,
            code=code,
            message=message,
            details=details,
        )


class BadRequestException(AppException):
    def __init__(self, code: str = "BAD_REQUEST", message: str = "Bad request", details: Optional[List[Any]] = None):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=code,
            message=message,
            details=details,
        )


class ServiceUnavailableException(AppException):
    def __init__(self, message: str = "Service unavailable"):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="SERVICE_UNAVAILABLE",
            message=message,
        )


def create_error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    details: Optional[List[Any]] = None,
) -> JSONResponse:
    content = {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
        },
        "request_id": request_id,
    }
    return JSONResponse(status_code=status_code, content=content)


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    return create_error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        request_id=request_id,
        details=exc.details,
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    details = []
    for error in exc.errors():
        loc = ".".join(str(l) for l in error.get("loc", []))
        details.append({
            "field": loc,
            "message": error.get("msg", ""),
            "type": error.get("type", ""),
        })
    return create_error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="VALIDATION_ERROR",
        message="Request validation failed",
        request_id=request_id,
        details=details,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error("unhandled_server_error", exc_info=str(exc), request_id=request_id)
    return create_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_SERVER_ERROR",
        message="An internal server error occurred",
        request_id=request_id,
        details=[],
    )
