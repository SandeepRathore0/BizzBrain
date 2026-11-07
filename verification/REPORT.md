# BizBrain User Service - Verification & Security Audit Report

**Auditor:** Senior Security-Minded Backend Reviewer  
**Date:** 2026-11-07  
**Repository:** `user-service`  
**Overall Verdict:** **NOT READY**

> **Verdict Summary:** The repository has only completed Phase 1 of development (project skeleton, SQLAlchemy models, initial Alembic migration, security primitives, error envelopes, and JWKS discovery), meaning all core business API endpoints (`/api/v1/auth/*`, `/api/v1/businesses/*`, `/api/v1/users/me`, `/internal/v1/*`), repositories, and business logic services are entirely missing (returning HTTP 404).

---

## 1. Specification Scorecard

| Rule / Requirement ID | Requirement Description | Status | Evidence / Notes |
| :--- | :--- | :--- | :--- |
| **US-01** | Public Auth Endpoints (`register`, `login`, `refresh`, `logout`, `logout-all`) | **FAIL** | All endpoints return HTTP 404 (`verification/test_behavior.py` lines 41-45). Routers do not exist. |
| **US-02** | Email Token Endpoints (`verify-email`, `password-reset/request`, `confirm`) | **FAIL** | All endpoints return HTTP 404 (`verification/test_behavior.py` lines 46-48). |
| **US-03** | User Profile Endpoints (`GET`, `PUT /api/v1/users/me`) | **FAIL** | Return HTTP 404 (`verification/test_behavior.py` lines 49-50). |
| **US-04** | Business Endpoints (`POST`, `GET`, `PATCH`, `DELETE /api/v1/businesses`) | **FAIL** | Return HTTP 404 (`verification/test_behavior.py` lines 51-55). |
| **US-05** | Membership Endpoints (`GET`, `POST /businesses/{id}/members`, `PATCH members/{user_id}`) | **FAIL** | Return HTTP 404 (`verification/test_behavior.py` lines 56-58). |
| **US-06** | Public JWKS Discovery (`GET /.well-known/jwks.json`) | **PASS** | `app/api/jwks.py`: returns 200, valid RS256 public key, `kid="bizbrain-user-key-1"`, no private keys. Tested via `tests/api/test_health_and_jwks.py`. |
| **US-07** | Internal Service Token (`POST /internal/v1/service-token`) | **FAIL** | Returns HTTP 404 (`verification/test_behavior.py` line 60). |
| **US-08** | Internal Authz Check (`POST /internal/v1/authz/check`) | **FAIL** | Returns HTTP 404 (`verification/test_behavior.py` line 61). |
| **US-09** | Internal Business Lookup (`GET /internal/v1/businesses/{id}`) | **FAIL** | Returns HTTP 404 (`verification/test_behavior.py` line 62). |
| **US-C01** | Tech Stack & Clean Layering | **PARTIAL** | Python 3.12, FastAPI, SQLAlchemy 2.0 async, Alembic, Argon2id, RS256 JWT, and structlog are setup. However, `app/services` and `app/repositories` contain only empty `__init__.py` files. |
| **US-C02** | Database Schema & UTC TIMESTAMPTZ | **PARTIAL** | All 6 tables created with UTC `TIMESTAMPTZ` in `migrations/versions/001_initial_schema.py`. However, `base_currency` and `token_hash` are `VARCHAR` instead of `CHAR(3)` and `CHAR(64)`. |
| **US-C03** | RS256 Access Token Issuance & Claims | **PASS** | `app/core/security.py`: Claims `sub`, `jti`, `iat`, `exp`, `iss`, `aud`, and `memberships` verified. Expired tokens, tampered signatures, `alg: none`, algorithm confusion, and bad audiences are rejected (`verification/test_tokens.py`). |
| **US-C04** | Refresh Token Family Rotation & Reuse Detection | **PARTIAL** | Data model and hashing primitives exist in `app/models/token.py` and `app/core/security.py`. Endpoint rotation and family revocation logic are not yet implemented. |
| **US-C05** | Account Lockout & Constant-Time Timing | **PARTIAL** | `app/core/security.py` has dummy Argon2 verification matching real verification within 0.18% (90.48ms vs 90.31ms). Lockout columns exist on `User` model, but login endpoint is not implemented. |
| **US-C06** | Tenancy Isolation (`X-Business-ID`) | **UNVERIFIED** | Endpoints are not implemented to enforce `X-Business-ID` validation against JWT memberships. |
| **US-C07** | Centralized Permission Policy | **PASS** | `app/core/permissions.py`: Full matrix for Owner, Admin, and Employee implemented and verified without scattered role checks (`tests/unit/test_permissions.py`). |
| **US-C08** | Transactional Last-Owner Invariant | **UNVERIFIED** | Model constraints exist (`check_business_member_role`), but transactional enforcement (`SELECT ... FOR UPDATE`) is not implemented in services/repositories. |
| **US-C09** | Currency & Timezone Validation | **PARTIAL** | ISO 4217 and IANA timezone validation implemented in `app/core/config.py`. Immutability on update cannot be verified as `PATCH /businesses/{id}` is not implemented. |
| **US-C10** | Append-Only `audit_log` Privileges | **FAIL** | `audit_log` table exists in `001_initial_schema.py`, but no SQL `REVOKE UPDATE, DELETE` is executed, and Docker configuration defaults to the `postgres` superuser. |

