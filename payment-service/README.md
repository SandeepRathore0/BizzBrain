# BizBrain – Payment Service

The Payment Service manages multi-tenant checkout order creation, PayPal capture processing, incoming webhook event handling, at-least-once outbox event delivery to the Invoice Service, and autonomous reconciliation of uncertain capture states and stale checkouts.

---

## 1. Architecture & Background Workers

In addition to the FastAPI HTTP API, the Payment Service runs two continuous background worker processes:

1. **Outbox Worker (`python -m app.workers.outbox_worker`)**:
   - Delivers `PaymentCaptured` events from `outbox_events` to the Invoice Service (contract C3: `POST /internal/v1/payment-events`) with at-least-once semantics.
   - Claims rows in short transactions with `FOR UPDATE SKIP LOCKED`.
   - Never holds a database lock or transaction open during HTTP calls.
   - Validates every payload against `contracts/events/PaymentCaptured.v1.json` before sending. Invalid payloads are marked `DEAD` with alert `OUTBOX_PAYLOAD_INVALID`.
   - Handles service token authentication (single 401 refresh retry), 5 consecutive failure circuit breaker with 30s cooldown, and full jitter exponential backoff up to 600s.
   - Moves payments to `allocation_status = 'ALLOCATED'` on 200 `APPLIED`/`DUPLICATE_IGNORED` or `allocation_status = 'REJECTED'` on 200 `REJECTED` (with `ALLOCATION_REJECTED` alert).

2. **Reconciliation Worker (`python -m app.workers.reconciliation_worker`)**:
   - Runs every 60 seconds across three periodic jobs:
     - **Job A (Stale Checkout Expiry)**: Sweeps `CREATED` and `PAYER_APPROVED` payments where `expires_at <= now` with `SKIP LOCKED`, transitioning them to `EXPIRED`. This releases the partial unique index (`uq_one_active_checkout_per_invoice`), allowing customers to retry checkout on that invoice.
     - **Job B (Uncertain Capture Repair)**: Inspects payments stuck in `CAPTURE_PENDING` (or `REQUIRES_REVIEW`) older than `RECON_MIN_AGE_SECONDS` (120s). Only READS from PayPal (`get_order`, `get_capture`). Never calls create or capture. Reuses the canonical transactional `complete_capture` function. Handles amount mismatches (`REQUIRES_REVIEW` + `RECON_AMOUNT_MISMATCH`), declined captures (`FAILED`), pending captures older than 30m (`RECON_CAPTURE_STILL_PENDING`), orders without captures past 15m (`FAILED`), and unknown orders (`REQUIRES_REVIEW` + `RECON_ORDER_NOT_FOUND`). Emits `RECON_PAYPAL_UNAVAILABLE` alert after 5 consecutive 5xx/timeouts.
     - **Job C (Health & Invariant Monitoring)**: Read-only sweep raising operational alerts: `ALLOCATION_STUCK` (payments `COMPLETED` with allocation `PENDING` > 15m), `REVIEW_STUCK` (payments in `REQUIRES_REVIEW` > 1h), and `OUTBOX_DEAD_PRESENT` (dead outbox rows, rate-limited to at most once per hour per row).

---

## 2. Worker Execution

### Running Locally
```bash
# Outbox Worker
python -m app.workers.outbox_worker

# Reconciliation Worker
python -m app.workers.reconciliation_worker
```

### Running via Docker Compose
```bash
docker compose up -d outbox-worker reconciliation-worker
```

---

## 3. Configuration Parameters

