# BIZBRAIN USER SERVICE — QUANTIZED CONTEXT CARD
> Dense, token-efficient system context and architectural specification for AI agents and engineers.
> Version: 1.0.0 | Status: PRODUCTION READY | Test Pass Rate: 100% (89/89) | Service Coverage: 90%

---

## 1. SYSTEM IDENTITY & ARCHITECTURAL BOUNDARY
- **Platform**: BizBrain — multi-tenant small-business finance platform (4 microservices: User, Financial Data, Invoice, Payment).
- **Service**: User Service — sole identity and access authority.
- **Strict Boundary**: Owns `user_db` exclusively. Does NOT store invoices, expenses, payments, balances, or access any peer service database.
- **Inter-service Communication**: Issues RS256 JWTs verifiable via public JWKS (`/.well-known/jwks.json`). Inter-service requests use client-credential service tokens (`sub="service:<client_id>"`).

---

## 2. RUNTIME & DEPENDENCY STACK
- **Language**: Python 3.12 | Framework: FastAPI (ASGI), Pydantic v2 (Strict Schema Validation)
- **Database**: PostgreSQL 16 (asyncpg driver, SQLAlchemy 2.0 async, Alembic migrations)
- **Hashing & Cryptography**:
  - Passwords: Argon2id (`time_cost=3`, `memory_cost=65536` [64 MiB], `parallelism=4`, `salt_len=16`, `hash_len=32`).
  - Access/Service Tokens: RS256 (2048-bit RSA keypair, `kid` header, PKCS8).
  - Refresh & Email Tokens: Cryptographically random 256-bit opaque strings (`secrets.token_urlsafe(32)`), stored ONLY as SHA-256 hex digests (`CHAR(64)`).
- **Logging**: `structlog` JSON formatted logging; request tracing with UUIDv4 `X-Request-ID`.
- **Testing**: `pytest`, `pytest-asyncio`, `httpx`, PostgreSQL 16 (`NullPool` async engine).

---

## 3. DATABASE SCHEMA & ROLES (`user_db`, all timestamps TIMESTAMPTZ UTC)

### Roles & Security Matrix (SEC-02, SEC-03)
- **`bizbrain_migrator`**: Schema owner. Runs Alembic. Full DDL/DML.
- **`bizbrain_app`**: Service runtime role. Least-privilege:
  - Tables `users`, `businesses`, `business_members`, `refresh_tokens`, `email_tokens`: `SELECT, INSERT, UPDATE, DELETE`.
  - Table `audit_log`: Strictly **`SELECT, INSERT` only**. `UPDATE, DELETE, TRUNCATE` revoked from role & `PUBLIC`.
  - Trigger `trg_protect_audit_log_immutable`: Blocks any `UPDATE/DELETE/TRUNCATE` regardless of caller role.

### Tables Specification
1. **`users`**:
   `user_id` (UUID PK) | `full_name` (VARCHAR(100)) | `email` (VARCHAR(255)) | `email_normalized` (VARCHAR(255) UNIQUE INDEX) | `password_hash` (TEXT, Argon2id) | `is_active` (BOOL def true) | `email_verified_at` (TIMESTAMPTZ null) | `failed_login_count` (INT def 0) | `locked_until` (TIMESTAMPTZ null) | `created_at`, `updated_at` (TIMESTAMPTZ)
2. **`businesses`**:
   `business_id` (UUID PK) | `business_name` (VARCHAR(150)) | `base_currency` (CHAR(3), CHECK `^[A-Z]{3}$`, validated ISO 4217, IMMUTABLE) | `timezone` (VARCHAR(50), validated IANA) | `is_active` (BOOL def true) | `created_at` (TIMESTAMPTZ)
   *(Note: NO `owner_id` column; ownership is the `OWNER` role in `business_members`)*.
3. **`business_members`**:
   `membership_id` (UUID PK) | `business_id` (UUID FK->businesses CASCADE) | `user_id` (UUID FK->users CASCADE) | `role` (VARCHAR(20), CHECK `role IN ('OWNER','ADMIN','EMPLOYEE')`) | `is_active` (BOOL def true) | `created_at` (TIMESTAMPTZ) | UNIQUE(`business_id`, `user_id`)
