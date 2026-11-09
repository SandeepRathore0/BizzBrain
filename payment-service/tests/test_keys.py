import time
import uuid
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
from jwt.algorithms import RSAAlgorithm

# Single source of truth for test RSA keys
TEST_PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
)
TEST_PUBLIC_KEY = TEST_PRIVATE_KEY.public_key()
TEST_KID = "bizbrain-test-key-1"


def get_test_jwks() -> dict:
    jwk = RSAAlgorithm.to_jwk(TEST_PUBLIC_KEY, as_dict=True)
    jwk["kid"] = TEST_KID
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return {"keys": [jwk]}


def create_test_jwt(
    user_id: uuid.UUID | str,
    memberships: dict[str, str],
    expires_in: int = 600,
    kid: str = TEST_KID,
    issuer: str = "bizbrain-user-service",
    audience: str = "bizbrain-services",
    algorithm: str = "RS256",
) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + expires_in,
        "iss": issuer,
        "aud": audience,
        "memberships": memberships,
    }
    headers = {"kid": kid, "alg": algorithm, "typ": "JWT"}
    return jwt.encode(payload, TEST_PRIVATE_KEY, algorithm=algorithm, headers=headers)
