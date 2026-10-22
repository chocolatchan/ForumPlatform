# Task 031 – Change Log

**Date:** 2026-10-22  
**Branch:** `master`  
**Baseline commit:** `2313bd746957daf34a39c881a4f09caf4682ea49`

---

## Database

### New migration: `migrations/notifications/versions/20261022_0003_email_deliveries.py`
- Creates `notifications.email_deliveries` table with columns:
  `id`, `event_id`, `recipient_id`, `recipient_email`, `subject`,
  `body_text`, `body_html`, `category`, `status`, `attempts`, `max_attempts`,
  `next_attempt_at`, `last_error`, `created_at`, `delivered_at`.
- Unique constraint `uq_email_delivery_event_recipient` on `(event_id, recipient_id)`.
- Indexes on `status` and `next_attempt_at` for efficient worker queries.
- Migration applied and verified via `\d notifications.email_deliveries` in psql.

---

## Notification Service

### `src/services/notification/models.py`
- Added `EmailDelivery` ORM class mapped to `notifications.email_deliveries`.
- Imported `EmailDelivery` in `metadata_` line to ensure table is included in schema.

### `src/services/notification/config.py`
- Added SMTP fields: `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`,
  `smtp_use_tls`, `smtp_starttls`, `email_from`.
- Added `email_delivery_backend` (default `"memory"`).
- Added `dev_email_allowlist` with `get_allowed_recipients()` and `is_recipient_allowed()` helpers.
- Added `email_worker_poll_interval` and `email_worker_batch_size`.

### `src/services/notification/email_worker.py` *(new file)*
- `EmailWorkerDeliveryError` exception with `recipient` and `is_recipient_rejected` fields.
- `SentNotificationEmail` frozen dataclass.
- `NotificationEmailSink` in-memory sink for unit tests with `deliver()`, `get_messages()`, `clear()`.
- `notification_email_sink` module-level singleton.
- `_send_smtp_email_sync()` synchronous SMTP dispatcher supporting plain, STARTTLS, and SSL/TLS.
- `dispatch_single_email()` async dispatcher enforcing allowlist then routing to backend.
- `calculate_email_backoff()` exponential backoff with jitter.
- `EmailDispatchWorker` class: `start()`, `stop()`, `_run_loop()`, `process_batch()`.
  Uses `SELECT … FOR UPDATE SKIP LOCKED` for safe concurrent worker scaling.

### `src/services/notification/templates.py` *(new file)*
- `RenderedEmail` dataclass with `subject`, `body_text`, `body_html`, `category`.
- `render_welcome_email(username)` → category `mandatory_security`.
- `render_moderation_email(action, reason)` → category `mandatory_security`.
- `render_comment_notification_email(title, preview)` → category `optional_activity`.
- `render_best_answer_email()` → category `optional_activity`.
- All user-controlled fields HTML-escaped.

### `src/services/notification/events.py`
- Added imports for `get_auth_http_client`, `notification_settings`, `EmailDelivery`, template renderers.
- `EventHandlerRegistry.process_event()` extended:
  - After creating `NotificationItem`, evaluates `rendered_email` from handler result.
  - Mandatory-security emails bypass `email_enabled` preference gate.
  - Optional-activity emails queued only when `email_enabled=True`.
  - If `recipient_email` not in event payload, fetches via `_fetch_recipient_email()`.
  - Creates `EmailDelivery` row with `status="pending"` within the same transaction.
  - Records `email_delivery_id` in `ConsumedEvent.metadata_`.
- Added `_fetch_recipient_email(user_id)` class method using internal HTTP endpoint.
- `_handle_user_registered`: passes `recipient_email` from event data and `render_welcome_email`.
- `_handle_comment_created`: passes `render_comment_notification_email`.
- `_handle_best_answer_changed`: passes `render_best_answer_email`.
- `_handle_vote_changed`: passes `rendered_email=None` (upvote-only, no email).
- `_handle_moderation_action_applied`: passes `render_moderation_email`.

### `src/services/notification/app.py`
- `lifespan` startup: creates `EmailDispatchWorker` and calls `worker.start()`.
- `lifespan` shutdown: calls `worker.stop()`.

---

## User Service

### `src/services/user/auth.py`
- Added `verify_internal_service_key(request: Request)` FastAPI dependency.
  Reads `X-Internal-Service-Key` header and compares to `user_settings.internal_service_secret`.
  Returns HTTP 403 on mismatch.

### `src/services/user/app.py`
- Added `GET /internal/users/{user_id}` endpoint protected by `verify_internal_service_key`.
- Returns: `id`, `username`, `display_name`, `email`, `role`, `is_banned`.
- Returns HTTP 404 when user not found.

---

## Tests

### `tests/test_email_dispatch_worker.py` *(new file — 23 tests, all pass)*

| Section | Tests |
|---|---|
| `TestCalculateEmailBackoff` | Base delay range, growth with attempts, max_delay cap, always positive (4 tests) |
| `TestNotificationEmailSink` | Deliver & retrieve, filter by recipient, case-insensitive filter, clear (4 tests) |
| `TestDispatchSingleEmail` | Memory backend sink, allowlist rejection, unknown backend error (3 tests) |
| `test_process_batch_*` | Delivered, failed-with-retry, dead_letter-on-rejection, dead_letter-on-max-attempts, skip future rows, returns 0 when empty (6 tests) |
| `test_worker_*` | Start/stop lifecycle, start idempotency (2 tests) |
| Event handler integration | UserRegistered queues email, optional email skipped when disabled, mandatory security email queued regardless of pref, duplicate event idempotency (4 tests) |

---

## Documentation

- `docs/audit/task_031_design.md` — architecture and design decisions.
- `docs/audit/task_031_change_log.md` — this file.
- `docs/audit/task_031_validation_report.md` — test results and verification evidence.
