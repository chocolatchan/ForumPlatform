# Email Sender Migration Report

**Task:** 031.3.E — Shared email adapter migration

**Date:** 2026-10-24

**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`

**Base commit:** `b38a693a7e140584cdf7eae0e1e3f67945cdb328`

**Status:** Source-level migration and verification complete

## 1. Executive Summary

The repository had two application-level email senders that imported `smtplib`
directly:

1. User OTP / recovery / email-change delivery in
   `src/services/user/otp_delivery.py`.
2. Notification outbox email dispatch in
   `src/services/notification/email_worker.py`.

Both now construct an `EmailMessage` and call the shared asynchronous adapter.
After migration, `smtplib` is imported by only one application module:

```text
src/common/email/adapter.py
```

No database schema, migration, production configuration, or container was
changed as part of Task E. No real email was sent and no external SMTP provider
was contacted.

## 2. Sender Migration Matrix

| Sender | Before | After | Preserved behavior |
|---|---|---|---|
| User OTP / recovery / email change | Direct `smtplib` helper in `otp_delivery.py`, run with `asyncio.to_thread` | `get_user_email_adapter()` + shared `EmailMessage` | Subject/body generation, recipient normalization, fail-closed allowlist, memory sink, anti-enumeration response handling, async offload, `EmailDeliveryError` contract |
| Notification outbox | Direct `smtplib` helper in `email_worker.py`, run with `loop.run_in_executor` | `get_notification_email_adapter()` + shared `EmailMessage` | Outbox-only dispatch, claiming/leases, retry/backoff, dead-letter decisions, success-only `delivered` state, memory sink, `EmailWorkerDeliveryError` contract |
| Shared transport | Existing adapter | Centralized `smtplib` owner with exact/`*`/`*@domain` allowlist support | Plain SMTP, SMTP_SSL, STARTTLS, authentication, 10-second timeout, adapter error translation |

## 3. Source Changes

### 3.1 User OTP sender

File: `src/services/user/otp_delivery.py`

- Removed the local `_send_smtp_email_sync` helper.
- Removed direct imports of `asyncio`, `smtplib`, and `email.message.EmailMessage`.
- Added imports from `src.common.email`:
  - `EmailAdapterError`
  - `EmailMessage`
  - `get_user_email_adapter`
- `deliver_otp` now:
  1. Normalizes the recipient.
  2. Applies the existing fail-closed allowlist preflight.
  3. Returns the existing memory-sink behavior when the backend is `memory`.
  4. Builds one shared `EmailMessage` and calls `adapter.send(...)` exactly once for SMTP.
  5. Maps `EmailAdapterError` to the established `EmailDeliveryError` without
     changing the recipient-rejection flag.
- The memory sink is written only after adapter success, preserving the previous
  "never report success after failed submission" behavior.

Preserved call sites and purposes:

| Workflow | Purpose |
|---|---|
| Registration | `registration` |
| Registration OTP resend | `registration` |
| Password recovery request / repeat request | `recovery` |
| Email-change request | `email_change` |
| Email-change OTP resend | `email_change` |

Recovery verification and email-change verification consume codes but do not
send email.

### 3.2 Notification outbox sender

File: `src/services/notification/email_worker.py`

- Removed the local `_send_smtp_email_sync` helper.
- Removed direct imports of `smtplib` and `email.message.EmailMessage`.
- Added imports from `src.common.email`:
  - `EmailAdapterError`
  - `EmailMessage`
  - `get_notification_email_adapter`
- `dispatch_single_email` now:
  1. Applies the existing fail-closed allowlist preflight.
  2. Returns the existing memory-sink behavior when the backend is `memory`.
  3. Builds one shared `EmailMessage` and calls `adapter.send(...)` exactly once for SMTP.
  4. Maps `EmailAdapterError` to the established `EmailWorkerDeliveryError`,
     preserving recipient-rejection semantics for immediate dead-lettering.
- `EmailDispatchWorker.process_batch` was not replaced or bypassed. It still owns
  claiming, leases, retries, backoff, and final delivery state.
- `delivered` is still written only after `dispatch_single_email` returns
  successfully.

### 3.3 Shared adapter

File: `src/common/email/adapter.py`

- Remains the only `smtplib` owner under `src/`.
- Added allowlist matching for the existing service contract:
  - exact address
  - global `*`
  - domain wildcard `*@domain`
- Existing adapter API, SMTP modes, timeout, sender selection, and error
  translation were retained.

## 4. Semantics Preserved

| Concern | Result |
|---|---|
| OTP generation and TTL | Unchanged |
| OTP / recovery / email-change content | Unchanged subject and body construction |
| Allowlist | Fail-closed preflight retained; adapter also enforces the same exact/wildcard contract |
| Registration failure handling | Pending challenge still transitions to `delivery_failed` |
| Resend failure handling | Pending challenge still transitions to `delivery_failed` |
| Recovery / email-change failure handling | Existing token/request invalidation paths unchanged |
| Notification outbox | Still the only dispatch path for notification email |
| Retry / backoff | Unchanged worker logic; adapter timeouts map to retryable worker errors |
| Recipient rejection | Maps to rejected error flags; notification worker dead-letters |
| Idempotency / duplicate dispatch | One adapter send per claimed row; no second dispatch path added |
| Delivery state | `delivered` still requires successful submission |
| Sensitive logging | No OTP, token, or message body added to logs |
| Memory backend | Existing sinks retained |

## 5. Test Coverage

Added or updated coverage proves:

- Shared-adapter success for both senders.
- Exactly one adapter dispatch per sender invocation / claimed outbox row.
- Adapter timeout propagation:
  - User sender → `EmailDeliveryError`, no sink write.
  - Notification worker → `EmailWorkerDeliveryError`, row remains `failed` with a future retry.
- Adapter recipient rejection:
  - User sender preserves rejected flag.
  - Notification worker dead-letters the row.
- Allowlist rejection occurs before adapter dispatch.
- No OTP / body content in logs.
- Outbox `pending` → `delivered` only after adapter success.
- Second worker poll does not redispatch a delivered row.
- Existing registration and resend failure-to-`delivery_failed` behavior.
- Adapter wildcard allowlist behavior.

Relevant test files:

- `tests/test_email_delivery.py`
- `tests/test_email_dispatch_worker.py`
- `tests/common/email/test_adapter.py`
- `tests/test_user_auth_lifecycle.py`
- `tests/test_security_hardening.py`
- `tests/test_email_worker_lease_pg.py`

## 6. Verification Commands and Results

### Relevant test suites

```bash
./venv/bin/pytest \
  tests/test_email_delivery.py \
  tests/test_email_dispatch_worker.py \
  tests/common/email \
  tests/test_user_auth_lifecycle.py \
  tests/test_security_hardening.py \
  -q
