# Email Sender Inventory

**Date:** 2026-10-24

**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`

**Base commit:** `b38a693a7e140584cdf7eae0e1e3f67945cdb328`

**Purpose:** Authoritative inventory for Task 031.3.E migration, with current-state
annotations from Task 031.3.H environment-aware routing.

## 1. Scope and Method

This inventory was rebuilt from the current worktree because the original Task
031.3.A inventory was missing. It covers application-level email producers,
dispatchers, workers, outbox handlers, configuration, and tests.

Repository scans performed:

```bash
grep -rlnE 'import smtplib|smtplib\.' src --include='*.py'
grep -rnE 'smtplib|aiosmtplib|sendgrid|mailgun|postmark|boto3|sendmail|send_message|SMTP\(' src tests scripts --include='*.py'
grep -rnE 'deliver_otp|dispatch_single_email|EmailDispatchWorker|EmailDelivery|otp_delivery_sink|notification_email_sink' src tests --include='*.py'
grep -rnE 'smtp|EMAIL_|mailpit|Mailpit' docs README.md docker-compose.yml .env.example .env.staging.example
```

No external email SDK or alternate application SMTP sender was found.

## 2. Verified Sender Inventory

### S-1 — User OTP / recovery / email-change sender

| Field | Value |
|---|---|
| Source | `src/services/user/otp_delivery.py` |
| Entry point | `async deliver_otp(recipient, code, purpose, timestamp)` |
| Original transport | Direct `smtplib.SMTP` / `smtplib.SMTP_SSL` in a removed `_send_smtp_email_sync`, executed through `asyncio.to_thread` |
| Shared adapter status | **Migrated** to `get_user_email_adapter()` in `src/services/user/otp_delivery.py` |
| Retry behavior | No retry inside the sender. Callers decide failure handling; requests remain anti-enumeration-safe. |
| Delivery-state semantics | No persistent delivery record. Registration/resend mark the pending challenge `delivery_failed` on error; recovery and email-change request paths invalidate/consume the newly created token/request on error. Successful SMTP delivery also writes the OTP to the in-memory test sink. |
| Recipient policy | No allowlist. The shared adapter routes every recipient to Mailpit in development/test and suppresses production `test.dev`/subdomains before SMTP. |
| Sensitive-data handling | Never logs plaintext OTP or message body. Logs recipient and rejection state only. |
| Memory backend | Writes to `otp_delivery_sink` when `EMAIL_DELIVERY_BACKEND=memory`. |

Verified triggers and call sites:

| Trigger | Route / workflow | Call site | Purpose |
|---|---|---|---|
| Registration | `POST /auth/register` | `src/services/user/app.py:299` | `registration` |
| Registration OTP resend | `POST /auth/register/resend-otp` | `src/services/user/app.py:522` | `registration` |
| Password recovery request and repeat/resend request | `POST /auth/recovery/request` | `src/services/user/app.py:1005` | `recovery` |
| Email-change request | `POST /profile/change-email/request` | `src/services/user/app.py:1419` | `email_change` |
| Email-change OTP resend | `POST /profile/change-email/resend-otp` | `src/services/user/app.py:1536` | `email_change` |

Recovery OTP verification and email-change verification consume the delivered
code but do not send email themselves.

Test coverage after migration:

- `tests/test_email_delivery.py`
  - No-allowlist delivery for recipients inside and outside `test.dev`.
  - `deliver_otp` routes through the shared adapter exactly once with unchanged subject, body, sender, and recipient.
  - Adapter timeout maps to `EmailDeliveryError` without a memory-sink write.
  - Adapter recipient rejection preserves `is_recipient_rejected`.
  - Production suppression maps to `EmailDeliveryError(is_suppressed=True)` with no sink write.
  - OTP and body content are absent from delivery logs.
  - Registration success and safe failure.
  - Registration resend success and failure-to-`delivery_failed`.
- `tests/test_user_auth_lifecycle.py`
  - Registration, resend cooldown/quota, recovery, recovery repeat/resend, and email-change flows using the memory sink.
- `tests/test_frontend_e2e.py`, `tests/test_e2e_integration.py`, `tests/test_security_hardening.py`
  - Workflow-level OTP and anti-enumeration coverage using the memory sink.
- `tests/common/email/test_adapter.py`, `tests/common/email/test_routing_policy.py`
  - Mailpit-only development/test routing, production external delivery, exact/case-insensitive suppression, lookalike domains, credential separation, and SMTP error behavior.

Remaining test gaps:

- Live Mailpit delivery through rebuilt containers was not re-run in Task E.
- The sender unit tests mock the adapter; actual SMTP socket behavior is covered only in adapter tests.
- Production external-SMTP delivery remains out of scope.

### S-2 — Notification outbox email sender

| Field | Value |
|---|---|
| Source | `src/services/notification/email_worker.py` |
| Entry point | `async dispatch_single_email(delivery: EmailDelivery)` called by `EmailDispatchWorker.process_batch()` |
| Original transport | Direct `smtplib.SMTP` / `smtplib.SMTP_SSL` in a removed `_send_smtp_email_sync`, executed with `loop.run_in_executor` |
| Shared adapter status | **Migrated** to `get_notification_email_adapter()` in `src/services/notification/email_worker.py` |
| Retry behavior | Outbox worker claims `pending`/`failed` rows with `SELECT FOR UPDATE SKIP LOCKED`, retries transient failures with exponential backoff plus jitter, and recovers expired `sending` leases. Recipient rejection dead-letters immediately. |
| Delivery-state semantics | `pending` → `sending` → `delivered` only after dispatch returns successfully. Failure → `failed` with `next_attempt_at`, or `dead_letter` when rejected/max attempts reached. At-least-once semantics: a crash after SMTP acceptance can redeliver. |
| Recipient policy | No allowlist. The shared adapter owns environment routing and production suppression; suppressed rows move immediately to `dead_letter`. |
| Sensitive-data handling | Logs recipient, status, attempt count, and error class/message; does not log rendered body content. |
| Memory backend | Writes to `notification_email_sink` when `EMAIL_DELIVERY_BACKEND=memory`. |
| Outbox integration | Email rows are queued by `src/services/notification/events.py`; dispatch is performed only by `EmailDispatchWorker`. |

Verified email-producing event triggers:

| Event | Handler | Email template | Category |
|---|---|---|---|
| `UserRegistered` | `_handle_user_registered` | Welcome | `mandatory_security` |
| `CommentCreated` | `_handle_comment_created` | Comment notification | `optional_activity` |
| `BestAnswerChanged` | `_handle_best_answer_changed` | Best answer | `optional_activity` |
| `ModerationActionApplied` | `_handle_moderation_action_applied` | Moderation notice | `mandatory_security` |
| `VoteChanged` | `_handle_vote_changed` | No email (`rendered_email=None`) | In-app notification only |

Test coverage after migration:

- `tests/test_email_dispatch_worker.py`
  - Memory sink delivery accepting every recipient.
  - Unknown backend behavior.
  - SMTP dispatch through the shared adapter exactly once with unchanged content.
  - Adapter timeout maps to `EmailWorkerDeliveryError` and outbox retry scheduling.
  - Adapter recipient rejection maps to immediate `dead_letter`.
  - Adapter suppression maps to immediate `dead_letter` with `delivered_at` unset.
  - Rendered body content is absent from delivery logs.
  - Outbox `pending` → `delivered` only after adapter success.
  - Transient failure → `failed` with future retry.
  - Max attempts → `dead_letter`.
  - Future `next_attempt_at` skipped.
  - Second worker poll does not redispatch a delivered row.
  - Worker lifecycle.
  - Event queueing and duplicate-event idempotency.
- `tests/test_email_worker_lease_pg.py`
  - Stale lease recovery, concurrent recovery, ambiguous SMTP outcomes, crash-after-accept redelivery. These are currently skipped without disposable PostgreSQL test configuration.
- `tests/common/email/test_adapter.py`, `tests/common/email/test_routing_policy.py`
  - Shared adapter success, failure, timeout, suppression, and exact-domain policy behavior.

Remaining test gaps:

- PostgreSQL lease tests were skipped because no disposable test database was configured.
- Live SMTP/Mailpit integration was not re-run against rebuilt containers.
- At-least-once delivery semantics still permit redelivery after a crash between SMTP acceptance and transaction commit; this is documented, not newly solved.

### S-3 — Shared SMTP transport

| Field | Value |
|---|---|
| Source | `src/common/email/adapter.py` |
| Status | Verified centralized transport; currently the only `smtplib` owner in `src/` |
| Factories | `get_user_email_adapter()`, `get_notification_email_adapter()` |
| Behavior | Async `send(EmailMessage)`, Mailpit-only development/test routing, production `test.dev`/subdomain suppression before transport, plain/SSL/STARTTLS SMTP, 10-second timeout, `EmailAdapterError`/`EmailSuppressedError` translation |
| Sender behavior | The transport uses its configured `EMAIL_FROM`; callers populate `EmailMessage.sender` with the same service sender for contract clarity. |

## 3. Configuration and Guard Binding

| Concern | User service | Notification service |
|---|---|---|
| Settings | `src/services/user/config.py` | `src/services/notification/config.py` |
| Backend | `EMAIL_DELIVERY_BACKEND` | `EMAIL_DELIVERY_BACKEND` |
| SMTP host/port | `SMTP_HOST` / `SMTP_PORT` | `SMTP_HOST` / `SMTP_PORT` |
| TLS | `SMTP_USE_TLS` / `SMTP_STARTTLS` | `SMTP_USE_TLS` / `SMTP_STARTTLS` |
| Sender | `EMAIL_FROM` | `EMAIL_FROM` |
| Recipient policy | Shared environment/suppression policy | Shared environment/suppression policy |
| Shared validator | `src/common/email/config.py` | `src/common/email/config.py` |
| Dev/test transport | `mailpit:1025` (Compose `DEV_SMTP_*`) | `mailpit:1025` (Compose `DEV_SMTP_*`) |
| Network guard | `scripts/network/smtp_egress_guard.sh` | `scripts/network/smtp_egress_guard.sh` |

## 4. Verified vs Assumed Findings

Verified by source inspection:

- After migration, only `src/common/email/adapter.py` imports `smtplib` under
  `src/`. Both application senders call that adapter.
- Both senders are invoked by existing service workflows, not by ad-hoc scripts.
- Notification email dispatch is reachable only through the outbox worker.
- Both senders use the shared adapter; recipient allowlists are no longer used.
- Development/test SMTP is pinned to Mailpit and production suppresses exact
  `test.dev` and its subdomains before transport.
- Suppressed Notification rows must not become `delivered`.
- No external email provider SDK or direct shell mail command is used by the
  application.

Assumed / not independently proven in this inventory:

- Third-party deployed code or out-of-repository jobs may send email; they are
  outside this repository audit.
- Runtime Mailpit delivery through currently running containers was not
  re-verified because those containers were not rebuilt or recreated.
- Production external-SMTP delivery is verified only with mocked transports.
- Live Mailpit acceptance after Task 031.3.H was not run because containers were
  not rebuilt or recreated.

## 5. Migration Checklist

| Sender | Migration result |
|---|---|
| S-1 User OTP | **Complete.** Uses `get_user_email_adapter()` and `EmailMessage`; subject/body, memory sink, async offload, safe-failure mapping, and suppression mapping preserved. |
| S-2 Notification outbox | **Complete.** Uses `get_notification_email_adapter()` and `EmailMessage`; outbox claiming, retries, dead-letter behavior, suppression dead-lettering, and success-only `delivered` state preserved. |
| S-3 Shared adapter | **Complete.** Centralized `smtplib`, API, configuration validation, Mailpit routing, and exact production domain suppression. |