---

## 2. Test Suite & Coverage Analysis

### Test Execution Results
The existing automated test suite was executed **3 consecutive times** to detect flakiness:
- **Run 1:** 28 passed in 3.03s (0 failed, 0 flaky)
- **Run 2:** 28 passed in 1.48s (0 failed, 0 flaky)
- **Run 3:** 28 passed in 1.38s (0 failed, 0 flaky)

### Coverage by Layer
```text
Name                           Stmts   Miss  Cover   Missing
------------------------------------------------------------
app\api\__init__.py                0      0   100%
app\api\jwks.py                    6      0   100%
app\core\__init__.py               0      0   100%
app\core\config.py                53      4    92%   35-37, 73
app\core\database.py              34      9    74%   33-37, 49-62
app\core\errors.py                67     18    73%   24, 76, 83, 90, 104, 118, 125, 142, 169-178, 192, 203
app\core\logging.py               24      0   100%
app\core\middleware.py            24      0   100%
app\core\permissions.py           40      0   100%
app\core\security.py              74      1    99%   180
app\main.py                       37     10    73%   22-33, 91-93
app\models\__init__.py             7      0   100%
app\models\audit.py               21      0   100%
app\models\base.py                30      3    90%   20, 28, 37
app\models\business.py            15      0   100%
app\models\member.py              17      0   100%
app\models\token.py               29      0   100%
app\models\user.py                26      3    88%   93-95
app\repositories\__init__.py       0      0   100%   (0 files, 0% coverage)
app\schemas\__init__.py            0      0   100%
app\schemas\common.py             28     28     0%   3-50
app\services\__init__.py           0      0   100%   (0 files, 0% coverage)
------------------------------------------------------------
TOTAL                            573    114    80%
```

- **Target Coverage:** Spec mandates at least 85% on `services` and `repositories`. Currently, neither layer has any code, resulting in **0% coverage** for those layers.

---

## 3. Findings

### [CRITICAL] SEC-01: Public and Internal API Endpoints are Unimplemented
- **What is wrong:** 20 out of 21 specified endpoints are not registered or implemented in `app/main.py` or `app/api`.
- **Evidence:**
  `verification/test_behavior.py` lines 41–63 tested:
  - `POST /api/v1/auth/register` -> `404 Not Found`
  - `POST /api/v1/auth/login` -> `404 Not Found`
  - `POST /api/v1/auth/refresh` -> `404 Not Found`
  - `POST /api/v1/businesses` -> `404 Not Found`
  - `POST /internal/v1/service-token` -> `404 Not Found`
  - `POST /internal/v1/authz/check` -> `404 Not Found`
- **Impact:** The service cannot perform identity registration, authentication, business management, or authorization checks for peer microservices.
- **Recommended Fix:** Implement Stages 2, 3, and 4 (routers, services, and repositories) and mount them in `app/main.py`.

---

### [HIGH] SEC-02: Missing Application DB Role and Audit Log Modification Grants
- **What is wrong:** Spec requirement: *"The application DB role must have no UPDATE or DELETE grant on audit_log."*
- **Evidence:**
  - `user-service/migrations/versions/001_initial_schema.py` lines 102–120 creates `audit_log`, but contains no `REVOKE` or permission grant commands.
  - `user-service/docker-compose.yml` line 10 configures `POSTGRES_USER: postgres`, running the service as the database superuser.
