# BizBrain Financial Data Service

The **Financial Data Service** is an authoritative microservice for basic financial records within BizBrain, a multi-tenant small-business finance platform. It manages customers, suppliers, expense categories, expenses, expense receipts, and financial summaries.

In accordance with BizBrain architecture, this service:
- Strictly owns its data domain: Customers, Suppliers, Expense Categories, Expenses, and Receipts.
- Does NOT touch or store invoices, payment transactions, PayPal orders, or AI agents (owned by other microservices).
- Communicates across services strictly via documented internal REST APIs and signed service tokens.

---

## 1. Tech Stack

- **Runtime:** Python 3.12+
- **API Framework:** FastAPI, Pydantic v2
- **Database:** PostgreSQL 16 with SQLAlchemy 2.x (asyncpg) and Alembic migrations
- **Authentication:** PyJWT with RS256 token verification against User Service cached JWKS
- **Object Storage:** Private S3-compatible store (MinIO in local/Docker development) with boto3 / aioboto3
- **Logging:** Structured JSON logging via `structlog` with automated sensitive field redaction
- **HTTP Client:** `httpx` with timeout, retries, and circuit breaker
- **Testing:** `pytest`, `pytest-asyncio`, `httpx` ASGI test client, SQLite in-memory async engine

---

## 2. Project Structure & Layering Rules

```
financial-data-service/
├── app/
│   ├── api/                  # FastAPI routers: customers, suppliers, categories, expenses, summary, internal
│   ├── clients/              # External service clients: user_service_client.py (with circuit breaker)
│   ├── core/                 # Config, RS256 auth, permissions policy, database engine, error envelope,
│   │                         # idempotency, money precision, pagination, storage
│   ├── models/               # SQLAlchemy 2.0 ORM mapped models (Customer, Supplier, ExpenseCategory,
│   │                         # Expense, ExpenseReceipt, IdempotencyKey, AuditLog)
│   ├── repositories/         # Database access layer: EVERY function requires `business_id: uuid.UUID`
│   ├── schemas/              # Pydantic v2 schemas: validation, serialization, success/error envelopes
│   ├── services/             # Pure business logic layer (no HTTP concerns)
│   └── main.py               # FastAPI app factory, lifespan, CORS, middleware, health/ready checks
├── contracts/
│   └── api/
│       └── financial-data.openapi.yaml   # OpenAPI 3.0 specification
├── migrations/               # Alembic database migrations
├── tests/
│   ├── api/                  # API integration tests (customers, suppliers, categories, expenses, receipts, summary, internal, health)
│   ├── unit/                 # Unit tests (RS256 auth, permission matrix, tenant isolation)
│   └── conftest.py           # Pytest fixtures: RSA key pair, test client, SQLite async DB, token generators
├── Dockerfile                # Multi-stage production container image (non-root appuser)
├── docker-compose.yml        # Local environment with Postgres 16, MinIO, and User Service mock
├── requirements.txt          # Production and testing Python dependencies
└── README.md
```

**Layering Constraint:** Routers call Services, Services call Repositories. No SQL queries or DB access in Routers; no HTTP concerns in Services or Repositories.

---

## 3. Security, Multi-Tenancy & Permissions

### Authentication & Business Context
- Every public request requires `Authorization: Bearer <token>` signed with **RS256** and matching audience `bizbrain`.
- Tenancy is enforced via the `X-Business-ID` header, which must exist in the token's `memberships` claim. If a user belongs to exactly one business and omits the header, it defaults to that business.
- All database queries filter strictly by `business_id`. Foreign business records return `404 Not Found` (never 403) to prevent tenant enumeration and information leakage.

### Role Permission Matrix
Defined in a single policy module (`app/core/permissions.py`):

