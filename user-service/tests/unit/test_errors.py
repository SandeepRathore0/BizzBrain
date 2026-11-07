"""Unit tests for domain exceptions and error response builder."""

from app.core.errors import (
    AppException,
    BadRequestException,
    ConflictException,
    ForbiddenException,
    InvalidCredentialsException,
    LastOwnerException,
    NotFoundException,
    TokenReuseException,
    create_error_response,
)


def test_app_exception_attributes():
    exc = AppException("Custom error", code="CUSTOM_CODE", status_code=418)
    assert exc.message == "Custom error"
    assert exc.code == "CUSTOM_CODE"
    assert exc.status_code == 418


def test_specific_exceptions():
    assert BadRequestException().status_code == 400
    assert InvalidCredentialsException().status_code == 401
    assert TokenReuseException().code == "TOKEN_REUSE_DETECTED"
    assert ForbiddenException().status_code == 403
    assert NotFoundException().status_code == 404
    assert ConflictException().status_code == 409
    assert LastOwnerException().code == "LAST_OWNER_PROTECTED"


def test_create_error_response_structure():
    resp = create_error_response(
        status_code=400,
        code="INVALID_HEADER",
        message="Missing X-Business-ID header",
        request_id="req-12345",
        details=[{"field": "X-Business-ID", "message": "Required"}],
    )
    assert resp.status_code == 400
    assert resp.headers["X-Request-ID"] == "req-12345"

    import json
    body = json.loads(resp.body.decode("utf-8"))
    assert body["success"] is False
    assert body["request_id"] == "req-12345"
    assert body["error"]["code"] == "INVALID_HEADER"
    assert body["error"]["message"] == "Missing X-Business-ID header"
    assert len(body["error"]["details"]) == 1