```

Result: **96 passed**

```bash
./venv/bin/pytest tests/test_email_worker_lease_pg.py -q
```

Result: **12 skipped** — disposable PostgreSQL test configuration is not present
in this environment.

### Ruff

```bash
./venv/bin/ruff check \
  src/services/user/otp_delivery.py \
  src/services/notification/email_worker.py \
  src/common/email/adapter.py \
  tests/test_email_delivery.py \
  tests/test_email_dispatch_worker.py \
  tests/common/email/test_adapter.py
```

Result: **All checks passed**

### Pyright

```bash
./venv/bin/pyright --pythonpath ./venv/bin/python \
  src/services/user/otp_delivery.py \
  src/services/notification/email_worker.py \
  src/common/email/adapter.py \
  tests/test_email_delivery.py \
  tests/test_email_dispatch_worker.py \
  tests/common/email/test_adapter.py
```

Result: **0 errors, 0 warnings, 0 informations**

### Whitespace check

```bash
git diff --check
```

Result: no output / no errors.

### Direct SMTP scan

```bash
grep -rlnE 'import smtplib|smtplib\.' src --include='*.py'
```

Result:

```text
src/common/email/adapter.py
```

## 7. Remaining Direct SMTP References

- `src/common/email/adapter.py` — intentional centralized transport.
- Tests monkeypatch `smtplib.SMTP` only to verify adapter behavior.
- Documentation and operational scripts reference SMTP/Mailpit but are not
  application senders.
- No external email SDK or shell mail sender was found in the repository.

## 8. Risks and Unverified Items

1. **Live Mailpit delivery was not re-run.** Task E did not rebuild or recreate
   containers, so the currently running User and Notification services may still
   use older images/entrypoints. Source-level adapter tests pass, but live
   container delivery remains unverified for this change.
2. **PostgreSQL lease tests were skipped.** Twelve tests require a disposable
   PostgreSQL test database. The worker source was not changed beyond the
   transport call, but database-backed lease behavior was not re-executed here.
3. **At-least-once delivery remains.** If a process crashes after SMTP accepts a
   message but before the `delivered` commit, the outbox can redeliver. This is
   existing outbox semantics, not a regression introduced by this migration.
4. **Adapter From-header contract.** The shared transport uses its configured
   `EMAIL_FROM` value. Callers populate `EmailMessage.sender` with the same
   service sender for contract clarity, but the transport currently reads the
   configured sender. No behavioral change was introduced.
5. **Third-party/out-of-repository senders** remain outside this audit.
6. **No production external-SMTP path** was configured or exercised.

## 9. Task E File Summary

Task E source/documentation changes:

- `src/services/user/otp_delivery.py`
- `src/services/notification/email_worker.py`
- `src/common/email/adapter.py`
- `tests/test_email_delivery.py`
- `tests/test_email_dispatch_worker.py`
- `tests/common/email/test_adapter.py`
- `docs/audit/email_sender_inventory.md`
- `docs/audit/email_sender_migration_report.md`

No commit, deployment, image rebuild, container recreation, or real email send
was performed. Existing unrelated worktree changes were left intact.
