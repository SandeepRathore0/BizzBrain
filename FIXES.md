# BizBrain User Service – Remediation & Implementation Log (FIXES.md)

This document tracks all remediation and implementation work performed to address every finding from the security audit report (`verification/REPORT.md`) and achieve production readiness according to the specification.

---

## 1. Executive Summary of Changes

| Stage | Focus Area | Status | Key Deliverables |
|---|---|---|---|
| **Stage A** | Database Foundation & Hardening | **COMPLETED** | Migration 002 (CHAR types, CHECK constraints, immutability trigger), `docker/initdb/01-roles.sh` with restricted `bizbrain_app` role. |
| **Stage B** | Real PostgreSQL Test Foundation | **COMPLETED** | Migrated tests from SQLite to real PostgreSQL 16 (`user_test_db`) using async SQLAlchemy and `NullPool`. Verification of audit log permission restrictions. |
| **Stage C** | Core Authentication & Session Engine | **COMPLETED** | Full `/api/v1/auth/*` and `/users/me` router suite, `AuthService`, `UserRepository`, Argon2id with dummy timing parity, refresh family rotation, reuse revocation, lockout after 5 failures. |
| **Stage D** | Tenancy & Business Management | **COMPLETED** | Full `/api/v1/businesses/*` and membership management router suite, `BusinessService`, `BusinessRepository`, transactional last-owner protection with `SELECT ... FOR UPDATE`, tenant isolation. |
| **Stage E** | Internal Endpoints & Inter-service Auth | **COMPLETED** | `/internal/v1/service-token`, `/internal/v1/authz/check`, `/internal/v1/businesses/{id}` with strict service-token enforcement and DB authorization checks. |
| **Stage F** | Hardening, Coverage & Documentation | **COMPLETED** | 89 tests passing (100% pass rate), 90% combined coverage on services and repositories, OpenAPI contract generated, comprehensive README. |

---

## 2. Detailed Findings & Fixes Matrix

### Stage A: Database Foundation (SEC-02, SEC-03)
- **SEC-03 (CHAR type compliance):**
  - Added Alembic migration `002_fix_char_and_audit_trigger.py` converting `businesses.base_currency` to `CHAR(3)` with CHECK constraint `^[A-Z]{3}$`, and `refresh_tokens.token_hash` to `CHAR(64)` unique.
  - Updated SQLAlchemy models `app/models/business.py` and `app/models/token.py`.
- **SEC-02 (Application DB role & audit log immutability):**
  - Created `docker/initdb/01-roles.sh` defining two roles: `bizbrain_migrator` (table owner, Alembic runner) and `bizbrain_app` (least-privilege runtime application user).
  - Explicitly revoked `UPDATE`, `DELETE`, and `TRUNCATE` on `audit_log` from `bizbrain_app` and `PUBLIC`.
  - Added PostgreSQL PL/pgSQL trigger on `audit_log` raising an exception on any `UPDATE`, `DELETE`, or `TRUNCATE` attempts (defense-in-depth).
  - Updated `docker-compose.yml` and `.env.example` with `DATABASE_URL` (runtime role) and `MIGRATION_DATABASE_URL` (migrator role).

### Stage B: Real PostgreSQL Test Foundation (SEC-04)
- **SEC-04 (Tests running on SQLite instead of PostgreSQL):**
  - Configured PostgreSQL 16 cluster with isolated test database `user_test_db`.
  - Applied migrations via `bizbrain_migrator`.
  - Updated `tests/conftest.py` with async SQLAlchemy engine using `NullPool` to prevent connection leaks across event loops.
  - Created `tests/integration/test_audit_log_permissions.py` proving `bizbrain_app` cannot modify or delete audit rows (`permission denied`).
  - Created `tests/integration/test_schema_types.py` verifying `character(3)` and `character(64)` types in PostgreSQL `information_schema`.

