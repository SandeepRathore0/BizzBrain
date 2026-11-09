from uuid import uuid4
import pytest
from starlette.requests import Request
from app.core.auth import get_current_user, get_service_token, require_internal_caller
from app.core.errors import (
    BadRequestException,
    ForbiddenException,
    UnauthorizedException,
)
from tests.jwt_test_helpers import (
    create_test_jwt,
    create_test_service_token,
    register_test_jwt_keys,
)

register_test_jwt_keys()


def _make_request(auth_header: str = None, headers: dict = None) -> Request:
    raw_headers = []
    if auth_header:
        raw_headers.append((b"authorization", auth_header.encode("latin-1")))
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/invoices",
        "headers": raw_headers,
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_auth_single_membership_defaults_business_id():
    user_id = uuid4()
    biz_id = str(uuid4())
    token = create_test_jwt(user_id=user_id, memberships={biz_id: "OWNER"})
    req = _make_request(auth_header=f"Bearer {token}")

    user_ctx = await get_current_user(req, x_business_id=None)
    assert user_ctx.user_id == user_id
    assert str(user_ctx.business_id) == biz_id
    assert user_ctx.role == "OWNER"


@pytest.mark.asyncio
async def test_auth_multiple_memberships_requires_header():
    user_id = uuid4()
    biz1 = str(uuid4())
    biz2 = str(uuid4())
    token = create_test_jwt(user_id=user_id, memberships={biz1: "OWNER", biz2: "EMPLOYEE"})
    req = _make_request(auth_header=f"Bearer {token}")

    with pytest.raises(BadRequestException, match="X-Business-ID header is required"):
        await get_current_user(req, x_business_id=None)


@pytest.mark.asyncio
async def test_auth_multiple_memberships_with_valid_header():
    user_id = uuid4()
    biz1 = str(uuid4())
    biz2 = str(uuid4())
    token = create_test_jwt(user_id=user_id, memberships={biz1: "OWNER", biz2: "EMPLOYEE"})
    req = _make_request(auth_header=f"Bearer {token}", headers={"X-Business-ID": biz2})

    user_ctx = await get_current_user(req, x_business_id=biz2)
    assert user_ctx.user_id == user_id
    assert str(user_ctx.business_id) == biz2
    assert user_ctx.role == "EMPLOYEE"


@pytest.mark.asyncio
async def test_auth_mismatched_business_id_forbidden():
    user_id = uuid4()
    biz1 = str(uuid4())
    other_biz = str(uuid4())
    token = create_test_jwt(user_id=user_id, memberships={biz1: "ADMIN"})
    req = _make_request(auth_header=f"Bearer {token}", headers={"X-Business-ID": other_biz})

    with pytest.raises(ForbiddenException, match="not an active member"):
        await get_current_user(req, x_business_id=other_biz)


@pytest.mark.asyncio
async def test_auth_malformed_business_id_header():
    token = create_test_jwt()
    req = _make_request(auth_header=f"Bearer {token}")
    with pytest.raises(BadRequestException, match="Must be UUID"):
        await get_current_user(req, x_business_id="not-a-uuid")


@pytest.mark.asyncio
async def test_auth_expired_token():
    token = create_test_jwt(expires_in_seconds=-10)
    req = _make_request(auth_header=f"Bearer {token}")
    with pytest.raises(UnauthorizedException, match="expired"):
        await get_current_user(req, x_business_id=None)


@pytest.mark.asyncio
async def test_auth_wrong_audience():
    token = create_test_jwt(audience="wrong-service")
    req = _make_request(auth_header=f"Bearer {token}")
    with pytest.raises(UnauthorizedException, match="audience is invalid"):
        await get_current_user(req, x_business_id=None)


@pytest.mark.asyncio
async def test_auth_wrong_issuer():
    token = create_test_jwt(issuer="fake-issuer")
    req = _make_request(auth_header=f"Bearer {token}")
    with pytest.raises(UnauthorizedException, match="issuer is invalid"):
        await get_current_user(req, x_business_id=None)


@pytest.mark.asyncio
async def test_auth_rejects_alg_none():
    token = create_test_jwt(algorithm="none")
    req = _make_request(auth_header=f"Bearer {token}")
    with pytest.raises(UnauthorizedException, match="Only RS256 is supported"):
        await get_current_user(req, x_business_id=None)


@pytest.mark.asyncio
async def test_auth_unknown_kid(monkeypatch):
    from app.core.jwks import jwks_client

    # Simulate JWKS server refreshing successfully with no matching kid
    async def mock_fetch_jwks():
        jwks_client._keys = {}
        jwks_client._last_fetched = 9999999999.0

    monkeypatch.setattr(jwks_client, "_fetch_jwks", mock_fetch_jwks)

    token = create_test_jwt(kid="non-existent-kid")
    req = _make_request(auth_header=f"Bearer {token}")
    with pytest.raises(UnauthorizedException, match="unknown signing key ID"):
        await get_current_user(req, x_business_id=None)


@pytest.mark.asyncio
async def test_auth_rejects_user_token_on_internal_route():
    token = create_test_jwt(audience="invoice-service")
    req = _make_request(auth_header=f"Bearer {token}")
    with pytest.raises(ForbiddenException, match="User tokens are not allowed on internal endpoints"):
        await get_service_token(req)


@pytest.mark.asyncio
async def test_auth_internal_service_token_succeeds():
    token = create_test_service_token(client_id="payment-service")
    req = _make_request(auth_header=f"Bearer {token}")
    svc_ctx = await get_service_token(req)
    assert svc_ctx.client_id == "payment-service"
    assert svc_ctx.caller_type == "SERVICE"


@pytest.mark.asyncio
async def test_auth_internal_caller_allowlist():
    token = create_test_service_token(client_id="unauthorized-service")
    req = _make_request(auth_header=f"Bearer {token}")
    svc_ctx = await get_service_token(req)

    dependency = require_internal_caller(allowed_callers=["payment-service"])
    with pytest.raises(ForbiddenException, match="not authorized for this internal endpoint"):
        await dependency(service_ctx=svc_ctx)
