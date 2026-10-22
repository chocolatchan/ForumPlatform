# Task 031 – Reliable Outbound Event Email Dispatch: Design Document

**Date:** 2026-10-22  
**Branch:** `master`  
**Baseline commit:** `2313bd746957daf34a39c881a4f09caf4682ea49`

---

## 1. Objective

Add reliable, at-least-once outbound email dispatch to the Notification Service.
Every domain event that produces a `NotificationItem` may also enqueue an outbound
email into a durable PostgreSQL table (`notifications.email_deliveries`).
A background `EmailDispatchWorker` drains that queue with exponential backoff and a
configurable maximum-attempt dead-letter threshold.

---

## 2. Architectural Decisions

### 2.1 Single-table outbox inside the Notification Service

The `email_deliveries` table lives in the `notifications` schema alongside all other
Notification Service tables.  This keeps email dispatch within the same transaction
boundary as `ConsumedEvent` and `NotificationItem` creation, avoiding cross-service
two-phase-commit.

### 2.2 DEC-002 compliance for recipient email resolution

DEC-002 prohibits direct database joins across service boundaries.  When an event
payload does not carry the recipient's email address, the Notification Service
resolves it via a new internal HTTP endpoint:

```
GET /internal/users/{user_id}
X-Internal-Service-Key: <shared secret>
```

This endpoint is implemented in the User Service (`src/services/user/app.py`) and
protected by `verify_internal_service_key` middleware (`src/services/user/auth.py`).

### 2.3 Mandatory-security vs optional-activity emails

Each `EmailDelivery` row carries a `category` field set by the email template renderer:

| Category | Description | Bypasses `email_enabled` preference |
|---|---|---|
| `mandatory_security` | Account events: welcome, ban, unban, password reset | **Yes** |
| `optional_activity` | Community events: comment, best-answer | No |

### 2.4 Allowlist enforcement (fail-closed)

`notification_settings.is_recipient_allowed(email)` guards every dispatch attempt.
An empty `dev_email_allowlist` (or unrecognized recipient) causes immediate rejection
and promotes the row to `dead_letter` to prevent infinite retries on a known-bad
address.  The wildcard `*` token in the allowlist unblocks all recipients (production
use).

### 2.5 SMTP vs memory backend

The `email_delivery_backend` setting selects:

- `memory` – writes to `NotificationEmailSink` (global singleton) for unit tests.  
- `smtp` – dispatches via SMTP using `smtplib` in a thread executor (non-blocking).  
  Defaults to Mailpit on `localhost:1025` in dev.

---

## 3. New Components

| File | Purpose |
|---|---|
| `migrations/notifications/versions/20261022_0003_email_deliveries.py` | Alembic migration creating `notifications.email_deliveries` |
| `src/services/notification/models.py` | `EmailDelivery` ORM model added |
| `src/services/notification/config.py` | SMTP settings, allowlist helpers, worker tuning fields |
| `src/services/notification/email_worker.py` | `EmailDispatchWorker`, `dispatch_single_email`, `NotificationEmailSink`, backoff |
| `src/services/notification/templates.py` | HTML-safe email template renderers for each event category |
| `src/services/notification/events.py` | Extended to queue `EmailDelivery` rows per event |
| `src/services/notification/app.py` | Worker lifecycle (start/stop) wired to app `lifespan` |
| `src/services/user/auth.py` | `verify_internal_service_key` dependency |
| `src/services/user/app.py` | `GET /internal/users/{user_id}` endpoint |
| `tests/test_email_dispatch_worker.py` | 23-test suite covering worker logic and event handler |

---

## 4. EmailDelivery State Machine

```
pending
  │  (process_batch claims, sets status=sending, then dispatches)
  ├─► delivered  (SMTP OK or memory sink)
  └─► failed     (transient error, attempts < max_attempts, next_attempt_at += backoff)
        │
        └─► dead_letter  (max_attempts reached, or is_recipient_rejected=True)
```

Row locking: `SELECT … FOR UPDATE SKIP LOCKED` ensures multiple worker replicas
never double-deliver the same row.

---

## 5. Backoff Policy

```
delay = min(max_delay, base_delay × 2^(attempt-1)) × jitter
jitter ∈ [0.5, 1.5]
base_delay = 2 s, max_delay = 60 s
```

Approximate median retry schedule: 2 s → 4 s → 8 s → 16 s → 32 s → dead_letter.

---

## 6. Configuration Reference

| Setting | Default | Description |
|---|---|---|
| `smtp_host` | `localhost` | SMTP server hostname |
| `smtp_port` | `1025` | SMTP server port (Mailpit default) |
| `smtp_user` | `""` | SMTP authentication username |
| `smtp_password` | `""` | SMTP authentication password |
| `smtp_use_tls` | `False` | Direct TLS (port 465) |
| `smtp_starttls` | `False` | STARTTLS upgrade (port 587) |
| `email_from` | `notifications@forumplatform.local` | Envelope From address |
| `email_delivery_backend` | `memory` | `smtp` or `memory` |
| `dev_email_allowlist` | `developer@example.test,tester@example.test` | Comma-separated recipient allowlist; `*` = all |
| `email_worker_poll_interval` | `1.0` | Seconds between empty-queue polls |
| `email_worker_batch_size` | `10` | Max rows claimed per batch |
| `user_service_url` | `http://localhost:8001` | Base URL for internal email resolution |
| `internal_service_secret` | *(from common config)* | Shared secret for `X-Internal-Service-Key` |

---

## 7. Scope Exclusions

- No Social Service, friend lists, following, or DM email.
- No new public API endpoints beyond the internal User Service endpoint.
- No redesign of the Notification Service outbox worker for in-app notifications.