### Stage C: Core Authentication & Sessions
- **Endpoints Implemented (`app/api/v1/auth.py`):**
  - `POST /api/v1/auth/register` (201 Created with envelope, user, access token RS256, refresh token)
  - `POST /api/v1/auth/login` (200 OK with envelope, tokens)
  - `POST /api/v1/auth/refresh` (200 OK, token rotation, reuse detection)
  - `POST /api/v1/auth/logout` (200 OK, revokes specific refresh token)
  - `POST /api/v1/auth/logout-all` (200 OK, revokes all active user refresh tokens)
  - `POST /api/v1/auth/verify-email` (200 OK, single-use token consumption)
  - `POST /api/v1/auth/password-reset/request` (200 OK, constant response preventing user enumeration)
  - `POST /api/v1/auth/password-reset/confirm` (200 OK, resets password and invalidates all existing sessions)
  - `GET /api/v1/users/me` & `PUT /api/v1/users/me`
- **Security & Business Logic (`app/services/auth_service.py` & `app/repositories/user_repo.py`):**
  - Timing parity between existent and non-existent users on login using precomputed dummy Argon2id hash (measured relative difference: 0.77%).
  - Account lockout after 5 failed login attempts for 15 minutes. Failed attempts and audit logs committed immediately.
  - Refresh token rotation: generates new token in same family; reuse of previously rotated or revoked token triggers immediate family-wide revocation.

### Stage D: Tenancy & Business Management
- **Endpoints Implemented (`app/api/v1/businesses.py`):**
  - `POST /api/v1/businesses` (creator automatically assigned `OWNER` role in `business_members`)
  - `GET /api/v1/businesses` (cursor-paginated listing of user's active businesses)
  - `GET /api/v1/businesses/{id}` (tenancy-scoped business lookup)
  - `PATCH /api/v1/businesses/{id}` (`base_currency` is strictly immutable, name and timezone updatable)
  - `DELETE /api/v1/businesses/{id}` (soft-delete marking `is_active=False`)
  - `GET /api/v1/businesses/{id}/members` (cursor-paginated member list)
  - `POST /api/v1/businesses/{id}/members` (add member with role)
  - `PATCH /api/v1/businesses/{id}/members/{user_id}` (update role or active state)
- **Last-Owner Protection (`app/services/business_service.py`):**
  - Evaluates active owner count and locks owner rows using `SELECT ... FOR UPDATE`. Demoting or deactivating the last active owner raises `409 Conflict` (`LAST_OWNER_PROTECTED`).

### Stage E: Internal Endpoints & Inter-service Auth
- **Endpoints Implemented (`app/api/internal/v1/internal.py`):**
  - `POST /internal/v1/service-token` (client credentials flow, RS256 token with 5-minute expiry)
  - `POST /internal/v1/authz/check` (inter-service authorization check against live DB state)
  - `GET /internal/v1/businesses/{id}` (internal business metadata lookup)
- **Layering & Isolation:**
  - Internal routes are segregated under `/internal/v1/`.
  - Public endpoints reject service tokens; internal endpoints require service tokens.
  - Layering rule strictly respected: routers call `BusinessService`, services call repositories.

### Stage F: Hardening & Test Verification
- **Static Analysis & Architecture Check:**
  - `verification/check_architecture.py`: 0 violations.
  - `verification/static_scan.py`: 0 violations.
  - `verification/test_tokens.py`: 100% passed (expired, tampered, alg=none, HS256 confusion, wrong audience/issuer rejected).
  - `verification/test_behavior.py`: 100% passed across all public/internal endpoints, Argon2 timing, JWKS, envelopes, and tracing headers.
- **Test Suite Results:**
  - **89 tests passing** across unit, integration, and API suites.
  - **90% total test coverage** on `app/services` and `app/repositories` (well exceeding the ≥85% requirement).
- **OpenAPI Contract:**
  - Exported to `contracts/api/user-service.openapi.yaml`.
