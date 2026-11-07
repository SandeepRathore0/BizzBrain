"""Unit tests for password hashing, tokens, email normalization, and JWKS."""

import uuid
import jwt
from app.core.security import (
    create_access_token,
    create_service_token,
    decode_jwt_token,
    generate_opaque_token,
    get_jwks,
    hash_password,
    hash_token,
    normalize_email,
    verify_password,
    verify_password_dummy,
)


def test_normalize_email():
    assert normalize_email("  User@Example.COM  ") == "user@example.com"
    assert normalize_email("John.Doe@DOMAIN.org") == "john.doe@domain.org"


def test_argon2_password_hashing():
    raw_password = "SuperSecretPassword123!"
    hashed = hash_password(raw_password)

    assert hashed != raw_password
    assert hashed.startswith("$argon2id$")
    assert verify_password(raw_password, hashed) is True
    assert verify_password("WrongPassword123!", hashed) is False


def test_verify_password_dummy():
    # Should always return False without throwing exceptions
    assert verify_password_dummy("some_password") is False


def test_opaque_token_and_sha256_hash():
    token = generate_opaque_token(32)
    assert len(token) >= 32

    token_hash = hash_token(token)
    assert len(token_hash) == 64
    assert isinstance(token_hash, str)

    # Identical input produces identical hash
    assert hash_token(token) == token_hash
    # Different token produces different hash
    assert hash_token(generate_opaque_token(32)) != token_hash


def test_access_token_creation_and_verification():
    user_id = str(uuid.uuid4())
    biz_id = str(uuid.uuid4())
    memberships = {biz_id: "OWNER"}

    token = create_access_token(user_id=user_id, memberships=memberships)
    assert isinstance(token, str)

    # Decode and check claims
    claims = decode_jwt_token(token)
    assert claims["sub"] == user_id
    assert claims["memberships"] == memberships
    assert claims["iss"] == "bizbrain-user-service"
    assert claims["aud"] == "bizbrain-services"
    assert "jti" in claims
    assert "exp" in claims
    assert "iat" in claims

    # Inspect unverified header for 'kid' and 'alg'
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256"
    assert "kid" in header


def test_service_token_creation_and_verification():
    client_id = "invoice-service"
    token = create_service_token(client_id=client_id)
    assert isinstance(token, str)

    claims = decode_jwt_token(token)
    assert claims["sub"] == f"service:{client_id}"
    assert claims["client_id"] == client_id
    assert claims["type"] == "service_token"


def test_jwks_generation():
    jwks = get_jwks()
    assert "keys" in jwks
    assert len(jwks["keys"]) == 1

    key = jwks["keys"][0]
    assert key["kty"] == "RSA"
    assert key["use"] == "sig"
    assert key["alg"] == "RS256"
    assert "n" in key
    assert "e" in key
    assert "kid" in key
