"""Token security test script."""

import os
import sys
import uuid
import jwt
import base64
import json

# Add user-service to sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "user-service"))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    decode_jwt_token,
)

settings = get_settings()

def test_token_attacks():
    user_id = str(uuid.uuid4())
    biz_id = str(uuid.uuid4())
    memberships = {biz_id: "OWNER"}
    valid_token = create_access_token(user_id, memberships)

    # 1. Expired token
    expired_token = create_access_token(user_id, memberships, expires_minutes=-10)
    try:
        decode_jwt_token(expired_token)
        print("[FAIL] Expired token was accepted!")
    except jwt.ExpiredSignatureError:
        print("[PASS] Expired token rejected: ExpiredSignatureError")

    # 2. Tampered signature
    tampered_sig = valid_token[:-10] + "abcdefghij"
    try:
        decode_jwt_token(tampered_sig)
        print("[FAIL] Tampered signature was accepted!")
    except jwt.InvalidSignatureError:
        print("[PASS] Tampered signature rejected: InvalidSignatureError")

    # 3. Tampered payload
    header, payload, sig = valid_token.split(".")
    padded = payload + "=" * ((4 - len(payload) % 4) % 4)
    data = json.loads(base64.urlsafe_b64decode(padded))
    data["sub"] = str(uuid.uuid4())
    new_payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    tampered_payload_token = f"{header}.{new_payload}.{sig}"
    try:
        decode_jwt_token(tampered_payload_token)
        print("[FAIL] Tampered payload was accepted!")
    except (jwt.InvalidSignatureError, jwt.DecodeError):
        print("[PASS] Tampered payload rejected: InvalidSignatureError")

    # 4. alg: none attack
    header_none = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).decode().rstrip("=")
    none_token = f"{header_none}.{payload}."
    try:
        decode_jwt_token(none_token)
        print("[FAIL] alg: none was accepted!")
    except (jwt.InvalidAlgorithmError, jwt.DecodeError, jwt.InvalidSignatureError):
        print("[PASS] alg: none rejected")

    # 5. Algorithm confusion (HS256 with public key bytes)
    public_pem_bytes = settings.get_public_key_pem().encode()
    # Force HS256 with HMAC secret
    hs256_token = jwt.encode(data, key="secret_key_123", algorithm="HS256")
    try:
        decode_jwt_token(hs256_token)
        print("[FAIL] HS256 algorithm confusion was accepted!")
    except jwt.InvalidAlgorithmError:
        print("[PASS] HS256 algorithm confusion rejected: InvalidAlgorithmError (only RS256 allowed)")

    # 6. Wrong audience
    try:
        decode_jwt_token(valid_token, audience="wrong-audience")
        print("[FAIL] Wrong audience accepted!")
    except jwt.InvalidAudienceError:
        print("[PASS] Wrong audience rejected: InvalidAudienceError")

    # 7. Wrong issuer
    data_bad_iss = dict(data)
    data_bad_iss["iss"] = "forged-issuer"
    private_key = settings.get_private_key_pem()
    bad_iss_token = jwt.encode(data_bad_iss, private_key, algorithm="RS256", headers={"kid": settings.JWT_KEY_ID})
    try:
        decode_jwt_token(bad_iss_token)
        print("[FAIL] Wrong issuer accepted!")
    except jwt.InvalidIssuerError:
        print("[PASS] Wrong issuer rejected: InvalidIssuerError")

if __name__ == "__main__":
    test_token_attacks()
