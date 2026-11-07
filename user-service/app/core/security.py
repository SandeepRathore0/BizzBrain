"""Security, cryptography, password hashing, and token issuance utilities."""

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import get_settings

# Argon2id password hasher with secure defaults
# Type 2 is Argon2id
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

# Pre-computed dummy argon2 hash for constant-time comparisons when email does not exist
# Prevents timing side-channel attacks on user enumeration
_DUMMY_ARGON2_HASH = _hasher.hash("dummy_constant_time_comparison_password_bizbrain_123")


def normalize_email(email: str) -> str:
    """Normalizes an email address (trims whitespace and converts to lowercase)."""
    return email.strip().lower()


def hash_password(password: str) -> str:
    """Hashes a password using Argon2id."""
    return _hasher.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against an Argon2id hash."""
    try:
        return _hasher.verify(hashed_password, plain_password)
    except (VerifyMismatchError, VerificationError):
        return False


def verify_password_dummy(plain_password: str) -> bool:
    """Performs dummy verification to achieve similar timing for non-existent users."""
    try:
        _hasher.verify(_DUMMY_ARGON2_HASH, plain_password)
    except Exception:
        pass
    return False


def generate_opaque_token(nbytes: int = 32) -> str:
    """Generates a secure cryptographically random 256-bit token (URL-safe string)."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Computes SHA-256 hash of an opaque token (returns 64-character lowercase hex string)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(
    user_id: str,
    memberships: Dict[str, str],
    expires_minutes: Optional[int] = None,
) -> str:
    """
    Creates an RS256-signed JWT access token.
    Claims: sub, jti, iat, exp, iss, aud, memberships (map of business_id -> role).
    Includes 'kid' in JWT header.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    duration = expires_minutes if expires_minutes is not None else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    exp = now.timestamp() + (duration * 60)

    claims: Dict[str, Any] = {
        "sub": str(user_id),
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(exp),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "memberships": memberships,  # Only ACTIVE memberships
    }

    headers = {
        "kid": settings.JWT_KEY_ID,
        "alg": "RS256",
        "typ": "JWT",
    }

    private_key = settings.get_private_key_pem()
    return jwt.encode(claims, private_key, algorithm="RS256", headers=headers)


def create_service_token(
    client_id: str,
    target_audience: Optional[str] = None,
    expires_minutes: Optional[int] = None,
) -> str:
    """
    Creates an RS256-signed JWT service token (Client Credentials flow, 5 min default expiry).
    Claims: sub, jti, iat, exp, iss, aud, client_id.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    duration = expires_minutes if expires_minutes is not None else settings.SERVICE_TOKEN_EXPIRE_MINUTES
    exp = now.timestamp() + (duration * 60)

    claims: Dict[str, Any] = {
        "sub": f"service:{client_id}",
        "client_id": client_id,
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(exp),
        "iss": settings.JWT_ISSUER,
        "aud": target_audience or settings.JWT_AUDIENCE,
        "type": "service_token",
    }

    headers = {
        "kid": settings.JWT_KEY_ID,
        "alg": "RS256",
        "typ": "JWT",
    }

    private_key = settings.get_private_key_pem()
    return jwt.encode(claims, private_key, algorithm="RS256", headers=headers)


def decode_jwt_token(
    token: str,
    audience: Optional[str] = None,
    verify_exp: bool = True,
) -> Dict[str, Any]:
    """Decodes and cryptographically verifies an RS256 JWT using the public key."""
    settings = get_settings()
    public_key = settings.get_public_key_pem()
    target_aud = audience or settings.JWT_AUDIENCE

    return jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        audience=target_aud,
        issuer=settings.JWT_ISSUER,
        options={"verify_exp": verify_exp},
    )


def int_to_base64url(val: int) -> str:
    """Converts a big integer to base64url-encoded string without padding."""
    byte_length = (val.bit_length() + 7) // 8
    val_bytes = val.to_bytes(byte_length, byteorder="big")
    return base64.urlsafe_b64encode(val_bytes).rstrip(b"=").decode("ascii")


def get_jwks() -> Dict[str, Any]:
    """Generates the JSON Web Key Set (JWKS) document for public key discovery."""
    settings = get_settings()
    public_pem = settings.get_public_key_pem()
    public_key = serialization.load_pem_public_key(
        public_pem.encode("utf-8"),
        backend=default_backend(),
    )

    if not isinstance(public_key, rsa.RSAPublicKey):
        raise ValueError("Public key must be an RSA public key")

    public_numbers = public_key.public_numbers()
    n = int_to_base64url(public_numbers.n)
    e = int_to_base64url(public_numbers.e)

    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": settings.JWT_KEY_ID,
        "n": n,
        "e": e,
    }

    return {"keys": [jwk]}
