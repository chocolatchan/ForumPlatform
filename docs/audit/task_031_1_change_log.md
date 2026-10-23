# Task 031.1 – Change Log

**Date:** 2026-10-22
**Branch:** `master`
**Baseline commit:** `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` (uncommitted work-in-progress on top of this commit was reviewed, completed, and preserved)

---

## Database

### New migration: `migrations/notifications/versions/20261022_0004_email_delivery_lease.py`
- Adds nullable column `notifications.email_deliveries.sending_since`
  (`TIMESTAMP WITH TIME ZONE`) — the dispatch lease timestamp.
- Downgrade drops the column.
- Applied to the local PostgreSQL instance (`alembic current` → `notifications_0004 (head)`).

---

## Notification Service

### `src/services/notification/models.py`
- `EmailDelivery` gains `sending_since: Mapped[datetime | None]`.

### `src/services/notification/config.py`
- New setting `email_worker_lease_timeout_seconds` (default `300`, env
  `EMAIL_WORKER_LEASE_TIMEOUT_SECONDS`) — seconds before a `sending` row is
  treated as an orphaned lease.

### `src/services/notification/email_worker.py`
- **New `recover_stale_leases()`**:
  - `SELECT … WHERE status='sending' AND (sending_since <= cutoff OR sending_since IS NULL)`
    with `FOR UPDATE SKIP LOCKED`, ordered oldest-first, bounded by `batch_size`.
  - Recovered rows: lease cleared, `attempts += 1` (crashed attempt counted),
    `last_error` set; `failed` + exponential backoff, or `dead_letter` at
    `max_attempts` (terminal).
  - Docstring documents at-least-once semantics and the SMTP-acceptance
    ambiguity window (possible duplicate after crash mid-dispatch).
- **`_run_loop()`**:
  - Startup recovery wrapped in `try/except` (a database outage at boot no
    longer kills the worker loop; recovery retries on the periodic schedule).
  - Periodic recovery every `max(60 s, lease_timeout / 2)`.
- **`process_batch()`**:
  - Claim transaction sets `sending_since = now` alongside `status='sending'`.
  - Outcome write (delivered / failed / dead_letter) clears `sending_since`.
- Removed unused imports (`html`, `update`, `AsyncSession`); added `or_`.

---

## Tests

### New: `tests/test_email_worker_lease_pg.py` (12 PostgreSQL integration tests)
Covers: expired-lease recovery; active-lease preservation; concurrent recovery
exactly-once; dead-letter at max attempts; startup recovery; claim/lease
set-and-clear; no duplicate claim under concurrent `process_batch`; worker
restart with stale + pending rows; ambiguous SMTP outcome retry; crash after
SMTP acceptance recovery + re-dispatch; repeated-failure cycles to terminal
dead-letter; `NULL sending_since` orphan recovery.
Fixture is function-scoped so the engine binds to each test's event loop
(`asyncio_mode = auto` creates a fresh loop per test).

### `tests/test_email_dispatch_worker.py` (existing Task 031 suite, 23 tests)
- **Fixture pollution fix**: the `notif_db` fixture previously mutated the
  *shared* SQLAlchemy `MetaData` (all services share one instance) by stripping
  every table's schema without restoring it — this collided table names
  (`content.consumed_events` vs `notifications.consumed_events`) and broke
  `test_notification_service.py` / `test_avatar_email_change_pg.py` /
  `test_user_auth_lifecycle.py` in combined runs (pre-existing at baseline,
  order-dependent). The fixture now relies on `DatabaseSessionManager`'s
  ATTACH-based SQLite schema support (like `test_notification_service.py`),
  deleting `/tmp/sqlite_schemas` at setup, and never mutates shared metadata.
- Lint remediation: replaced `lambda` assignment with `def`; removed unused
  imports (`MagicMock`, `NotificationSettings`, `notification_settings`,
  `ConsumedEvent`, `NotificationItem`); import block sorted.

---

## Docker Compose

### `docker-compose.yml` — `notification-service`
- Added SMTP environment (previously absent — the service silently used the
  in-memory backend and could never deliver to Mailpit):
  `EMAIL_DELIVERY_BACKEND` (default `smtp`), `SMTP_HOST` (`mailpit`),
  `SMTP_PORT` (`1025`), `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`,
  `DEV_EMAIL_ALLOWLIST` (default `developer@example.test,tester@example.test` —
  fail-closed, no wildcard), `EMAIL_WORKER_LEASE_TIMEOUT_SECONDS` (`300`).
- Added `depends_on: mailpit: condition: service_healthy`.

### `.env.example`
- Documented `EMAIL_WORKER_LEASE_TIMEOUT_SECONDS=300`.

---

## Not Changed
- No changes to User/Content/Interaction/Moderation/Search services, gateway,
  their migrations, or Task 028 / 029 / 029.1 / 030 / 031 artifacts.
- No public email-status endpoint, no new message broker, no wildcard recipients.
- Nothing committed or pushed; all changes remain uncommitted in the working tree.
