import time
import uuid
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from unittest.mock import AsyncMock, patch
from app.core.auth import verify_user_token, verify_service_token, jwks_cache
from app.core.errors import UnauthorizedException


@pytest.fixture(scope="module")
def rsa_key_pair():
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    public_key = private_key.public_key()
    return private_key, public_key


@pytest.mark.asyncio
async def test_verify_user_token_valid_rs256(rsa_key_pair):
    private_key, public_key = rsa_key_pair
    user_id = str(uuid.uuid4())
    bus_id = str(uuid.uuid4())
    kid = "test-key-1"

    token = jwt.encode(
        {
            "sub": user_id,
            "aud": "bizbrain",
            "memberships": {bus_id: "OWNER"},
            "exp": int(time.time()) + 600,
            "iat": int(time.time()),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )

    mock_pyjwk = AsyncMock()
    mock_pyjwk.key = public_key

    with patch.object(jwks_cache, "get_signing_key", new=AsyncMock(return_value=mock_pyjwk)):
        payload = await verify_user_token(token)
        assert payload.sub == user_id
        assert payload.memberships[bus_id] == "OWNER"


@pytest.mark.asyncio
async def test_verify_user_token_rejects_alg_none(rsa_key_pair):
    user_id = str(uuid.uuid4())

    token = jwt.encode(
        {"sub": user_id, "exp": int(time.time()) + 600},
        key="",
        algorithm="none",
        headers={"kid": "key-none"},
    )

    with pytest.raises(UnauthorizedException) as exc_info:
        await verify_user_token(token)
    assert "Unsupported algorithm 'none'" in str(exc_info.value.message)


@pytest.mark.asyncio
async def test_verify_user_token_rejects_hs256():
    user_id = str(uuid.uuid4())

    token = jwt.encode(
        {"sub": user_id, "exp": int(time.time()) + 600},
        key="super-secret-key-1234567890123456",
        algorithm="HS256",
        headers={"kid": "key-hs"},
    )

    with pytest.raises(UnauthorizedException) as exc_info:
        await verify_user_token(token)
    assert "Unsupported algorithm 'HS256'" in str(exc_info.value.message)


@pytest.mark.asyncio
async def test_verify_user_token_rejects_missing_kid(rsa_key_pair):
    private_key, _ = rsa_key_pair
    user_id = str(uuid.uuid4())

    token = jwt.encode(
        {"sub": user_id, "exp": int(time.time()) + 600},
        private_key,
        algorithm="RS256",
    )

    with pytest.raises(UnauthorizedException) as exc_info:
        await verify_user_token(token)
    assert "missing 'kid'" in str(exc_info.value.message).lower()


@pytest.mark.asyncio
async def test_verify_user_token_rejects_expired(rsa_key_pair):
    private_key, public_key = rsa_key_pair
    user_id = str(uuid.uuid4())
    kid = "test-key-exp"

    token = jwt.encode(
        {
            "sub": user_id,
            "exp": int(time.time()) - 100,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )

    mock_pyjwk = AsyncMock()
    mock_pyjwk.key = public_key

    with patch.object(jwks_cache, "get_signing_key", new=AsyncMock(return_value=mock_pyjwk)):
        with pytest.raises(UnauthorizedException) as exc_info:
            await verify_user_token(token)
        assert "expired" in str(exc_info.value.message).lower()


@pytest.mark.asyncio
async def test_verify_service_token_audience_mismatch(rsa_key_pair):
    private_key, public_key = rsa_key_pair
    kid = "test-key-srv"

    token = jwt.encode(
        {
            "sub": "invoice-service",
            "aud": "wrong-service",
            "exp": int(time.time()) + 300,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )

    mock_pyjwk = AsyncMock()
    mock_pyjwk.key = public_key

    with patch.object(jwks_cache, "get_signing_key", new=AsyncMock(return_value=mock_pyjwk)):
        with pytest.raises(UnauthorizedException) as exc_info:
            await verify_service_token(token, expected_audience="financial-data-service")
        assert "audience" in str(exc_info.value.message).lower()
