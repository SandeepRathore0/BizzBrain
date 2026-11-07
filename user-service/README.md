# BizBrain User Service

The **User Service** is the identity and access authority for BizBrain, a multi-tenant small-business finance platform. It manages users, businesses, memberships, roles, sessions, and token issuance.

> **Isolation Boundary:** The User Service does not create invoices, store expenses, process payments, keep financial balances, or access any other service's database.

---

## Tech Stack
- **Language & Runtime:** Python 3.12
- **Framework:** FastAPI, Pydantic v2, Pydantic Settings
- **Database:** PostgreSQL 16, SQLAlchemy 2.0 (Async), asyncpg, Alembic migrations
- **Security & Cryptography:** Argon2id (`argon2-cffi`), RS256 JWTs (`PyJWT` with cryptography), SHA-256 for opaque refresh and single-use tokens
- **Logging:** `structlog` for structured JSON logging with request tracing
- **Testing:** `pytest`, `pytest-asyncio`, `httpx`
- **Containerization:** Docker & Docker Compose

---

## Project Structure

```
user-service/
  app/
    api/              # Route controllers (auth, users, businesses, members, internal, jwks)
    core/             # Config, security, permissions, errors, logging, middleware, database, deps
    models/           # SQLAlchemy 2.0 declarative models
    schemas/          # Pydantic v2 request/response models & standard envelopes
    services/         # Business domain logic (decoupled from HTTP concerns)
    repositories/     # Database access layer (no SQL in routers or services)
    main.py           # FastAPI application factory and lifespan configuration
  migrations/         # Alembic async migration environment & revision versions
  tests/
    unit/             # Unit tests (config, security, errors, permissions, models, services)
    integration/      # Integration test suites against database (audit immutability, schema types)
    api/              # HTTP API tests (auth, businesses, members, internal, health, jwks)
  Dockerfile          # Production container specification
  docker-compose.yml  # Local multi-container setup (Postgres 16 user_db + service)
  requirements.txt    # Frozen dependency specifications
  .env.example        # Environment variable configuration template
  README.md
```

---

## Database Security & Role Model (SEC-02, SEC-03)

The service enforces least privilege at the database level:
- **`bizbrain_migrator`**: Owns all tables, runs Alembic migrations.
- **`bizbrain_app`**: Runtime application role used by the FastAPI service. Has `SELECT`, `INSERT`, `UPDATE`, `DELETE` on normal tables, but **strictly `SELECT` and `INSERT` only** on `audit_log`. `UPDATE`, `DELETE`, and `TRUNCATE` are explicitly revoked.
- **Audit Immutability Trigger**: A PL/pgSQL trigger on `audit_log` raises an exception on any `UPDATE`, `DELETE`, or `TRUNCATE` attempts even if executed by the table owner.

---

## API Endpoints

### Public Endpoints (`/api/v1`)
| Method | Path | Auth Required | Description |
|---|---|---|---|
| POST | `/api/v1/auth/register` | None | Register new user; issues tokens |
| POST | `/api/v1/auth/login` | None | Authenticate user; 5 failures locks account for 15 min |
| POST | `/api/v1/auth/refresh` | None | Rotate refresh token; reuse revokes token family |
| POST | `/api/v1/auth/logout` | Bearer Token | Revoke single refresh token |
| POST | `/api/v1/auth/logout-all` | Bearer Token | Revoke all active sessions for user |
| POST | `/api/v1/auth/verify-email` | None | Consume single-use email verification token |
| POST | `/api/v1/auth/password-reset/request` | None | Request password reset (constant-time response) |
| POST | `/api/v1/auth/password-reset/confirm` | None | Confirm password reset and revoke all sessions |
| GET | `/api/v1/users/me` | Bearer Token | Get current user profile |
| PUT | `/api/v1/users/me` | Bearer Token | Update user full name |
| POST | `/api/v1/businesses` | Bearer Token | Create new business tenant (creator becomes OWNER) |
| GET | `/api/v1/businesses` | Bearer Token | List businesses for current user (cursor-paginated) |
| GET | `/api/v1/businesses/{id}` | Bearer Token | Get business details (requires membership) |
| PATCH | `/api/v1/businesses/{id}` | Bearer Token | Update business (`base_currency` is immutable) |
| DELETE | `/api/v1/businesses/{id}` | Bearer Token | Soft-delete business tenant |
| GET | `/api/v1/businesses/{id}/members` | Bearer Token | List members of business (cursor-paginated) |
| POST | `/api/v1/businesses/{id}/members` | Bearer Token | Add new member with role |
| PATCH | `/api/v1/businesses/{id}/members/{uid}` | Bearer Token | Update member role/status (last owner protected) |
| GET | `/.well-known/jwks.json` | None | Public key discovery for RS256 token verification |
| GET | `/health` | None | Service health check |

### Internal Endpoints (`/internal/v1`)
*Network-isolated; requires Service Token.*
| Method | Path | Description |
|---|---|---|
| POST | `/internal/v1/service-token` | Client Credentials flow for inter-service tokens (5 min expiry) |
| POST | `/internal/v1/authz/check` | Check user permissions against live DB state |
| GET | `/internal/v1/businesses/{id}` | Inter-service business lookup |

---

## Role-Based Access Control (RBAC) Matrix

| Permission | OWNER | ADMIN | EMPLOYEE |
|---|:---:|:---:|:---:|
| `business.delete` | Yes | No | No |
| `business.update` | Yes | Yes | No |
| `members.manage` | Yes | Yes (cannot touch OWNER) | No |
| `invoices.create` | Yes | Yes | Yes |
| `invoices.edit_draft` | Yes | Yes | Yes |
| `invoices.issue` | Yes | Yes | No |
| `invoices.void` | Yes | Yes | No |
| `expenses.record` | Yes | Yes | Yes |
| `expenses.view` | Yes | Yes | Yes |
| `expenses.void` | Yes | Yes | No |
| `customers.read` | Yes | Yes | Yes |
| `customers.write` | Yes | Yes | Yes |
| `dashboard.view` | Yes | Yes | Yes |
| `payments.view` | Yes | Yes | Yes |
| `payments.configure` | Yes | No | No |

---

## Running the Service

### 1. Database Migrations
```bash
alembic upgrade head
```

### 2. Running Tests
```bash
python -m pytest -v
```

### 3. Running with Coverage
```bash
python -m pytest --cov=app/services --cov=app/repositories --cov-report=term-missing
```

### 4. Running via Docker Compose
```bash
docker-compose up --build
```
The service will be accessible at `http://localhost:8000`, with interactive OpenAPI documentation at `http://localhost:8000/docs`.
