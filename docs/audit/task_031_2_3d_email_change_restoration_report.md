# Task 031.2.3d – Email-Change Handler Restoration Report

**Date:** 2026-10-23
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`
**Type:** Source restoration (no deployment, no database writes)
**Reference:** `/tmp/opencode/user_app_container_4a1c1198a0ed.py`
**Target:** `src/services/user/app.py`
**Predecessors:** `docs/audit/task_031_2_3b_restoration_map.md`, `docs/audit/task_031_2_3c_recovery_restoration_report.md`

---

## 1. Baseline and Working-Tree State

### 1.1 Initial baseline

| Item | Value |
|---|---|
| Branch | `master` |
| HEAD | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| Target `app.py` before Task 031.2.3d | 1,362 lines |
| Reference | 1,848 lines |
| Reference SHA-256 | `7a1d3c6f4222703af6dcdb2bfde7ced1b7da10eb31f73b1e3efc46bbe51ea098` |
| Reference matches Task 031.2.3a hash | ✅ Yes |
| Reference modified by this task | ❌ No (hash identical after) |

### 1.2 Initial working-tree state

Task 031.1 modifications/untracked artifacts plus the Task 031.2.3c `app.py` recovery restoration were present. They were preserved:

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
?? docs/audit/task_031_2_3c_recovery_restoration_report.md
?? migrations/notifications/versions/20261022_0004_email_delivery_lease.py
?? tests/test_email_worker_lease_pg.py
```

### 1.3 Final working-tree state

`src/services/user/app.py` is now 1,789 lines. No staged changes. Final status is in §9.

---

## 2. Reference Integrity

- SHA-256 before: `7a1d3c6f4222703af6dcdb2bfde7ced1b7da10eb31f73b1e3efc46bbe51ea098`
- SHA-256 after: `7a1d3c6f4222703af6dcdb2bfde7ced1b7da10eb31f73b1e3efc46bbe51ea098`
- The reference was read only; it was not copied wholesale.

---

## 3. Routes and Dependencies Restored

### 3.1 Routes

| Route | Handler | Location | Previous state |
|---|---|---|---|
| `POST /profile/change-email/request` | `change_email_request` | `app.py:1111` | 501 stub |
| `POST /profile/change-email/resend-otp` | `change_email_resend_otp` | `app.py:1286` | Absent |
| `POST /profile/change-email/verify` | `change_email_verify` | `app.py:1393` | 501 stub |

Route-table assertion: each of the three paths is registered exactly **once**; handler names are `change_email_request`, `change_email_resend_otp`, `change_email_verify`. Route decorator count is **25** (24 previous + `resend-otp`).

All three routes retain `dependencies=[Depends(verify_csrf_header)]` and now also depend on `get_current_session` via `AuthenticatedContext`.

### 3.2 Dependencies added/changed

| Dependency | Change | Reason |
|---|---|---|
| `EmailChangeRequest` | Added to `src.services.user.models` import | Verification-challenge persistence |
| `rate_limiter_change_email_request` | New limiter, `app.py:133`, `max_requests=5` | Request throttling, matching reference |
| `rate_limiter_change_email_verify` | New limiter, `app.py:137`, `max_requests=10` | Verification throttling, matching reference |
| Test-hook resets | Two `.reset()` calls added | Keep `/test/reset-rate-limiters` complete |
| `not_implemented_response` | Import removed | No longer used after the last stubs were replaced |

`rate_limiter_resend_otp` is reused for the resend route (key `{client_ip}:resend_email_change_otp`), exactly as in the reference. No new schema, authentication path, delivery helper, or error-envelope helper was created. `IntegrityError` was already imported.

The two change-email limiter thresholds are hardcoded `5` and `10` because no corresponding `user_settings` fields exist and modifying `config.py` is outside this task's scope. This mirrors the reference.

---

## 4. Password Re-Authentication Contract Decision

### 4.1 Evidence

| Artifact | Statement |
|---|---|
| `src/docs/forum_public_api.openapi.yaml:421` | `required: [new_email, password]` |
| `docs/audit/task_030_design.md:63` | "Verify password (re-authentication)" in the email-change sequence |
| `docs/audit/task_030_design.md` security flow | Request step explicitly includes re-authentication before anti-enumeration and OTP issuance |
| `src/services/user/schemas.py:104–106` | `password: str \| None = None` (optional) |
| Reference `change_email_request` | Checks password only if `req.password is not None`; omission allowed |
| `docs/audit/task_031_2_3b_restoration_map.md` §2.6 | Flags the OpenAPI-vs-reference drift |