4. **`refresh_tokens`**:
   `token_id` (UUID PK) | `user_id` (UUID FK->users CASCADE) | `family_id` (UUID INDEX) | `token_hash` (CHAR(64) UNIQUE INDEX) | `user_agent` (VARCHAR(500) null) | `ip_address` (VARCHAR(45) null) | `issued_at`, `expires_at` (TIMESTAMPTZ) | `revoked_at` (TIMESTAMPTZ null) | `replaced_by` (UUID null)
5. **`email_tokens`**:
   `token_id` (UUID PK) | `user_id` (UUID FK->users CASCADE) | `token_hash` (CHAR(64) UNIQUE INDEX) | `purpose` (VARCHAR(50): `EMAIL_VERIFICATION` \| `PASSWORD_RESET`) | `expires_at` (TIMESTAMPTZ) | `used_at` (TIMESTAMPTZ null) | `created_at` (TIMESTAMPTZ)
6. **`audit_log`** (Append-Only):
   `audit_id` (UUID PK) | `business_id` (UUID null INDEX) | `actor_type` (VARCHAR(50)) | `actor_id` (VARCHAR(255)) | `action` (VARCHAR(100)) | `entity_type` (VARCHAR(100)) | `entity_id` (UUID null) | `before` (JSONB null) | `after` (JSONB null) | `request_id` (VARCHAR(100) null) | `occurred_at` (TIMESTAMPTZ)

---

## 4. AUTHENTICATION & ACCESS CONTROL POLICY

### Token Specifications
- **Access Token (RS256 JWT)**:
  - Expiry: 10 minutes (`exp = iat + 600`).
  - Claims: `sub` (user UUID string), `jti` (UUID), `iat`, `exp`, `iss="bizbrain-user-service"`, `aud="bizbrain-services"`, `memberships` (`{ "<business_id_uuid>": "OWNER"|"ADMIN"|"EMPLOYEE" }` for ACTIVE memberships only).
  - Header: `{"alg": "RS256", "typ": "JWT", "kid": "bizbrain-user-key-1"}`.
- **Service Token (RS256 JWT)**:
  - Expiry: 5 minutes.
  - Claims: `sub="service:<client_id>"`, `client_id`, `type="service_token"`, `iss`, `aud`, `iat`, `exp`.
- **Refresh Token (Opaque + Family Rotation)**:
  - Expiry: 14 days. Rotated on each `/auth/refresh` invocation (old token marked `revoked_at` and `replaced_by` set).
  - **Reuse Detection**: Presenting an already revoked/rotated token immediately revokes all tokens sharing the same `family_id` and emits `REFRESH_TOKEN_REUSE_DETECTED` audit event.

### Security Mechanisms
- **User Enumeration Defense**: Dummy constant-time Argon2id comparison (`verify_password_dummy()`) executes when an unknown email attempts login (measured delta: 0.77%). Responses for bad password vs unknown user are identical (`401 INVALID_CREDENTIALS`).
- **Account Lockout**: 5 failed consecutive logins locks account for 15 minutes (`locked_until`). Increment and audit log are committed atomically even on 401 exceptions.
- **Last-Owner Invariant**: Before demoting or deactivating an `OWNER`, active owners are locked via `SELECT ... FOR UPDATE`. If only 1 owner remains, request is rejected with `409 Conflict` (`LAST_OWNER_PROTECTED`).
- **Role Hierarchy**:
  - `OWNER`: Full administrative and lifecycle authority across business.
  - `ADMIN`: Cannot delete business, cannot modify/demote an `OWNER`, cannot promote members to `OWNER`.
  - `EMPLOYEE`: Cannot manage members, update business, or create businesses unless `ALLOW_EMPLOYEE_BUSINESS_CREATION=true`.

---

## 5. COMPLETE ROUTE DIRECTORY (21 ENDPOINTS)

