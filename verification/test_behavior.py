"""Behavioral test script for auditing User Service implementation."""

import asyncio
import os
import sys
import uuid
import time
from datetime import datetime, timezone
import httpx
from httpx import ASGITransport, AsyncClient

# Add user-service to sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "user-service"))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.main import app
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_service_token,
    decode_jwt_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    normalize_email,
    verify_password,
    verify_password_dummy,
    get_jwks,
)
from app.core.permissions import has_permission, can_manage_member, Role, Permission

results = {}

async def run_audit():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        print("=== STARTING BEHAVIORAL AUDIT ===")

        # -------------------------------------------------------------
        # 1. Check Public Auth Endpoints
        # -------------------------------------------------------------
        print("\n--- 1. Testing Public Auth Endpoints ---")
        endpoints_to_check = [
            ("POST", "/api/v1/auth/register", {"email": "test@bizbrain.com", "password": "Password12345!", "full_name": "Test User"}),
            ("POST", "/api/v1/auth/login", {"email": "test@bizbrain.com", "password": "Password12345!"}),
            ("POST", "/api/v1/auth/refresh", {"refresh_token": "some_token"}),
            ("POST", "/api/v1/auth/logout", {}),
            ("POST", "/api/v1/auth/logout-all", {}),
            ("POST", "/api/v1/auth/verify-email", {"token": "some_token"}),
            ("POST", "/api/v1/auth/password-reset/request", {"email": "test@bizbrain.com"}),
            ("POST", "/api/v1/auth/password-reset/confirm", {"token": "some_token", "new_password": "NewPassword12345!"}),
            ("GET", "/api/v1/users/me", None),
            ("PUT", "/api/v1/users/me", {"full_name": "Updated Name"}),
            ("POST", "/api/v1/businesses", {"business_name": "BizCorp", "base_currency": "USD", "timezone": "UTC"}),
            ("GET", "/api/v1/businesses", None),
            ("GET", f"/api/v1/businesses/{uuid.uuid4()}", None),
            ("PATCH", f"/api/v1/businesses/{uuid.uuid4()}", {"business_name": "New Name"}),
            ("DELETE", f"/api/v1/businesses/{uuid.uuid4()}", None),
            ("GET", f"/api/v1/businesses/{uuid.uuid4()}/members", None),
            ("POST", f"/api/v1/businesses/{uuid.uuid4()}/members", {"user_id": str(uuid.uuid4()), "role": "EMPLOYEE"}),
            ("PATCH", f"/api/v1/businesses/{uuid.uuid4()}/members/{uuid.uuid4()}", {"role": "ADMIN"}),
            ("GET", "/.well-known/jwks.json", None),
            # Internal
            ("POST", "/internal/v1/service-token", {"client_id": "invoice-service", "client_secret": "secret"}),
            ("POST", "/internal/v1/authz/check", {"user_id": str(uuid.uuid4()), "business_id": str(uuid.uuid4()), "permission": "dashboard.view"}),
            ("GET", f"/internal/v1/businesses/{uuid.uuid4()}", None),
        ]

        endpoint_status = {}
        for method, path, payload in endpoints_to_check:
            try:
                if method == "POST":
                    resp = await client.post(path, json=payload)
                elif method == "GET":
                    resp = await client.get(path)
                elif method == "PUT":
                    resp = await client.put(path, json=payload)
                elif method == "PATCH":
                    resp = await client.patch(path, json=payload)
                elif method == "DELETE":
                    resp = await client.delete(path)
                endpoint_status[f"{method} {path}"] = (resp.status_code, resp.json())
            except Exception as e:
                endpoint_status[f"{method} {path}"] = ("ERROR", str(e))

        for ep, (status_code, body) in endpoint_status.items():
            print(f"[{status_code}] {ep}")

        # -------------------------------------------------------------
        # 2. Check Security Functions & Argon2id & Timing
        # -------------------------------------------------------------
        print("\n--- 2. Testing Password Hashing & Unknown Email Timing ---")
        pw = "SuperSecretPassword123!"
        hashed = hash_password(pw)
        pw_is_argon2id = hashed.startswith("$argon2id$")
        pw_verifies = verify_password(pw, hashed)
        pw_rejects_wrong = not verify_password("WrongPassword123!", hashed)

        print(f"Argon2id prefix check: {pw_is_argon2id}")
        print(f"Argon2id verify correct: {pw_verifies}")
        print(f"Argon2id reject wrong: {pw_rejects_wrong}")

        # Measure 20 timings of dummy vs real verify
        dummy_times = []
        real_times = []
        for _ in range(20):
            t0 = time.perf_counter()
            verify_password_dummy("AnyPassword123!")
            dummy_times.append(time.perf_counter() - t0)

            t0 = time.perf_counter()
            verify_password("WrongPassword123!", hashed)
            real_times.append(time.perf_counter() - t0)

        avg_dummy = sum(dummy_times) / len(dummy_times)
        avg_real = sum(real_times) / len(real_times)
        print(f"Avg dummy verify timing: {avg_dummy*1000:.2f}ms")
        print(f"Avg real verify timing:  {avg_real*1000:.2f}ms")
        timing_diff = abs(avg_dummy - avg_real) / max(avg_dummy, avg_real)
        print(f"Relative timing difference: {timing_diff*100:.2f}%")

        # -------------------------------------------------------------
        # 3. Token RS256 Verification & Claims
        # -------------------------------------------------------------
        print("\n--- 3. Testing Token Creation & Verification ---")
        user_id = str(uuid.uuid4())
        biz_id = str(uuid.uuid4())
        memberships = {biz_id: "OWNER"}
        access_tok = create_access_token(user_id=user_id, memberships=memberships)
        claims = decode_jwt_token(access_tok)
        print("Decoded claims:", claims)

        # -------------------------------------------------------------
        # 4. JWKS Public Key Endpoint
        # -------------------------------------------------------------
        print("\n--- 4. Testing JWKS Endpoint ---")
        jwks_resp = await client.get("/.well-known/jwks.json")
        print("JWKS status:", jwks_resp.status_code)
        jwks_data = jwks_resp.json()
        print("JWKS keys count:", len(jwks_data.get("keys", [])))
        key_0 = jwks_data.get("keys", [])[0] if jwks_data.get("keys") else {}
        print("Key attributes:", {k: key_0.get(k) for k in ["kty", "use", "alg", "kid"]})
        has_private_key = any("d" in k or "p" in k or "q" in k for k in jwks_data.get("keys", []))
        print("Has private key in JWKS:", has_private_key)

        # -------------------------------------------------------------
        # 5. Envelope & Request ID Propagation
        # -------------------------------------------------------------
        print("\n--- 5. Testing Envelopes & Request ID ---")
        custom_req_id = "audit-req-id-77777"
        hlth = await client.get("/health", headers={"X-Request-ID": custom_req_id})
        hlth_json = hlth.json()
        print("Health response:", hlth_json)
        print("Header X-Request-ID:", hlth.headers.get("X-Request-ID"))

        err_resp = await client.get("/non-existent-route")
        err_json = err_resp.json()
        print("404 error envelope:", err_json)

if __name__ == "__main__":
    asyncio.run(run_audit())