| Permission Code | Description | OWNER | ADMIN | EMPLOYEE |
| :--- | :--- | :---: | :---: | :---: |
| `dashboard.view` | View dashboard & financial summaries | ✅ | ✅ | ✅ |
| `customers.read` | Read customers | ✅ | ✅ | ✅ |
| `customers.write` | Create / update / deactivate customers | ✅ | ✅ | Create only (no edit/delete) |
| `suppliers.read` | Read suppliers | ✅ | ✅ | ✅ |
| `suppliers.write` | Create / update / deactivate suppliers | ✅ | ✅ | ❌ |
| `categories.read` | Read expense categories | ✅ | ✅ | ✅ |
| `categories.write` | Create / update / deactivate categories | ✅ | ✅ | ❌ |
| `expenses.record` | Record expenses | ✅ | ✅ | ✅ |
| `expenses.view` | View expenses | ✅ | ✅ | Own expenses only |
| `expenses.edit` | Edit description/category or upload receipt | ✅ | ✅ | Own expenses only |
| `expenses.void` | Void an expense | ✅ | ✅ | ❌ |

---

## 4. Financial Data Integrity & Guardrails

- **Money Precision:** Money is stored as `NUMERIC(19,4)` in PostgreSQL and handled as Python `Decimal`. Money is never represented as `float` and is serialized as string values in JSON (e.g. `"125.50"`).
- **Currency Validation:** Expense currency must match the business's authoritative `base_currency` (retrieved from User Service and cached).
- **Idempotency Protection:** `POST /api/v1/expenses` strictly requires a UUID `Idempotency-Key` header.
  - Same key + identical payload returns `200 OK` with header `Idempotent-Replayed: true`.
  - Reused key + different payload returns `422 IDEMPOTENCY_KEY_REUSED`.
- **Expense Immutability & Void Flow:**
  - `PATCH /api/v1/expenses/{id}` permits modifying only `description` and `category_id`.
  - Attempting to edit `amount`, `currency`, `expense_date`, or `supplier_id` returns `422 FIELD_NOT_EDITABLE`.
  - `POST /api/v1/expenses/{id}/void` uses `SELECT ... FOR UPDATE` row locking, calls the User Service `/internal/v1/authz/check` live authorization check, sets `status = 'VOID'`, and requires `void_reason`.
  - Voided expenses cannot be edited or have receipts added (`409 Conflict`).
- **No Physical Deletions:** Customers and suppliers are soft-deactivated (`is_active = False`); financial records are never deleted.
- **Audit Logging:** Every mutating financial action (`CUSTOMER_CREATED`, `CUSTOMER_UPDATED`, `CUSTOMER_DEACTIVATED`, `SUPPLIER_CREATED`, `SUPPLIER_DEACTIVATED`, `CATEGORY_CREATED`, `CATEGORY_UPDATED`, `EXPENSE_RECORDED`, `EXPENSE_UPDATED`, `EXPENSE_VOIDED`, `RECEIPT_UPLOADED`) writes an append-only row to `audit_log` within the same transaction.

---

## 5. Object Storage & Receipts

- Receipt uploads (`POST /api/v1/expenses/{id}/receipt`) enforce:
  - Max file size: **5 MB**.
  - **Magic-bytes content sniffing** (PDF: `%PDF-`, PNG: `\x89PNG\r\n\x1a\n`, JPEG: `\xff\xd8\xff`). Disguised or non-matching files are rejected with `422 INVALID_FILE_TYPE`.
  - SHA-256 hash calculation and verification.
  - Secure key generation: `receipts/{business_id}/{expense_id}/{receipt_id}.{ext}`.
  - Private bucket storage; downloads via short-lived presigned URLs (`GET /api/v1/expenses/{id}/receipt`).

---

## 6. Internal API & Inter-Service Contract

### Contract C1 (Invoice Service -> Financial Data Service)
- **Endpoint:** `GET /internal/v1/customers/{customer_id}?business_id={business_id}`
- **Security:** Requires a verified service token (`aud="financial-data-service"`), rejecting user tokens.
- **Allowed Callers:** `invoice-service`, `user-service`.
- **Purpose:** Invoice Service retrieves customer snapshot (`name`, `email`, `billing_address`, `tax_id`, `is_active`) to validate customer eligibility before issuing an invoice.

