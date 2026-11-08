import time
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import jwt
from app.core.config import settings
from app.core.jwks import jwks_client

# Generate a 2048-bit test RSA key pair for testing
_private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
)
_public_key = _private_key.public_key()

TEST_KID = "test-key-1"

# Convert keys to PEM
TEST_PRIVATE_KEY_PEM = _private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode("utf-8")

TEST_PUBLIC_KEY_PEM = _public_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode("utf-8")


def register_test_jwt_keys():
    """Register the test public key in jwks_client."""
    jwks_client.set_test_key(TEST_KID, _public_key)


def create_test_jwt(
    user_id: Optional[UUID] = None,
    memberships: Optional[Dict[str, str]] = None,
    issuer: str = settings.JWT_ISSUER,
    audience: str = settings.JWT_AUDIENCE,
    kid: str = TEST_KID,
    algorithm: str = "RS256",
    expires_in_seconds: int = 600,
    private_key: str = TEST_PRIVATE_KEY_PEM,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id or uuid4()),
        "jti": str(uuid4()),
        "iat": now,
        "exp": now + expires_in_seconds,
        "iss": issuer,
        "aud": audience,
        "memberships": memberships if memberships is not None else {str(uuid4()): "OWNER"},
    }
    if extra_claims:
        payload.update(extra_claims)

    headers = {"kid": kid, "alg": algorithm}
    if algorithm == "none":
        return jwt.encode(payload, key="", algorithm="none")
    return jwt.encode(payload, key=private_key, algorithm=algorithm, headers=headers)


def create_test_service_token(
    client_id: str = "payment-service",
    audience: str = settings.INTERNAL_JWT_AUDIENCE,
    issuer: str = settings.JWT_ISSUER,
    kid: str = TEST_KID,
    expires_in_seconds: int = 300,
) -> str:
    now = int(time.time())
    payload = {
        "sub": client_id,
        "client_id": client_id,
        "jti": str(uuid4()),
        "iat": now,
        "exp": now + expires_in_seconds,
        "iss": issuer,
        "aud": audience,
    }
    headers = {"kid": kid, "alg": "RS256"}
    return jwt.encode(payload, key=TEST_PRIVATE_KEY_PEM, algorithm="RS256", headers=headers)
