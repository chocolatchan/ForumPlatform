# Task 031.2.3c – Password Recovery Handler Restoration Report

**Date:** 2026-10-23 (local +0700)
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`
**Type:** Source restoration (no deployment, no database writes)
**Reference:** `/tmp/opencode/user_app_container_4a1c1198a0ed.py`
**Target:** `src/services/user/app.py`
**Predecessor map:** `docs/audit/task_031_2_3b_restoration_map.md`

---

## 1. Baseline and Working-Tree State

### 1.1 Initial baseline

| Item | Value |
|---|---|
| Branch | `master` |
| HEAD | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| Target `app.py` before edits | 985 lines, SHA-256 `13638d198c6e0d440270246856ddb6a3f4c7312ecb0787102f310a14cea87581` |
| Reference before edits | 1,848 lines, SHA-256 `7a1d3c6f4222703af6dcdb2bfde7ced1b7da10eb31f73b1e3efc46bbe51ea098` |
| Reference matches Task 031.2.3a hash | ✅ Yes |

The reference was read only and still has the same SHA-256 after the task.

### 1.2 Initial working-tree state

Pre-existing Task 031/031.1 changes and audit artifacts were present and left untouched:

```
 M .env.example
 M docker-compose.yml
 M src/services/notification/config.py
 M src/services/notification/email_worker.py
 M src/services/notification/models.py
 M tests/test_email_dispatch_worker.py
?? docs/audit/task_031_1_change_log.md
?? docs/audit/task_031_1_design.md
?? docs/audit/task_031_1_validation_report.md
?? docs/audit/task_031_2_1_inventory_report.md
?? docs/audit/task_031_2_2_discrepancy_report.md
?? docs/audit/task_031_2_3a_image_preservation_report.md
?? docs/audit/task_031_2_3b_restoration_map.md
?? migrations/notifications/versions/20261022_0004_email_delivery_lease.py
?? tests/test_email_worker_lease_pg.py
```

### 1.3 Final working-tree state

`src/services/user/app.py` is now 1,362 lines. The only newly modified tracked file from this task is that file. The final status is reported in §9.

---

## 2. Restored Functions and Dependencies

### 2.1 Handlers restored

| Route | Handler | Restored at | Replaced |
|---|---|---|---|
| `POST /auth/recovery/request` | `recovery_request` | `app.py:721` | 501 stub |
| `POST /auth/recovery/verify` | `recovery_verify` | `app.py:842` | 501 stub |
| `POST /auth/recovery/reset` | `recovery_reset` | `app.py:966` | 501 stub |

The recovery section comment was updated from the placeholder heading to `# --- Account Password Recovery & Reset (ADR-003, ADR-015, DEC-001) ---`.

### 2.2 Dependencies added

| Dependency | Change | Reason |
|---|---|---|
| `OneTimeToken` | Added to `src.services.user.models` import | Recovery OTP and reset-ticket persistence |
| `generate_reset_token` | Added to `src.services.user.security` import | 256-bit reset-ticket generation |
| `rate_limiter_recovery_request` | New limiter, `app.py:121` | Recovery request throttling from existing settings |
| `rate_limiter_recovery_verify` | New limiter, `app.py:125` | Verification throttling from existing settings |
| `rate_limiter_recovery_reset` | New limiter, `app.py:129` | Reset throttling from existing settings |
| Test-hook resets | Added three `.reset()` calls | Keep `/test/reset-rate-limiters` complete for in-process tests |

### 2.3 Dependencies intentionally not added

| Dependency | Why not |
|---|---|
| `httpx` | Only the avatar handlers need it; avatar is out of scope |
| `EmailChangeRequest` / change-email limiters | Email-change restoration is out of scope |
| `SetAvatarRequest` | Avatar restoration is out of scope |

All other dependencies used by the recovery handlers were already committed and imported: `Account`, `PasswordHistory`, `Session`, `deliver_otp`, `EmailDeliveryError`, `ensure_utc`, `generate_otp`, `hash_secret`, `hash_password`, `verify_otp_hash`, `verify_password`, `get_client_ip`, `SlidingWindowRateLimiter`, `user_db`, `error_response`, and the recovery config values.

No duplicate authentication or response-envelope path was introduced. `not_implemented_response` remains imported because the two email-change stubs still use it.

---

## 3. Security and Concurrency Review

### 3.1 Lock acquisition order (documented before copying)

| Handler | Lock order (reference) | Lock order (restored) |
|---|---|---|
| `recovery_request` | Account → OneTimeToken | Account → OneTimeToken |
| `recovery_verify` | Account → OneTimeToken | Account → OneTimeToken |
| `recovery_reset` | **OneTimeToken (ticket) → Account** | **Account → OneTimeToken (ticket)** |

### 3.2 Adjustment made

The reference `recovery_reset` locked the reset ticket first, then the account, while the other two handlers lock the account first. That is a confirmed lock-order inversion between verify and reset, with deadlock potential under concurrent recovery operations for the same account.