- **Impact:** An attacker who achieves SQL injection or service compromise can tamper with or delete audit logs, defeating non-repudiation.
- **Recommended Fix:** Add a migration that creates a restricted application role (e.g. `bizbrain_app`), grants only `SELECT, INSERT` on `audit_log`, and configures `DATABASE_URL` to use this restricted user.

---

### [MEDIUM] SEC-03: Database Column Types Diverge from Exact Spec
- **What is wrong:** The spec requires `base_currency CHAR(3)` and `token_hash CHAR(64) UNIQUE`. Both are defined as `VARCHAR` in the migration and models.
- **Evidence:**
  - `user-service/migrations/versions/001_initial_schema.py` line 34: `sa.Column('base_currency', sa.String(length=3))` -> `VARCHAR(3)`.
  - `user-service/migrations/versions/001_initial_schema.py` line 67: `sa.Column('token_hash', sa.String(length=64))` -> `VARCHAR(64)`.
  - `app/models/business.py` line 31: `String(3)`.
  - `app/models/token.py` line 38: `String(64)`.
- **Impact:** While length-constrained in Postgres, fixed-length hashes and standard 3-letter currency codes should use fixed-width `CHAR(n)` for consistency with other microservice specifications.
- **Recommended Fix:** Alter column definitions in Alembic migration to `sa.CHAR(3)` and `sa.CHAR(64)`.

---

### [LOW] SEC-04: Test Suite Relies on In-Memory SQLite Instead of Postgres Containers
- **What is wrong:** Spec requires: *"Integration tests against a real Postgres... testcontainers (or a test Postgres via docker-compose)"*. The test suite currently runs solely against in-memory SQLite with `StaticPool`.
- **Evidence:**
  - `user-service/tests/conftest.py` line 25: `test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")`.
  - Postgres-specific behavior (e.g. `SELECT ... FOR UPDATE` row-level locks, PostgreSQL JSONB containment, and transactional concurrency under high load) cannot be tested against SQLite.
- **Impact:** Postgres-specific locking rules (such as concurrent last-owner demotions) may behave differently between test and production.
- **Recommended Fix:** Add test fixtures utilizing `testcontainers[postgres]` or a dedicated PostgreSQL test container in `docker-compose.yml`.

---

## 4. Coverage Gaps

The following spec rules have **zero** automated tests because the code is unimplemented:
1. **Registration & Duplicate Handling:** Case/space-insensitive uniqueness check via API.
2. **Account Lockout:** Account locking after 5 failed attempts; 15-minute expiration.
3. **Refresh Rotation & Reuse Detection:** Family invalidation upon presenting an old refresh token.
4. **Session Invalidation:** `logout` and `logout-all` revoking active refresh families.
5. **Tenancy Enforcement:** `X-Business-ID` header required when user has multiple memberships; returning 400 when missing, 403 when user is not a member.
6. **Last-Owner Invariant:** Concurrent requests attempting to demote the last two owners (verifying exactly one succeeds via `SELECT ... FOR UPDATE`).
7. **Internal Service-to-Service Flow:** Client-credentials exchange at `/internal/v1/service-token` and live DB authorization decisions at `/internal/v1/authz/check`.
8. **Audit Trail Recording:** Verifying that registration, logins, token refresh, and business operations write un-redacted rows to `audit_log`.

---

## 5. Deviations from Specification

1. **Unmounted Routers:** Only `/.well-known/jwks.json` and `/health` are mounted on the FastAPI app. The `/api/v1` and `/internal/v1` prefixes do not exist.
2. **Key Storage:** The application generates a transient in-memory RSA keypair when environment variables are not supplied. This is beneficial for local testing, but production deployments must enforce providing `JWT_PRIVATE_KEY_PEM`.

---

## 6. Commands to Reproduce Verification Checks

All commands can be re-run from the workspace root:

```bash
# 1. Run static security and code audit scan
python verification/static_scan.py
python verification/check_architecture.py

# 2. Run existing automated tests and coverage report
cd user-service
pytest --cov=app --cov-report=term-missing
cd ..

# 3. Test token security (tampering, alg:none, algorithm confusion, audience)
python verification/test_tokens.py

# 4. Test behavioral HTTP API calls and timing verification
python verification/test_behavior.py

# 5. Verify Alembic migration offline DDL compilation
cd user-service
alembic upgrade head --sql
alembic downgrade 001_initial_schema:base --sql
cd ..
```
