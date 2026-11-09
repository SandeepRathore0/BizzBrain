import time
import uuid
import pytest
import jwt
from app.core.auth import verify_jwt_token
from app.core.errors import UnauthorizedException
from tests.test_keys import create_test_jwt, TEST_PRIVATE_KEY


@pytest.mark.asyncio
async def test_valid_token_verified():
    user_id = uuid.uuid4()
    token = create_test_jwt(user_id=user_id, memberships={str(uuid.uuid4()): "OWNER"})
    payload = await verify_jwt_token(token)
    assert payload["sub"] == str(user_id)


@pytest.mark.asyncio
async def test_expired_token_rejected():
    user_id = uuid.uuid4()
    token = create_test_jwt(user_id=user_id, memberships={}, expires_in=-10)
    with pytest.raises(UnauthorizedException) as exc:
        await verify_jwt_token(token)
    assert exc.value.code == "TOKEN_EXPIRED"


@pytest.mark.asyncio
async def test_tampered_token_rejected():
    user_id = uuid.uuid4()
    token = create_test_jwt(user_id=user_id, memberships={})
    tampered = token[:-4] + "abcd"
    with pytest.raises(UnauthorizedException) as exc:
        await verify_jwt_token(tampered)
    assert exc.value.code == "INVALID_TOKEN"


@pytest.mark.asyncio
async def test_wrong_issuer_rejected():
    user_id = uuid.uuid4()
    token = create_test_jwt(user_id=user_id, memberships={}, issuer="wrong-issuer")
    with pytest.raises(UnauthorizedException) as exc:
        await verify_jwt_token(token)
    assert exc.value.code == "INVALID_ISSUER"


@pytest.mark.asyncio
async def test_wrong_audience_rejected():
    user_id = uuid.uuid4()
    token = create_test_jwt(user_id=user_id, memberships={}, audience="wrong-aud")
    with pytest.raises(UnauthorizedException) as exc:
        await verify_jwt_token(token)
    assert exc.value.code == "INVALID_AUDIENCE"


@pytest.mark.asyncio
async def test_alg_none_rejected():
    user_id = uuid.uuid4()
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + 600,
        "iss": "bizbrain-user-service",
        "aud": "bizbrain-services",
        "memberships": {},
    }
    token = jwt.encode(payload, key="", algorithm="none")
    with pytest.raises(UnauthorizedException) as exc:
        await verify_jwt_token(token)
    assert exc.value.code == "UNSUPPORTED_ALGORITHM"


@pytest.mark.asyncio
async def test_unknown_kid_rejected():
    user_id = uuid.uuid4()
    token = create_test_jwt(user_id=user_id, memberships={}, kid="unknown-kid")
    with pytest.raises(UnauthorizedException) as exc:
        await verify_jwt_token(token)
    assert exc.value.code == "UNKNOWN_KID"
