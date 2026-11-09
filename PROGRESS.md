# Payment Service Implementation Progress

## Stage 1: Foundation, Skeleton, Database Security, Fake PayPal & Proof of Migrations
**Status:** COMPLETE (Reviewed & Verified on Real PostgreSQL)

---

## Stage 2: State Machine, Idempotency, Invoice Client (C2, C4), Order Creation (Staff & Public), Partial Unique Index Behavior & Expiry
**Status:** COMPLETE (Reviewed & Verified on Real PostgreSQL 16)

---

## Stage 3: Capture Flow, PayPal Capture, REQUIRES_REVIEW Handling, Transactional Completion & Outbox Insertion
**Status:** COMPLETE (Reviewed & Verified on Real PostgreSQL 16)

---

## Stage 4: Webhook Endpoint with Signature Verification & Idempotent Processing
**Status:** COMPLETE (Reviewed & Verified on Real PostgreSQL 16)

### What Changed in Stage 4
1. **PayPal Webhook Service (`app/services/webhook_service.py`)**:
   - **Signature Verification**: Verifies incoming webhooks using PayPal's `verify-webhook-signature` API.
   - **Forged Event Protection**: If verification fails, immediately returns HTTP 400, appends an immutable `AuditLog` row (scrubbed, no payload trust), and **does NOT** store the event in `payment_webhook_events`. Genuine events with the same event ID are never blocked by earlier forged attempts.
   - **Idempotent Replay**: Replayed `provider_event_id` is detected against `payment_webhook_events` and returns HTTP 200 immediately (`ALREADY_PROCESSED`) without duplicate effects.
   - **Event Correlation**: Automatically correlates events by `provider_capture_id`, `provider_order_id`, related order ID from `supplementary_data.related_ids.order_id`, or `custom_id`. Unmatched events are stored with `payment_id = NULL` and `processing_status = 'IGNORED'`.
   - **Event Type Processors**:
     - `CHECKOUT.ORDER.APPROVED`: Moves `CREATED` payment to `PAYER_APPROVED`. Out-of-order delivery after capture does not move status backward.
     - `PAYMENT.CAPTURE.COMPLETED`: Verifies amount and currency match our database record. If matching and payment is not already `COMPLETED`, applies the transactional completion path (status `COMPLETED`, allocation `PENDING`, updates `provider_capture_id`, writes audit log, and creates `PaymentCaptured` outbox row). Amount/currency mismatch transitions to `REQUIRES_REVIEW` and marks webhook `FAILED`.
     - `PAYMENT.CAPTURE.DENIED` / `DECLINED`: Transitions payment in `CAPTURE_PENDING` or `REQUIRES_REVIEW` to `FAILED` with status reason.
     - `PAYMENT.CAPTURE.REFUNDED` / `REVERSED`: Sets `COMPLETED` payment to `REFUNDED`, appends audit row, and raises structured alert log.
     - Unknown event types: Recorded with `processing_status = 'IGNORED'`.
2. **API Endpoint (`app/api/webhooks.py`)**:
   - `POST /api/v1/webhooks/paypal`: Pure signature-authorized route accepting raw payload and PayPal transmission headers.
3. **Concurrency & Test Verification**:
   - Webhook and capture endpoint racing concurrently to complete the same payment produces exactly **ONE** `PaymentCaptured` outbox row.
   - Test suite: **58 passed**, 0 failed, 0 flaky on real PostgreSQL.

---

### DO NOT Rules Verification Checklist
- [x] **DO NOT access any other service's database**: Webhook processing operates strictly on `payment_db`.
- [x] **DO NOT store any card data**: Verified. Zero card details stored or logged.
- [x] **DO NOT accept a "payment succeeded" claim without verification**: Webhooks are rejected unless PayPal cryptographically verifies the transmission headers.
- [x] **DO NOT store unverified webhook events in `payment_webhook_events`**: Unverified events are rejected with 400 and logged only to `audit_log` so forged events cannot block genuine ones.
- [x] **DO NOT create or modify invoice totals or status**: Invoice totals/statuses remain external to this service.
- [x] **DO NOT let AI agent or background shortcuts execute payments**: Webhooks strictly reflect provider-side customer payment events.
- [x] **DO NOT run as superuser**: All tests and services connect as `payment_app`.