### 4.2 Conclusion

The discrepancy is real. The authoritative contract is the committed canonical OpenAPI plus the Task 030 design: **the current password is required**. The reference's optional behavior is drift, not an intentional decision.

### 4.3 Implementation choice

The handler now enforces the contract without editing the OpenAPI file or the Pydantic schema (both outside this task's allowed file scope):

- Omitted `password` → `422 VALIDATION_ERROR` ("Current password is required for email change."), consistent with the documented required-field validation response.
- Wrong password → `400 INVALID_PASSWORD`.
- Correct password → continues.

No OpenAPI document was modified, and the password was **not** made optional. The committed serialization schema still declares the field optional, so the auto-generated FastAPI document continues to show it as optional until a separately authorized schema task tightens `ChangeEmailRequest.password`. The hand-written canonical specification remains the contract of record and behavior now matches it.

---

## 5. OTP Lifecycle, Cooldown, Quota, Uniqueness, and Transactions

### 5.1 Request

- Rate limit key `{client_ip}:change_email_request`, `max_requests=5`.
- Re-authentication as described in §4.
- Manual email-format validation → `422 VALIDATION_ERROR`.
- Same-as-current email → `400 EMAIL_UNCHANGED`.
- Email owned by another account → neutral `202`, no challenge created, no OTP delivered (DEC-001 anti-enumeration).
- Transaction: `Account … FOR UPDATE` → active `EmailChangeRequest … FOR UPDATE`.
- Existing active challenge: 60-second cooldown and quota `>= 3` produce a neutral `202` without a new OTP.
- Prior active challenges are marked `completed_at=now`; a new challenge is inserted with:
  - 6-digit OTP, TTL 600 s, stored as a SHA-256 hash,
  - `attempts=0`,
  - `resend_count` carried forward (or 0),
  - `resend_available_at = now + 60 s`.
- `deliver_otp(recipient=new_email, purpose="email_change")` runs after commit. On `EmailDeliveryError` the new record is marked completed so no usable orphan OTP remains.
- No OTP or hash is returned.

### 5.2 Resend

- Reuses `rate_limiter_resend_otp` under key `{client_ip}:resend_email_change_otp`.
- Transaction: `Account … FOR UPDATE` → active `EmailChangeRequest … FOR UPDATE`.
- Missing account → `404 USER_NOT_FOUND`; no active challenge → `400 NO_ACTIVE_EMAIL_CHANGE`; cooldown → `429 RESEND_COOLDOWN_ACTIVE`; quota `>= 3` → `400 RESEND_LIMIT_EXCEEDED`; expired → `400 REQUEST_EXPIRED`.
- Regenerates a 600-second OTP, resets `otp_hash`/`expires_at`, increments `resend_count`, and sets a fresh 60-second cooldown.
- Delivery failure is logged but the new OTP is retained and the endpoint still returns `200 {"status":"sent"}`. This is inherited reference behavior and is recorded as a risk.

### 5.3 Verify

- Rate limit key `{client_ip}:change_email_verify`, `max_requests=10`.
- Transaction: `Account … FOR UPDATE` → active `EmailChangeRequest … FOR UPDATE`.
- Expiry (`OTP_EXPIRED`), attempt cap of 5 (`OTP_ATTEMPTS_EXCEEDED`), constant-time `verify_otp_hash`, and immediate completion on the 5th failure.
- Wrong OTP increments `attempts` and commits the increment (`INVALID_OTP`).
- Successful OTP:
  - re-checks `Account.email_normalized` for the target address excluding the current account,
  - existing duplicate → `409 EMAIL_ALREADY_IN_USE` and challenge completed,
  - otherwise updates `Account.email`, `Account.email_normalized`, `Account.email_verified_at`, and marks the challenge `verified_at`/`completed_at` in the same transaction.
- Successful verification consumes the challenge exactly once by setting `completed_at`; a replay finds no active challenge.
- No session revocation is performed, matching the reference and Task 030 design.
- Response is the private profile with `avatar_url` derived from the existing `Account.avatar_object_key`; avatar routes/handlers were not restored.

### 5.4 Uniqueness and database guarantees

`Account.email_normalized` has a database unique constraint (`uq_accounts_email_normalized`). Application checks provide early, friendly errors; the constraint remains the final concurrency guard. The verify handler maps a commit-time `IntegrityError` to the documented `409 EMAIL_ALREADY_IN_USE` instead of allowing a 500. The catch is specific to `IntegrityError`; no broad exception handling was introduced.

On such a conflict the transaction rolls back, so the challenge remains active. The client can retry or request a different address. This is a minor residual behavior, not a duplicate-email exposure.

---

## 6. Lock Order and Concurrency Review

### 6.1 Restored lock order

| Handler | Lock order |
|---|---|
| `change_email_request` | `Account` → `EmailChangeRequest` |
| `change_email_resend_otp` | `Account` → `EmailChangeRequest` |
| `change_email_verify` | `Account` → `EmailChangeRequest` |

### 6.2 Correction 1 – verify inversion

The reference verify handler locked `EmailChangeRequest` first and then `Account`, while request and resend lock `Account` first. That is a confirmed inversion with deadlock potential between a concurrent request/resend and verify for the same account.

Correction: verify now locks the authenticated `Account` row first, then the active `EmailChangeRequest`. All other logic and error paths are unchanged. This is narrow and does not change the public contract.

### 6.3 Correction 2 – cross-account duplicate lock

The reference additionally took `FOR UPDATE` on the *other* account that owns the target email. If two accounts concurrently try to claim each other's addresses, that produces a cross-account lock cycle (A→B vs B→A).

Correction: the duplicate-email read in verify no longer takes `FOR UPDATE`. The read still gives the normal `409`, and the unique constraint plus the `IntegrityError` → `409` mapping handles the true race. This removes the only second-order account lock in the workflow.

### 6.4 Residual concurrency notes

- Request's anti-enumeration duplicate read is intentionally unlocked; a race there can still create a challenge for an address that becomes taken, and verification then fails with `409`.
- The `IntegrityError` path rolls back attempt/completion updates along with the failed email update.
- No PostgreSQL concurrency test was run in this task (see §8), so these corrections are reasoned and statically validated, not proven under real row-level locking.

---

## 7. Tests and Static Checks Executed

All commands ran from the repository root using `./venv`.

| Check | Command | Result |
|---|---|---|
| Ruff | `./venv/bin/ruff check src/services/user/app.py` | **All checks passed** (exit 0) |
| Pyright | `./venv/bin/pyright --pythonpath venv/bin/python src/services/user/app.py` | **0 errors, 0 warnings, 0 informations** (exit 0) |
| Dirty-diff whitespace | `git diff --check` | Clean (exit 0) |
| Narrow tier-1 regression | `pytest -q tests/test_tier1_api.py::test_unimplemented_endpoints_return_501_not_implemented` | **1 passed in 2.14s** |
| Tier-1 API file | `pytest -q tests/test_tier1_api.py` | **4 passed in 2.31s** |
| Recovery lifecycle | `pytest -q tests/test_user_auth_lifecycle.py::test_password_recovery_and_reset_lifecycle` | **1 passed in 2.76s** |
| Auth lifecycle file | `pytest -q tests/test_user_auth_lifecycle.py` | **14 passed in 7.72s** |
| Combined regression re-run | `pytest -q tests/test_tier1_api.py tests/test_user_auth_lifecycle.py` | **18 passed in 8.90s** |
| Route assertion | Imported `app`; checked required paths | All three email routes + recovery routes + internal route present; each email route exactly once; no avatar paths |

The tier-1 test named above still asserts that unauthenticated `POST /profile/change-email/request` returns `401 UNAUTHORIZED` (not `501`); it passes after restoration. No existing test was modified.

---

## 8. Tests Not Executed and Why

| Test | Reason |
|---|---|
| `tests/test_avatar_email_change_pg.py` (including `test_email_change_anti_enumeration`, `test_email_change_otp_attempts_lockout`, `test_concurrent_email_change_verification`) | PostgreSQL integration tests that write to a database, and they target the live stack. Explicitly excluded by this task. These are the only existing email-change workflow tests. |
| `tests/test_browser_e2e.py::test_browser_avatar_and_email_change_lifecycle` | Requires a rebuilt/live service and browser stack; deployment is out of scope, and the test covers avatar as well. |
| Full repository test suite | Includes the excluded PG/live-stack suites. |
| New in-process email-change lifecycle test | None exists, and this task does not add tests. The successful workflow was therefore validated statically and through the tier-1/recovery regressions, not end-to-end in-process. |

No concurrency-safety claim is made from sequential tests.

---

## 9. Diff-Scope Summary

`app.py` grew from 1,362 to 1,789 lines (net +427). Cumulative working-tree diff for the file (including the previously authorized Task 031.2.3c recovery work) is:

```
src/services/user/app.py | 816 ++++++++++++++++++++++++++++++++++++++++++++++-
1 file changed, 816 insertions(+), 12 deletions(-)
```

Task 031.2.3d's own changes:

- 3 handlers: 2 stubs replaced, 1 new resend route/handler.
- 2 new rate-limiter instances and 2 test-hook resets.
- 1 model import added (`EmailChangeRequest`).
- 1 unused import removed (`not_implemented_response`).
- 2 narrow concurrency corrections in verify (Account-first lock; no lock on the duplicate account) and `IntegrityError` → `409`.
- 1 required-password guard in the request handler.

Verified:

| Check | Result |
|---|---|
| Only `src/services/user/app.py` newly modified by this task | ✅ |
| No new files besides this report | ✅ |
| Three email-change routes registered exactly once | ✅ |
| `resend-otp` registered exactly once | ✅ |
| Recovery handlers from Task 031.2.3c intact | ✅ (`recovery_request` 731, `recovery_verify` 852, `recovery_reset` 976) |
| `GET /internal/users/{user_id}` intact and protected | ✅ (route 1761, dependency 1763, handler 1765) |
| Avatar routes/handlers remain absent | ✅ (no `/profile/avatar`, `set_avatar`, `delete_avatar`) |
| Task 031.1 / Notification / Compose / env / migrations modified | ❌ None |
| Schemas, config, or OpenAPI modified | ❌ None |
| Containers touched | ❌ None |
| Database accessed/written | ❌ None |
| Staged or committed | ❌ None |

The other modified/untracked files in `git status` are pre-existing Task 031/031.1 work and audit artifacts; their contents were not changed by this task.

---

## 10. Recovery and Internal Endpoint Confirmation

- Password recovery routes and handlers restored in Task 031.2.3c remain present and unchanged: `/auth/recovery/request`, `/auth/recovery/verify`, `/auth/recovery/reset`.
- `GET /internal/users/{user_id}` remains registered exactly once with `Depends(verify_internal_service_key)`.
- `verify_internal_service_key` remains imported and used.
- The recovery lifecycle test still passes.

---

## 11. Remaining Risks and Recommended Next Task

### Remaining risks

1. **No PostgreSQL/workflow test run for email change.** The only email-change tests require the live PG stack. Lock corrections and the `IntegrityError` path are unproven under real concurrency.
2. **Generated OpenAPI still shows `password` optional.** Behavior now requires it, matching the canonical hand-written specification, but `ChangeEmailRequest.password` in `schemas.py` remains `str | None`. A separately authorized schema task should tighten it.
3. **Resend delivery failure still reports success.** The reference keeps the new OTP and returns `200 {"status":"sent"}` on `EmailDeliveryError`; the user may be told a code was sent when delivery failed.
4. **Resend limiter is shared with registration resend.** The reference reuses `rate_limiter_resend_otp`; a burst of registration resends can consume the email-change resend budget. Deciding whether to split it is a config/design task.
5. **No audit/outbox event is emitted for email changes**, matching the reference.
6. **6-digit OTPs are stored as unsalted SHA-256 hashes**, inherited from the existing design; online attempts are capped, but an offline dump is brute-forceable.
7. **Running container remains stale.** `forum_user_service` still uses the preserved `4a1c1198a0ed` image; none of the restored source is deployed.

### Recommended next task

**Task 031.2.3e — restore avatar handlers and profile `avatar_url` fields**, plus resolve the Content Service `X-Internal-Key` vs `X-Internal-Service-Key` mismatch. After source restoration is complete, a separate rebuild/recreate task should run the PostgreSQL concurrency suites, Mailpit delivery checks, and the browser E2E journey.
