# Task 031 – Validation Report: Reliable Outbound Event Email Dispatch

**Date:** 2026-10-22  
**Branch:** `master`  
**Baseline commit:** `2313bd746957daf34a39c881a4f09caf4682ea49`

---

## 1. Pre-Implementation Checks

| Check | Result |
|---|---|
| `git status` — no unintended staged changes | ✅ Confirmed |
| HEAD == `2313bd746957daf34a39c881a4f09caf4682ea49` | ✅ Confirmed |
| Prior task work (029, 029.1, 030) preserved | ✅ No prior files overwritten |

---

## 2. Database Migration Verification

```
\d notifications.email_deliveries
```

Columns present: `id`, `event_id`, `recipient_id`, `recipient_email`, `subject`,
`body_text`, `body_html`, `category`, `status`, `attempts`, `max_attempts`,
`next_attempt_at`, `last_error`, `created_at`, `delivered_at`.

Constraints:
- Primary key: `id` (UUID)
- Unique: `(event_id, recipient_id)` → `uq_email_delivery_event_recipient`
- Indexes: `status`, `next_attempt_at`, `recipient_id`

Migration status: `alembic upgrade head` applied cleanly. ✅

---

## 3. Unit & Integration Test Results

### Task 031 Email Worker Suite (`tests/test_email_dispatch_worker.py`)

```
23 passed in 1.34 s
```

| Test | Result |
|---|---|
| `test_first_attempt_returns_near_base_delay` | ✅ PASSED |
| `test_increases_with_attempts` | ✅ PASSED |
| `test_capped_at_max_delay` | ✅ PASSED |
| `test_always_positive` | ✅ PASSED |
| `test_deliver_and_retrieve` | ✅ PASSED |
| `test_filter_by_recipient` | ✅ PASSED |
| `test_filter_case_insensitive` | ✅ PASSED |
| `test_clear` | ✅ PASSED |
| `test_memory_backend_delivers_to_sink` | ✅ PASSED |
| `test_allowlist_rejection_raises_error` | ✅ PASSED |
| `test_unknown_backend_raises_value_error` | ✅ PASSED |
| `test_process_batch_delivers_pending_email` | ✅ PASSED |
| `test_process_batch_marks_failed_on_delivery_error` | ✅ PASSED |
| `test_process_batch_dead_letter_on_recipient_rejected` | ✅ PASSED |
| `test_process_batch_dead_letter_on_max_attempts_reached` | ✅ PASSED |
| `test_process_batch_skips_future_next_attempt_at` | ✅ PASSED |
| `test_process_batch_returns_zero_when_empty` | ✅ PASSED |
| `test_worker_start_stop_lifecycle` | ✅ PASSED |
| `test_worker_start_idempotent` | ✅ PASSED |
| `test_user_registered_queues_email_delivery` | ✅ PASSED |
| `test_email_not_queued_when_email_disabled_for_optional_activity` | ✅ PASSED |
| `test_moderation_email_queued_regardless_of_preference` | ✅ PASSED |
| `test_duplicate_event_is_idempotent` | ✅ PASSED |

### Existing Notification Service Regression (`tests/test_notification_service.py`)

```
4 passed in 2.16 s
```

No regressions introduced by Task 031 changes. ✅

---

## 4. Behavioural Verification

### 4.1 Allowlist enforcement (fail-closed)

- Empty `dev_email_allowlist` → `get_allowed_recipients()` returns `set()`.
- `is_recipient_allowed("")` → `False`. Confirmed by unit test and code review.
- `EmailWorkerDeliveryError(is_recipient_rejected=True)` immediately promotes to `dead_letter`.

### 4.2 Mandatory-security bypass of `email_enabled` preference

- `render_moderation_email` returns `category="mandatory_security"`.
- `EventHandlerRegistry.process_event` checks `is_mandatory = rendered_email.category == "mandatory_security"`.
- When `is_mandatory=True`, `should_send_email=True` regardless of `email_enabled` preference.
- Verified by `test_moderation_email_queued_regardless_of_preference`.

### 4.3 Duplicate event idempotency

- `ConsumedEvent` unique constraint on `(event_id, recipient_id)` blocks second insert.
- `EventHandlerRegistry.process_event` returns `[]` on duplicate.
- Only one `EmailDelivery` row exists after two identical event submissions.
- Verified by `test_duplicate_event_is_idempotent`.

### 4.4 Concurrent claim safety

- `SELECT … FOR UPDATE SKIP LOCKED` in `process_batch` prevents two workers from
  claiming the same row.
- Status is set to `"sending"` inside the claim transaction before SMTP dispatch,
  so any crash between claim and update results in a stuck `"sending"` row (not double-sent).

> [!NOTE]
> Stuck `"sending"` rows (e.g. after worker crash) are not automatically recovered in
> this implementation. A recovery job (or a `sending` → `pending` rollback on startup)
> is deferred to a future hardening task.

### 4.5 Internal email resolution (DEC-002 compliance)

- `GET /internal/users/{user_id}` requires `X-Internal-Service-Key` matching
  `user_settings.internal_service_secret`.
- Missing or wrong key returns HTTP 403.
- User not found returns HTTP 404.
- Notification Service uses `notification_settings.internal_service_secret` (from common config)
  and `notification_settings.user_service_url` to construct the request.

---

## 5. Known Limitations / Deferred Work

| Item | Priority | Notes |
|---|---|---|
| `"sending"` row recovery on worker restart | Medium | Rows stuck in `sending` after crash are not retried automatically |
| Mailpit integration smoke test (live SMTP) | Low | Requires Docker; manually verified separately |
| Email delivery status API endpoint | Low | Not in scope for Task 031; useful for debugging |
| Wildcard domain allowlist (`*@example.test`) | Medium | Logic present in config; not exercised by tests |

---

## 6. Files Changed (Task 031 scope)

**New files:**
- `migrations/notifications/versions/20261022_0003_email_deliveries.py`
- `src/services/notification/email_worker.py`
- `src/services/notification/templates.py`
- `tests/test_email_dispatch_worker.py`
- `docs/audit/task_031_design.md`
- `docs/audit/task_031_change_log.md`
- `docs/audit/task_031_validation_report.md`

**Modified files:**
- `src/services/notification/models.py` — `EmailDelivery` ORM model
- `src/services/notification/config.py` — SMTP + worker config
- `src/services/notification/events.py` — email queuing in event handler
- `src/services/notification/app.py` — worker lifecycle
- `src/services/user/auth.py` — internal key verification
- `src/services/user/app.py` — internal user endpoint

---

## 7. Verdict

**Task 031 implementation is complete and verified.**  
All 23 new tests and 4 pre-existing notification tests pass.  
No scope creep; no prior task work was overwritten.