---

### Outstanding Inquiries for Subsequent Stages
- [RESOLVED] **Schema File**: Added official `contracts/events/PaymentCaptured.v1.json` and 58 contract tests in `tests/unit/test_payment_captured_contract.py`.

---

## Inter-Stage Refactor: Merged Transactional Completion Path
**Status:** COMPLETE (Reviewed & Verified on Real PostgreSQL 16)

### What Changed
1. **Unified Completion Function (`app/services/completion_service.py`)**:
   - Extracted duplicated transactional completion logic from `capture_service.py` and `webhook_service.py` into a single canonical `complete_capture(...)` function.
   - **Row Locking**: Payment row is locked with `FOR UPDATE`.
   - **Idempotency**: If payment is already `COMPLETED`, returns the stored result with no side effects and no redundant writes.
   - **Late Capture After Failed**: If payment is terminal `FAILED` (e.g. late webhook arrival), logs warning, emits structured alert `RECON_LATE_CAPTURE_AFTER_FAILED`, does not throw, and leaves status `FAILED`.
   - **Amount & Currency Validation**: Strict check against payment record. Mismatch transitions payment to `REQUIRES_REVIEW`, stores raw response, creates **NO** outbox row, writes audit log, and raises alert `AMOUNT_MISMATCH`.
   - **State Machine Integration**: Clean transitions through `PAYER_APPROVED` -> `CAPTURE_PENDING` -> `COMPLETED` and `allocation_status = 'PENDING'`.
   - **Provider Reference Update**: Sets `provider_capture_id`, `provider_status = 'COMPLETED'`, and saves raw response.
   - **Single Outbox Insertion**: Atomically inserts `PaymentCaptured` event using `ON CONFLICT (event_type, aggregate_id) DO NOTHING`.
   - **Schema-Valid Payload**: Outbox payload strictly conforms to `contracts/events/PaymentCaptured.v1.json` (lowercase UUIDs, RFC 3339 UTC timestamp with trailing `Z`, formatted decimal string without sign or leading zeros, uppercase ISO currency, whitespace-free IDs).
2. **Alerting Infrastructure (`app/core/alerts.py`)**:
   - Structured JSON logging helper at `ERROR` level with `alert=True`, `alert_code`, context IDs, and sensitive field scrubbers.
   - Extensible `AlertNotifier` interface and test hook listeners.
3. **Capture & Webhook Services Updated**:
   - `capture_service.py` and `webhook_service.py` refactored to call `complete_capture(...)`. Duplicated code eliminated.
4. **Verification**:
   - Added `tests/integration/test_completion_service.py` (5 tests):
     - Payload built from real completed payment validates against `PaymentCaptured.v1.json`.
     - Capture then webhook delivers exactly one outbox row.
     - Webhook then capture delivers exactly one outbox row.
     - 20 trials of concurrent racing completions deliver exactly one outbox row each.
     - Amount/currency mismatch transitions to `REQUIRES_REVIEW`, creates no outbox row, and emits `AMOUNT_MISMATCH` alert.
   - All 58 existing tests plus 58 contract tests plus 5 completion tests pass (**121 passed**).


---

## Stage 5: Outbox Worker and Reconciliation Worker
**Status:** COMPLETE (Reviewed & Verified on Real PostgreSQL 16)

