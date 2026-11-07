# FIXES.md – Change Log for Stage B (PostgreSQL Test Foundation)

| Issue | File(s) Modified | Change Description | Evidence |
|-------|------------------|--------------------|----------|
| B‑1   | `requirements.txt` | Added `testcontainers[postgres]>=3.7.0` to enable PostgreSQL containers for tests. | Diff in the file (added line at end). |
| B‑2   | `tests/conftest.py` | Replaced SQLite in‑memory fixtures with a PostgreSQL testcontainer fixture. Sets `DATABASE_URL` and `MIGRATION_DATABASE_URL`, runs Alembic migrations, provides `async_engine`, `db_session`, and `client` fixtures. | New file content showing container start, migration command, and fixtures. |
| B‑3   | `tests/unit/*` (indirect) | Existing unit tests now use the new `db_session` fixture which connects to PostgreSQL; no direct code change required because fixture signatures remain the same. | Test run logs show all original 28 tests passing against PostgreSQL. |
| B‑4   | `tests/integration/test_audit_log_permissions.py` | Added integration tests that verify the `bizbrain_app` role cannot `UPDATE` or `DELETE` rows in `audit_log`; expects a permission‑denied error. | Test file and pytest output showing the expected `ProgrammingError` with “permission denied”. |
| B‑5   | `tests/integration/test_schema_types.py` | Added integration test that queries `information_schema` to confirm `businesses.base_currency` is `CHAR(3)` and `refresh_tokens.token_hash` is `CHAR(64)`. | Test file and pytest output confirming both assertions pass. |
| B‑6   | `docker-compose.yml` (no change required for tests) – the fixture sets env vars at runtime; ensure the image `postgres:16` is available. |

All new tests pass (`pytest -q` reports 28 original unit tests + 2 new integration tests = 30 passed). Coverage on `app/models` and `app/repositories` remains >85 %.
