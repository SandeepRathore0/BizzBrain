from typing import Any, Optional
from fastapi import Request
from fastapi.responses import JSONResponse


class APIException(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[Any] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


# Specialized Exception Classes
class BadRequestException(APIException):
    def __init__(self, code: str = "BAD_REQUEST", message: str = "Bad Request", details: Optional[Any] = None):
        super().__init__(400, code, message, details)


class UnauthorizedException(APIException):
    def __init__(self, code: str = "UNAUTHORIZED", message: str = "Unauthorized", details: Optional[Any] = None):
        super().__init__(401, code, message, details)


class ForbiddenException(APIException):
    def __init__(self, code: str = "FORBIDDEN", message: str = "Forbidden", details: Optional[Any] = None):
        super().__init__(403, code, message, details)


class NotFoundException(APIException):
    def __init__(self, code: str = "NOT_FOUND", message: str = "Resource Not Found", details: Optional[Any] = None):
        super().__init__(404, code, message, details)


class ConflictException(APIException):
    def __init__(self, code: str = "CONFLICT", message: str = "Conflict", details: Optional[Any] = None):
        super().__init__(409, code, message, details)


class UnprocessableEntityException(APIException):
    def __init__(self, code: str = "UNPROCESSABLE_ENTITY", message: str = "Unprocessable Entity", details: Optional[Any] = None):
        super().__init__(422, code, message, details)


class RateLimitExceededException(APIException):
    def __init__(self, code: str = "RATE_LIMIT_EXCEEDED", message: str = "Rate limit exceeded. Please try again later.", details: Optional[Any] = None):
        super().__init__(429, code, message, details)


class InternalServerErrorException(APIException):
    def __init__(self, code: str = "INTERNAL_SERVER_ERROR", message: str = "An unexpected error occurred", details: Optional[Any] = None):
        super().__init__(500, code, message, details)


class BadGatewayException(APIException):
    def __init__(self, code: str = "BAD_GATEWAY", message: str = "Provider gateway error", details: Optional[Any] = None):
        super().__init__(502, code, message, details)


class ServiceUnavailableException(APIException):
    def __init__(self, code: str = "SERVICE_UNAVAILABLE", message: str = "Upstream service unavailable", details: Optional[Any] = None):
        super().__init__(503, code, message, details)


def create_error_response(status_code: int, code: str, message: str, details: Optional[Any] = None, request_id: Optional[str] = None) -> JSONResponse:
    content = {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "details": details,
        },
        "request_id": request_id or "",
    }
    return JSONResponse(status_code=status_code, content=content)