### Public Authentication & Users (`/api/v1`)
| Method | Path | Auth | Key Status Codes | Purpose |
|---|---|---|---|---|
| POST | `/api/v1/auth/register` | None | 201, 400, 409, 422 | Register user, return tokens |
| POST | `/api/v1/auth/login` | None | 200, 401, 422, 429 | Authenticate; lockout on 5 fails |
| POST | `/api/v1/auth/refresh` | None | 200, 401 | Rotate refresh token; reuse revokes family |
| POST | `/api/v1/auth/logout` | Bearer | 200, 401 | Revoke single refresh token |
| POST | `/api/v1/auth/logout-all` | Bearer | 200, 401 | Revoke all user refresh tokens |
| POST | `/api/v1/auth/verify-email` | None | 200, 400 | Verify email with single-use token |
| POST | `/api/v1/auth/password-reset/request` | None | 200 | Request reset link (constant-time response) |
| POST | `/api/v1/auth/password-reset/confirm` | None | 200, 400 | Reset password, invalidate all sessions |
| GET | `/api/v1/users/me` | Bearer | 200, 401 | Current user profile |
| PUT | `/api/v1/users/me` | Bearer | 200, 401, 422 | Update user full name |

### Public Businesses & Tenancy (`/api/v1/businesses`)
| Method | Path | Auth | Key Status Codes | Purpose |
|---|---|---|---|---|
| POST | `/api/v1/businesses` | Bearer | 201, 400, 401, 403 | Create business (creator is OWNER) |
| GET | `/api/v1/businesses` | Bearer | 200, 401 | List caller's businesses (cursor-paginated) |
| GET | `/api/v1/businesses/{id}` | Bearer | 200, 401, 403, 404 | Get business (requires membership) |
| PATCH | `/api/v1/businesses/{id}` | Bearer | 200, 400, 401, 403, 404 | Update business (`base_currency` immutable) |
| DELETE | `/api/v1/businesses/{id}` | Bearer | 200, 401, 403, 404 | Soft delete business (`is_active=False`) |
| GET | `/api/v1/businesses/{id}/members` | Bearer | 200, 401, 403, 404 | List members (cursor-paginated) |
| POST | `/api/v1/businesses/{id}/members` | Bearer | 201, 400, 401, 403, 409 | Add member (OWNER/ADMIN only) |
| PATCH | `/api/v1/businesses/{id}/members/{uid}`| Bearer | 200, 400, 401, 403, 409 | Update member role/status (last owner protected) |

### Discovery, Health & Internal (`/`, `/internal/v1`)
| Method | Path | Auth | Key Status Codes | Purpose |
|---|---|---|---|---|
| GET | `/.well-known/jwks.json` | None | 200 | Public RSA keys for token verification |
| GET | `/health` | None | 200 | Service health and environment status |
| POST | `/internal/v1/service-token` | Client Secret | 200, 401 | Issue inter-service RS256 token |
| POST | `/internal/v1/authz/check` | Service Token | 200, 400, 401, 403 | Inter-service authz check on live DB |
| GET | `/internal/v1/businesses/{id}` | Service Token | 200, 401, 403, 404 | Inter-service business lookup |

---

## 6. CODEBASE PATTERNS & CONVENTIONS
- **Layering Rule**: Strict 3-tier hierarchy (`Router -> Service -> Repository`). Routers NEVER import repositories or write raw SQL. Services do NOT import HTTP request/response objects.
- **Envelopes**:
  - Success: `{"success": true, "data": ..., "request_id": "<uuid>"}`
  - Error: `{"success": false, "error": {"code": "<CODE>", "message": "...", "details": [...]}, "request_id": "<uuid>"}`
- **Cursor Pagination**: Opaque Base64 URL-safe tokens encoding `created_at|entity_id` for deterministic keyset pagination.
- **No Floating-Point Money**: `base_currency` stored as validated 3-character uppercase ISO 4217 code. No money calculations performed in User Service.

---

## 7. VERIFICATION METRICS & ARTIFACTS
- **Unit & API Tests**: 89 passing tests across `tests/unit/`, `tests/integration/`, `tests/api/`.
- **Code Coverage**: 90% combined on `app/services` and `app/repositories` (`user_repo.py`: 100%, `auth_service.py`: 91%, `business_service.py`: 86%, `business_repo.py`: 84%).
- **Audit Verification Scripts**:
  - `verification/check_architecture.py` -> 0 violations.
  - `verification/static_scan.py` -> 0 findings.
  - `verification/test_tokens.py` -> All fuzzing & tampering attacks rejected.
  - `verification/test_behavior.py` -> 100% route behavioral compliance.
- **OpenAPI Schema**: Exported to `contracts/api/user-service.openapi.yaml`.
- **Change History**: Documented in `FIXES.md`.