---

## 7. API Reference Summary

| Method | Endpoint | Description | Auth / Role |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Liveness check | Public |
| `GET` | `/ready` | Readiness check (database connectivity) | Public |
| `POST` | `/api/v1/customers` | Create customer | `customers.write` |
| `GET` | `/api/v1/customers` | List customers (cursor paginated, search, filter) | `customers.read` |
| `GET` | `/api/v1/customers/{id}` | Get customer | `customers.read` |
| `PUT` | `/api/v1/customers/{id}` | Update customer | `customers.write` (Owner/Admin) |
| `DELETE` | `/api/v1/customers/{id}` | Soft deactivate customer | `customers.write` (Owner/Admin) |
| `POST` | `/api/v1/suppliers` | Create supplier | `suppliers.write` |
| `GET` | `/api/v1/suppliers` | List suppliers (cursor paginated) | `suppliers.read` |
| `GET` | `/api/v1/suppliers/{id}` | Get supplier | `suppliers.read` |
| `PUT` | `/api/v1/suppliers/{id}` | Update supplier | `suppliers.write` |
| `DELETE` | `/api/v1/suppliers/{id}` | Soft deactivate supplier | `suppliers.write` |
| `POST` | `/api/v1/expense-categories` | Create custom category | `categories.write` |
| `GET` | `/api/v1/expense-categories` | List categories (lazy default seeding) | `categories.read` |
| `PATCH` | `/api/v1/expense-categories/{id}`| Rename or deactivate category | `categories.write` |
| `POST` | `/api/v1/expenses` | Record expense (requires `Idempotency-Key`) | `expenses.record` |
| `GET` | `/api/v1/expenses` | List expenses (cursor paginated, filters) | `expenses.view` |
| `GET` | `/api/v1/expenses/{id}` | Get expense | `expenses.view` |
| `PATCH` | `/api/v1/expenses/{id}` | Edit description / category only | `expenses.edit` |
| `POST` | `/api/v1/expenses/{id}/void` | Void expense with reason | `expenses.void` (Owner/Admin) |
| `POST` | `/api/v1/expenses/{id}/receipt`| Upload receipt (magic bytes, max 5MB) | `expenses.edit` |
| `GET` | `/api/v1/expenses/{id}/receipt` | Get presigned download URL (15 min) | `expenses.view` |
| `GET` | `/api/v1/financial-summary/expenses` | Aggregated expenses by category and month | `dashboard.view` |
| `GET` | `/api/v1/financial-summary` | High-level summary totals | `dashboard.view` |
| `GET` | `/internal/v1/customers/{id}` | Fetch customer snapshot (Contract C1) | Service Token (`invoice-service`) |

---

## 8. Running Locally

### Prerequisites
- Python 3.12+ (or Docker & Docker Compose)
- PostgreSQL 16
- MinIO (or AWS S3)

### Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Key configuration options:
- `DATABASE_URL`: `postgresql+asyncpg://financial_user:financial_pass@localhost:5432/financial_db`
- `USER_SERVICE_BASE_URL`: `http://localhost:8001`
- `S3_ENDPOINT_URL`: `http://localhost:9000`
- `S3_ACCESS_KEY`: `minioadmin`
- `S3_SECRET_KEY`: `minioadmin`
- `S3_BUCKET_NAME`: `receipts`
- `JWT_AUDIENCE`: `bizbrain`

### Running with Docker Compose
```bash
docker-compose up --build
```
This starts:
1. `financial-data-service` on port `8002`
2. `postgres` (PostgreSQL 16) on port `5432`
3. `minio` on ports `9000` (API) and `9001` (Console)
4. `user-service-mock` on port `8001` (providing JWKS and internal endpoints)

### Running Database Migrations
```bash
alembic upgrade head
```

### Running Tests
All 52 tests run against an in-memory async SQLite engine with comprehensive isolation:
```bash
pytest -v tests
```
