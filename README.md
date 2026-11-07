# BizBrain – Multi-Tenant Small-Business Finance Platform

BizBrain is a modern, distributed financial management platform built with an event-driven, multi-tenant microservices architecture. It provides identity, access authority, financial data aggregation, invoice processing, and payment workflows.

---

## Architecture Overview

BizBrain is organized into four independent, decoupled services:

1. **User Service (`user-service/`)**: The identity and access authority. Manages users, business tenants, memberships, roles, sessions, and RS256 token issuance. **Owns `user_db` exclusively.** Does not store expenses, invoices, or payment records.
2. **Financial Data Service**: Handles customer records, expense recording, accounts, and reporting.
3. **Invoice Service**: Manages draft, issue, and void lifecycles for customer invoices.
4. **Payment Service**: Processes payment configurations, receipts, and reconciliations.

```
                  +-----------------------------------+
                  |         BizBrain Clients          |
                  +-----------------------------------+
                                    |
                    +---------------+---------------+
                    |               |               |
                    v               v               v
            +---------------+---------------+---------------+
            |  User Service |Invoice Service|Financial Data |
            |  (Identity)   |  (Invoices)   |  (Accounting) |
            +---------------+---------------+---------------+
                    |
                    v
             PostgreSQL 16
              (`user_db`)
```

---

## User Service Quick Reference

### Tech Stack
- **Runtime:** Python 3.12, FastAPI, Pydantic v2
- **Database:** PostgreSQL 16 (asyncpg, SQLAlchemy 2.0 async, Alembic migrations)
- **Security:** Argon2id password hashing, RS256 JWTs with public JWKS discovery, SHA-256 hashed refresh and single-use email tokens
- **Auditing:** Immutable append-only audit trail protected at the PostgreSQL role and trigger levels
- **Observability:** `structlog` JSON structured logging with propagated `X-Request-ID` tracing

### Key Features
- **Strict Tenancy Isolation:** All business endpoints require tenant membership validation. Roles (`OWNER`, `ADMIN`, `EMPLOYEE`) are derived strictly from cryptographically verified tokens or live database queries.
- **Transactional Last-Owner Protection:** Prevents removing, demoting, or deactivating the last active owner of a business using row-level locking (`SELECT ... FOR UPDATE`).
- **Timing-Attack Resistance:** Employs precomputed dummy Argon2id hashes for non-existent users during login to guarantee constant-time responses (0.77% relative variance) preventing account enumeration.
- **Session Security:** Refresh token rotation on every use with automatic detection and revocation of the entire token family upon reuse of old tokens. 5 failed login attempts trigger a 15-minute account lockout.
- **Role Hardening:** Runtime execution role `bizbrain_app` has strictly limited privileges; `UPDATE`, `DELETE`, and `TRUNCATE` are revoked on `audit_log` with an immutable database trigger defense-in-depth.

---

## Directory Structure

```
.
├── contracts/
│   └── api/
│       └── user-service.openapi.yaml   # Exported OpenAPI 3.1 specification
├── user-service/                       # Microservice implementation
│   ├── app/
│   │   ├── api/                        # Route controllers (v1 auth, businesses, internal, jwks)
│   │   ├── core/                       # Security, config, RBAC policy, middleware, database
│   │   ├── models/                     # SQLAlchemy 2.0 models (users, businesses, members, tokens, audit)
│   │   ├── repositories/               # Encapsulated database access layer
│   │   ├── schemas/                    # Pydantic v2 request/response models & envelopes
│   │   ├── services/                   # Business domain services
│   │   └── main.py                     # Application entry point
│   ├── docker/
│   │   └── initdb/
│   │       └── 01-roles.sh             # PostgreSQL role creation and least-privilege grants
│   ├── migrations/                     # Alembic database migrations
│   ├── tests/                          # 89 unit, integration, and API tests
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── requirements.txt
├── verification/                       # Security audit scripts and initial report
│   ├── REPORT.md                       # Auditor's initial evaluation report
│   ├── check_architecture.py           # Layering and RBAC static analyzer
│   ├── static_scan.py                  # Secret, SQL injection, and float scanner
│   ├── test_tokens.py                  # Token attack and RS256 fuzzing tests
│   └── test_behavior.py                # Behavioral and timing parity tests
├── FIXES.md                            # Comprehensive remediation and implementation log
└── README.md
```

---

## Getting Started

### 1. Prerequisites
- Python 3.12+
- PostgreSQL 16+ or Docker

### 2. Setup Database & Roles
Ensure PostgreSQL is running, then create the database and restricted roles:
```bash
# As postgres superuser:
CREATE DATABASE user_db;
CREATE ROLE bizbrain_migrator WITH LOGIN PASSWORD 'migrator_secure_pass_123';
CREATE ROLE bizbrain_app WITH LOGIN PASSWORD 'app_runtime_secure_pass_123';
```
*(When using Docker Compose, roles and permissions are configured automatically via `docker/initdb/01-roles.sh`)*.

### 3. Run Migrations
```bash
cd user-service
alembic upgrade head
```

### 4. Run the Service
```bash
# Development server:
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Or with Docker Compose:
docker-compose up --build
```
The API is available at `http://localhost:8000` with Swagger UI at `http://localhost:8000/docs`.

---

## Testing & Quality Assurance

The test suite runs against real PostgreSQL 16:

```bash
cd user-service

# Run all 89 tests:
pytest -v

# Run with test coverage report:
pytest --cov=app/services --cov=app/repositories --cov-report=term-missing
```

### Coverage Summary
- **Total Services & Repositories Coverage:** **90%**
- **Unit, API, and Integration Test Results:** **89 passed, 0 failed** (100% pass rate)

---

## API Specification
The full API schema is available in [`contracts/api/user-service.openapi.yaml`](contracts/api/user-service.openapi.yaml) or via the interactive endpoint `/docs`.