The following environment variables configure the service and background workers (matching `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *Required* | Runtime asyncpg connection string (`payment_app` role, NO DELETE) |
| `MIGRATION_DATABASE_URL` | *Required* | DDL migration connection string (`payment_migrator` role) |
| `PAYPAL_ENV` | `sandbox` | PayPal environment (`sandbox` or `live`). Live forbidden in test |
| `PAYPAL_CLIENT_ID` | *Required* | PayPal REST API client ID |
| `PAYPAL_CLIENT_SECRET` | *Required* | PayPal REST API client secret |
| `PAYPAL_BASE_URL` | `https://api-m.sandbox.paypal.com` | Base URL for PayPal API or stub |
| `PAYPAL_WEBHOOK_ID` | `""` | PayPal Webhook ID for signature verification |
| `USER_SERVICE_URL` | `http://localhost:8000` | User Service base URL for JWKS & OAuth2 client credentials |
| `INVOICE_SERVICE_URL` | `http://localhost:8002` | Invoice Service base URL for C2/C3/C4 calls |
| `OUTBOX_POLL_INTERVAL_WORK` | `0.5` | Outbox worker poll delay when work was found (seconds) |
| `OUTBOX_POLL_INTERVAL_IDLE` | `2.0` | Outbox worker poll delay when queue is empty (seconds) |
| `OUTBOX_BATCH_SIZE` | `10` | Number of outbox events claimed per batch |
| `OUTBOX_VISIBILITY_TIMEOUT_SECONDS`| `60` | Deadline before an in-flight row becomes reclaimable |
| `OUTBOX_MAX_ATTEMPTS` | `10` | Maximum delivery attempts before moving event to `DEAD` |
| `OUTBOX_CIRCUIT_BREAKER_FAILURES` | `5` | Consecutive failures before tripping outbox circuit breaker |
| `OUTBOX_CIRCUIT_BREAKER_COOLDOWN_SECONDS` | `30` | Cooldown period before retrying after circuit trips |
| `RECON_INTERVAL_SECONDS` | `60` | Periodic interval between reconciliation cycles |
| `RECON_BATCH_SIZE` | `50` | Maximum rows inspected per reconciliation cycle |
| `RECON_MIN_AGE_SECONDS` | `120` | Minimum age before an uncertain capture is eligible for recon |
| `RECON_NO_CAPTURE_WINDOW_SECONDS` | `900` | Window (15m) before capture-pending payment with no capture is marked `FAILED` |
| `RECON_CAPTURE_PENDING_ALERT_SECONDS` | `1800` | Duration (30m) before pending capture alerts `RECON_CAPTURE_STILL_PENDING` |
| `RECON_ALLOCATION_STUCK_SECONDS` | `900` | Duration (15m) before pending allocation alerts `ALLOCATION_STUCK` |
| `RECON_REVIEW_STUCK_SECONDS` | `3600` | Duration (1h) before unresolved review alerts `REVIEW_STUCK` |

---

## 4. Outbox Response Handling Matrix (Section 3.5)

| Invoice Service Result | Worker Action |
|---|---|
| **200** with outcome `APPLIED` or `DUPLICATE_IGNORED` | In one transaction: row `status = 'DELIVERED'` (sets `delivered_at`), payment `allocation_status` transitioned from `PENDING` to `ALLOCATED`, audit log written. |
| **200** with outcome `REJECTED` (with `reason_code`) | In one transaction: row `status = 'DELIVERED'`, payment `allocation_status` transitioned from `PENDING` to `REJECTED`, audit row with `reason_code`, alert `ALLOCATION_REJECTED` (payment remains `COMPLETED`). |
| **200** with unknown or missing outcome | Treated as a retryable failure. Response body logged (truncated, sanitized, no secrets). Row remains `IN_FLIGHT` until next retry. |
| **400** / **422** (Contract violation / Malformed) | Row moved to `status = 'DEAD'` immediately (retries cannot succeed), `last_error` recorded, audit row written, alert `OUTBOX_REJECTED_BY_CONSUMER`. |
| **401** (Token expired or invalid) | Discard cached service token, fetch a new token from User Service, retry that row once in the same attempt. If second call fails with 401: fail attempt, back off, alert `OUTBOX_AUTH_FAILURE`. |
| **403** (Forbidden) | Attempt fails, back off exponentially, alert `OUTBOX_AUTH_FAILURE`. |
| **404** (Invoice not found) | Row moved to `status = 'DEAD'`, `last_error` recorded, audit row written, alert `OUTBOX_REJECTED_BY_CONSUMER`. |
| **429** (Rate Limited) | Attempt fails, back off exponentially. Honor `Retry-After` header if present and >= computed backoff. |
| **5xx** / Connect / Read Timeout | Attempt fails, back off exponentially with full jitter. Increment consecutive failure count toward circuit breaker. Alert `OUTBOX_CONSUMER_UNAVAILABLE` after 5 consecutive failures. |
| **Attempts >= 10** | Row moved to `status = 'DEAD'`, `last_error = 'Max retries reached'`, audit row written, alert `OUTBOX_DEAD`. |

---

## 5. System Alert Codes & Operator Actions

All alerts are logged as structured JSON at `ERROR` level with `alert: true`, the corresponding `alert_code`, and sanitized context identifiers (zero secrets or credentials):

| Alert Code | Meaning / Root Cause | Operator Action |
|---|---|---|
| `AMOUNT_MISMATCH` | Capture confirmed with amount/currency differing from payment record. | Check PayPal transaction details; manually verify with customer or issue refund if unauthorized. |
| `RECON_AMOUNT_MISMATCH` | Reconciliation found completed capture on PayPal with mismatched amount/currency. | Review order on PayPal portal and database row in `REQUIRES_REVIEW`. |
| `OUTBOX_PAYLOAD_INVALID` | Stored outbox event payload failed JSON Schema validation against `PaymentCaptured.v1.json`. | Check payload format in database; inspect code emitting outbox row; fix serializer bug. |
| `OUTBOX_AUTH_FAILURE` | Outbox worker received 401 (even after refresh) or 403 from Invoice Service. | Verify OAuth2 client credentials in User Service and audience permissions. |
| `OUTBOX_REJECTED_BY_CONSUMER` | Invoice Service rejected event with 400, 404, or 422. | Inspect `last_error` on outbox row; check if referenced invoice exists in Invoice Service. |
| `OUTBOX_CONSUMER_UNAVAILABLE` | 5 consecutive connection timeouts or 5xx errors calling Invoice Service. | Check Invoice Service health, networking, ingress proxies, and DNS resolution. |
| `OUTBOX_DEAD` | Outbox event failed 10 delivery attempts and was moved to `DEAD`. | Inspect `last_error` on outbox row; address root cause and reset `status = 'PENDING'` if fixable. |
| `ALLOCATION_REJECTED` | Invoice Service processed event but rejected allocation (e.g., invoice canceled/closed). | Inspect reason code; evaluate if manual customer refund is required. |
| `RECON_CAPTURE_STILL_PENDING` | PayPal capture has remained in `PENDING` status for > 30 minutes. | Check PayPal merchant dashboard for compliance holds, risk review, or payment disputes. |
| `RECON_NO_CAPTURE_AFTER_WINDOW` | Payment in `CAPTURE_PENDING` for > 15 minutes with no capture on PayPal; marked `FAILED`. | Normal timeout behavior for abandoned browser captures. No action needed unless elevated. |
| `RECON_ORDER_NOT_FOUND` | Order ID referenced in payment was not found on PayPal; placed in `REQUIRES_REVIEW`. | Investigate if order was created in wrong PayPal sandbox/environment or purged. |
| `RECON_PAYPAL_UNAVAILABLE` | 5 consecutive 5xx or timeout errors communicating with PayPal API. | Check PayPal API status page and service egress network connectivity. |
| `RECON_LATE_CAPTURE_AFTER_FAILED` | Capture arrived (via webhook or recon) for a payment already transitioned to terminal `FAILED`. | Investigate customer charge; initiate manual refund or customer account credit. |
| `ALLOCATION_STUCK` | Payment `COMPLETED` with `allocation_status = 'PENDING'` for > 15 minutes. | Check if outbox worker is running or if Invoice Service delivery is backlogged. |
| `REVIEW_STUCK` | Payment has remained in `REQUIRES_REVIEW` for > 1 hour without manual resolution. | Operations team must review the payment and transition to `COMPLETED` or `FAILED`. |
| `OUTBOX_DEAD_PRESENT` | Dead rows exist in `outbox_events` (rate-limited to once per hour per row). | Triage dead outbox rows using `outbox_events.last_error` and resolve blocking issues. |