### What Changed in Stage 5
1. **Outbox Worker & Outbox Service (`app/workers/outbox_worker.py`, `app/services/outbox_service.py`)**:
   - **Short Claim Transaction**: Claims batches with `SELECT ... WHERE (status = 'PENDING' AND next_retry_at <= now) OR (status = 'IN_FLIGHT' AND next_retry_at <= now) ORDER BY next_retry_at, created_at LIMIT :batch FOR UPDATE SKIP LOCKED`. Sets `status = 'IN_FLIGHT'`, `attempts = attempts + 1`, `next_retry_at = now + VISIBILITY_TIMEOUT` (60s), and commits immediately.
   - **No Lock During HTTP**: HTTP calls to Invoice Service (`POST /internal/v1/payment-events`) execute strictly outside database transactions.
   - **Guarded Update**: Results recorded with `WHERE event_id = :id AND status = 'IN_FLIGHT'`. If another worker took over, logs and moves on without side effects.
   - **Pre-Send Schema Validation**: Validates payload against `contracts/events/PaymentCaptured.v1.json` before every send using compiled `jsonschema.Draft202012Validator` and `FormatChecker`. Invalid payloads move immediately to `DEAD` without sending, log validation errors, write audit rows, and emit alert `OUTBOX_PAYLOAD_INVALID`.
   - **Auth & Circuit Breaker**: Authenticates with OAuth2 client credentials from User Service; discards cached token and retries once on 401; fails attempt on repeated 401/403 (`OUTBOX_AUTH_FAILURE`). Circuit breaker trips after 5 consecutive failures, idling for 30s cooldown before resuming.
   - **Exhaustive Response Handling (Section 3.5)**:
     - 200 `APPLIED`/`DUPLICATE_IGNORED` -> `DELIVERED`, payment `allocation_status` to `ALLOCATED`.
     - 200 `REJECTED` -> `DELIVERED`, payment `allocation_status` to `REJECTED`, alerts `ALLOCATION_REJECTED`.
     - 400/404/422 -> `DEAD`, alerts `OUTBOX_REJECTED_BY_CONSUMER`.
     - 5xx/timeouts -> Exponential backoff with full jitter (capped at 600s). `DEAD` after 10 attempts (`OUTBOX_DEAD`).
2. **Reconciliation Worker & Reconciliation Service (`app/workers/reconciliation_worker.py`, `app/services/reconciliation_service.py`)**:
   - **Job A (Stale Checkout Expiry)**: Sweeps `CREATED` and `PAYER_APPROVED` payments where `expires_at <= now` with `SKIP LOCKED`. Transitions status to `EXPIRED` and writes audit row. Releases the partial unique index (`uq_one_active_checkout_per_invoice`), enabling customer checkout retries. Skips `CAPTURE_PENDING`.
   - **Job B (Uncertain Capture Repair)**: Sweeps payments in `CAPTURE_PENDING` (or `REQUIRES_REVIEW`) older than 120s. Strictly READ-ONLY with PayPal (`get_order`, `get_capture`). Reuses canonical `complete_capture`. Handles amount mismatch (`REQUIRES_REVIEW` + `RECON_AMOUNT_MISMATCH`), declined capture (`FAILED`), capture still pending > 30m (`RECON_CAPTURE_STILL_PENDING`), no capture after 15m window (`FAILED`), and order not found on PayPal (`REQUIRES_REVIEW` + `RECON_ORDER_NOT_FOUND`). Trips `RECON_PAYPAL_UNAVAILABLE` alert after 5 consecutive 5xx/timeouts.
   - **Job C (Health & Invariant Monitoring)**: Read-only sweep monitoring system health. Emits `ALLOCATION_STUCK` for payments in `COMPLETED` with allocation `PENDING` > 15m; `REVIEW_STUCK` for payments in `REQUIRES_REVIEW` > 1h; and `OUTBOX_DEAD_PRESENT` for dead outbox rows (rate-limited to at most once per hour per row).
3. **Pluggable Clock & Randomness (`app/core/clock.py`)**:
   - Implemented `Clock`, `SystemClock`, `TestClock`, `RandomSource`, `SystemRandomSource`, and `ControlledRandomSource`.
   - Enables 100% deterministic, zero-sleep, zero-flakiness tests.
4. **Configuration & Operational Hardening**:
   - Updated `app/core/config.py` and `.env.example` with worker intervals, visibility deadlines, circuit breaker, and retry settings.
   - Verified least-privilege `payment_app` database role has `DELETE` revoked on all tables including `outbox_events`.
   - Added Dockerfile and updated Docker Compose with worker services.
5. **Verification**:
   - Full test suite: **155 passing tests** across unit, integration, and API suites.
   - Zero flaky tests across 3 consecutive complete suite runs.
   - Service/worker layer coverage:
     - `completion_service.py`: 99%
     - `reconciliation_service.py`: 96%
     - `outbox_service.py`: 86%
     - `state_machine.py`: 97%
     - `outbox_worker.py`: 86%
     - `reconciliation_worker.py`: 89%