A narrowly scoped correction was applied:

1. Outside the transaction, read the ticket's `account_id` with a plain `SELECT` (no lock).
2. Inside the transaction, lock `Account` first (`FOR UPDATE`).
3. Re-read the ticket under `FOR UPDATE`, filtered by hash, purpose, account, and `consumed_at IS NULL`.
4. Validate expiry and continue with the original logic.

The pre-read snapshot is **not trusted for state**: the ticket is re-read under lock and all original checks remain. The public contract, error codes, status codes, token lifetime, password policy, history append, and session revocation are unchanged. Concurrent resets with the same ticket still serialize on the account and only one can consume the ticket.

```python
# Pre-read owner; ticket is re-read under FOR UPDATE below.
owner_stmt = select(OneTimeToken.account_id).where(
    OneTimeToken.token_hash == ticket_hash,
    OneTimeToken.purpose == "password_reset_ticket",
    OneTimeToken.consumed_at.is_(None),
)
...
acc_stmt = select(Account).where(Account.id == ticket_account_id).with_for_update()
...
tok_stmt = select(OneTimeToken).where(..., account_id == ticket_account_id, ...).with_for_update()
```

### 3.3 Controls preserved

- Anti-enumeration neutral `202` behavior for unknown/banned/cooldown/quota-exhausted recovery requests.
- OTP generation (`generate_otp`, 900 s TTL) and SHA-256 storage via `hash_secret`.
- Constant-time OTP comparison via `verify_otp_hash`.
- Attempt cap of 5; expiry handling; prior-token invalidation.
- Single-use reset ticket with 10-minute TTL, stored as a hash of a 256-bit token.
- Current-password and last-5-history reuse prevention.
- Full session revocation on successful reset (`revocation_reason="password_reset"`).
- Existing transaction boundaries for token creation/consumption, password update, history append, and session revocation.
- No OTP, reset token, password hash, or other secret is returned in responses; the reset token is returned only by verify, as designed.
- No broad exception handling was added. The only caught exception is the reference's specific `EmailDeliveryError` path in `recovery_request`.

### 3.4 Not claimed by this task

Sequential lifecycle success does **not** establish concurrency safety. The lock-order correction was not exercised against PostgreSQL in this task (see §5).

---

## 4. Tests and Static Checks Executed

All commands ran from the repository root using the established `./venv` environment.

| Check | Command | Result |
|---|---|---|
| Ruff | `./venv/bin/ruff check src/services/user/app.py` | **All checks passed** (exit 0) |
| Pyright | `./venv/bin/pyright --pythonpath venv/bin/python src/services/user/app.py` | **0 errors, 0 warnings, 0 informations** (exit 0) |
| Focused lifecycle test | `./venv/bin/pytest -q tests/test_user_auth_lifecycle.py::test_password_recovery_and_reset_lifecycle` | **1 passed in 2.52s** |
| Auth lifecycle regression | `./venv/bin/pytest -q tests/test_user_auth_lifecycle.py` | **14 passed in 7.31s** |
| Diff whitespace | `git diff --check` | Clean (exit 0) |
| Route assertion | Imported `app` and checked required paths | All present, including `/internal/users/{user_id}` and all three recovery routes |

### 4.1 Test-name discrepancy

The task specified:

`tests/test_user_auth_lifecycle.py::test_password_recovery_and_set_reset_lifecycle`

The actual committed test name is:

`tests/test_user_auth_lifecycle.py::test_password_recovery_and_reset_lifecycle`

(no `set_`). The requested node ID does not exist. The actual recovery lifecycle test was run and passed. No test file was modified or substituted.

### 4.2 What the passing lifecycle test covers

The test uses in-process `ASGITransport` with SQLite and the memory OTP backend. It verifies:

- Neutral `202` for unknown and known accounts.
- Recovery OTP delivery to the test sink.
- Wrong OTP → `400 INVALID_OTP`.
- Valid OTP → `200` with reset token.
- Replayed OTP → `400`.
- Password reuse → `400 PASSWORD_REUSE_FORBIDDEN`.
- Successful reset → `204`.
- Replayed reset ticket → `400 INVALID_RESET_TOKEN`.
- Session revoked after reset.
- Old password login fails; new password login succeeds.

---

## 5. Tests Not Executed

| Test | Reason |
|---|---|
| `tests/test_recovery_concurrency_pg.py` | Per task rules, PostgreSQL integration tests that write to a database were excluded. It also targets the running gateway/user-service container, which was not rebuilt or restarted and still contains the old image. |
| `tests/test_avatar_email_change_pg.py` | Out of scope; avatar and email-change handlers are not restored. It also requires the live stack and DB writes. |
| `tests/test_browser_e2e.py` recovery journey | Requires a rebuilt/live service and Mailpit; deployment is explicitly out of scope. |
| Ruff/Pyright on other files | Only `src/services/user/app.py` changed. |
| Full repository test suite | Would include live-stack and out-of-scope tests; not required for this task. |

No concurrency-safety claim is made from the sequential lifecycle test.

---

## 6. Diff-Scope Summary

`git diff --stat -- src/services/user/app.py`:

```
src/services/user/app.py | 387 ++++++++++++++++++++++++++++++++++++++++++++++-
1 file changed, 382 insertions(+), 5 deletions(-)
```

Scope of the change:

- 2 import additions (`OneTimeToken`, `generate_reset_token`).
- 3 recovery rate-limiter instances.
- 3 reset calls in the test-only limiters hook.
- 3 stub handlers replaced with real implementations.
- 1 section comment updated.
- The reset handler differs from the reference only by the documented lock-order correction (pre-read + Account-first lock + re-read under `FOR UPDATE`).

Verified unchanged:

| Check | Result |
|---|---|
| `GET /internal/users/{user_id}` present | ✅ `app.py:1331–1368` |
| `get_internal_user_contact` present | ✅ |
| `verify_internal_service_key` import/dependency present | ✅ `app.py:29, 1333` |
| Email-change handlers still stubs | ✅ `change_email_request` / `change_email_verify` unchanged |
| Avatar routes/handlers absent | ✅ `grep` for `/profile/avatar` returns nothing |
| `resend-otp` route absent | ✅ |
| `@app.*` route count | 24 (unchanged; no new routes) |
| Task 031.1 files modified | ❌ None |
| Notification Service / Compose / env / migrations modified | ❌ None |
| Containers touched | ❌ None |
| Database accessed/written | ❌ None |
| Reference file modified | ❌ None (hash unchanged) |

The six other modified tracked files in `git status` are the pre-existing Task 031.1 changes; their diffs were not touched by this task.

---

## 7. Internal Recipient-Resolution Endpoint Confirmation

`GET /internal/users/{user_id}` remains intact:

- Route registration at `src/services/user/app.py:1331`.
- `Depends(verify_internal_service_key)` at line 1333.
- Handler `get_internal_user_contact` at line 1335.
- `verify_internal_service_key` still imported at line 29.
- Route-table assertion confirmed the path is registered after restoration.

No structural change was made to this endpoint or its response fields.

---

## 8. Remaining Risks and Recommended Next Task

### Remaining risks

1. **No PostgreSQL concurrency validation yet.** The Account→token lock-order correction is reasoned and narrow, but the live PostgreSQL concurrency tests were not run. They require a rebuilt/recreated User Service and DB writes.
2. **Running container is stale.** The running `forum_user_service` still uses the preserved `4a1c1198a0ed` image; it does not contain this source change.
3. **Email-change and avatar functionality remain missing/stubbed.** Two email-change routes still return 501, `resend-otp` is absent, and avatar routes are absent.
4. **Inherited recovery design risks remain** (from the 031.2.3b map): 6-digit OTP hashes are offline-brute-forceable if the DB is exposed; recovery delivery failure handling covers only `EmailDeliveryError`; reset does not prune `PasswordHistory`; no audit/outbox event is emitted.
5. **No deployment or live recovery flow has been exercised.** Source-level and in-process evidence only.

### Recommended next task

**Task 031.2.3d — restore the three email-change handlers (request, resend-otp, verify) only**, following the same pattern:

- Import `EmailChangeRequest`.
- Add the two change-email rate limiters and their test-hook resets.
- Replace the two stubs and add the missing resend route.
- Resolve the OpenAPI-vs-schema question about whether the re-authentication password is required.
- Do not restore avatar or deploy.

A later, separate task should handle avatar restoration plus the `X-Internal-Key` / `X-Internal-Service-Key` decision, and only after source restoration is complete should the service be rebuilt and the PostgreSQL concurrency tests plus Mailpit/browser flows be run.

---

## 9. Final Working-Tree Status

```
 M .env.example
 M docker-compose.yml
 M src/services/notification/config.py
 M src/services/notification/email_worker.py
 M src/services/notification/models.py
 M src/services/user/app.py
 M tests/test_email_dispatch_worker.py
?? docs/audit/task_031_1_change_log.md
?? docs/audit/task_031_1_design.md
?? docs/audit/task_031_1_validation_report.md
?? docs/audit/task_031_2_1_inventory_report.md
?? docs/audit/task_031_2_2_discrepancy_report.md
?? docs/audit/task_031_2_3a_image_preservation_report.md
?? docs/audit/task_031_2_3b_restoration_map.md
?? migrations/notifications/versions/20261022_0004_email_delivery_lease.py
?? tests/test_email_worker_lease_pg.py
```

This report (`docs/audit/task_031_2_3c_recovery_restoration_report.md`) is the only additional untracked file created by this task.

No staging, commit, push, rebuild, restart, migration, database write, or deployment was performed.
