# Task 031.2.3b – User Service Restoration Map

**Date:** 2026-10-23 (local +0700)
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`
**Type:** Strictly read-only comparison and restoration planning
**Committed source:** `src/services/user/app.py` (HEAD)
**Preserved reference:** `/tmp/opencode/user_app_container_4a1c1198a0ed.py`
**Predecessors:**
- `docs/audit/task_031_2_2_discrepancy_report.md`
- `docs/audit/task_031_2_3a_image_preservation_report.md`

> No application code, tests, migrations, configuration, database, or containers were changed. No tests were executed. The reference file was read only. This document proposes a restoration sequence; it does not perform it.

---

## 1. Baseline and Integrity Evidence

### 1.1 Git baseline

| Item | Value |
|---|---|
| Branch | `master` |
| HEAD | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| Staged changes | None |
| Pre-existing modified files | `.env.example`, `docker-compose.yml`, `src/services/notification/{config,email_worker,models}.py`, `tests/test_email_dispatch_worker.py` |
| Pre-existing untracked files | 3 × Task 031.1 audit docs, `task_031_2_1_inventory_report.md`, `task_031_2_2_discrepancy_report.md`, `task_031_2_3a_image_preservation_report.md`, `migrations/notifications/versions/20261022_0004_email_delivery_lease.py`, `tests/test_email_worker_lease_pg.py` |
| Stashes / extra branches / extra worktrees | None / none / none |

No Git state was changed during this analysis.

### 1.2 File integrity

| File | Lines | SHA-256 |
|---|---:|---|
| `src/services/user/app.py` (committed worktree) | 985 | `13638d198c6e0d440270246856ddb6a3f4c7312ecb0787102f310a14cea87581` |
| `/tmp/opencode/user_app_container_4a1c1198a0ed.py` (reference) | 1,848 | `7a1d3c6f4222703af6dcdb2bfde7ced1b7da10eb31f73b1e3efc46bbe51ea098` |

The reference SHA-256 **matches** the value recorded in Task 031.2.3a (`7a1d3c6f…a098`). The reference is unchanged.

### 1.3 Structural identity checks

| Check | Committed source | Reference |
|---|---|---|
| `@app.*` route decorators | 24 | 26 |
| `not_implemented_response` occurrences | 6 (import + 5 stub bodies) | 1 (unused import only) |
| Recovery handlers | 3 stubs | 3 real |
| Change-email handlers | 2 stubs | 3 real |
| Avatar handlers | 0 | 2 real |
| `GET /internal/users/{user_id}` | Present | Absent |
| Imports `verify_internal_service_key` | Yes | No |

The reference is therefore **not a superset** of committed source and must be merged selectively.

---

## 2. Route-by-Route Comparison

All reference line numbers refer to `/tmp/opencode/user_app_container_4a1c1198a0ed.py`. All committed line numbers refer to the current worktree `src/services/user/app.py`.

### 2.1 `POST /auth/recovery/request`

| Aspect | Reference implementation | Committed source |
|---|---|---|
| Location | `recovery_request`, lines 868–987 | `recovery_request`, lines 703–705 |
| Status | Real | 501 placeholder |
| Request | `RecoveryRequest` → `{email: str}` | Same schema |
| Success response | `202` `{"status": "accepted", "message": "If the account exists, ..."}` | None (501) |
| Auth / CSRF | Public; no session, no CSRF | Same route signature |
| Rate limit | `rate_limiter_recovery_request`, key `"{client_ip}:recovery_request"` → `429 RATE_LIMIT_EXCEEDED` | Limiter not defined |
| Data read | `Account` by `email_normalized`; `OneTimeToken` latest active `password_recovery` | — |
| Data written | Invalidates prior active `password_recovery` tokens (`consumed_at`); inserts a new token | — |
| Transaction / locks | Read session (no lock); then transaction with `Account … FOR UPDATE`, token query `FOR UPDATE`, bulk update, insert | — |
| Token parameters | 6-digit OTP (`generate_otp(ttl_seconds=900)`), SHA-256 hash, `resend_after = now + 60s`, `resend_count` carried over | — |
| Anti-enumeration | Unknown, banned, cooldown-active, or resend-quota-exhausted all return the same neutral `202`; no OTP delivered | — |
| Email | `deliver_otp(recipient, code, purpose="recovery", timestamp=now)`; on `EmailDeliveryError` the new token is marked consumed in a second transaction | — |
| Side effects | Token creation, OTP delivery, invalidation of previous tokens | — |
| Errors | `429 RATE_LIMIT_EXCEEDED`; unhandled delivery errors beyond `EmailDeliveryError` would propagate | — |

**Note:** `resend_count` is only incremented when a new recovery request actually issues a token; the first token has `resend_count = 0`, and the quota is `>= 3`.

### 2.2 `POST /auth/recovery/verify`

| Aspect | Reference implementation | Committed source |
|---|---|---|
| Location | `recovery_verify`, lines 989–1111 | `recovery_verify`, lines 708–710 |
| Status | Real | 501 placeholder |
| Request | `RecoveryVerifyRequest` → `{email, otp: 6 digits}` | Same schema |
| Success response | `200` `{"reset_token": str, "expires_at": ISO-8601}` | None (501) |
| Auth / CSRF | Public; no session, no CSRF | Same route signature |
| Rate limit | `rate_limiter_recovery_verify`, key `"{client_ip}:recovery_verify"` → `429` | Limiter not defined |
| Data read | `Account … FOR UPDATE` by normalized email; latest active `password_recovery` token `FOR UPDATE` | — |
| Data written | Consumes recovery token; invalidates prior `password_reset_ticket` tokens; inserts a new ticket | — |
| Token parameters | Reset ticket = `generate_reset_token()` (256-bit URL-safe), stored as `hash_secret(token)`, TTL 10 minutes | — |
| Locks / order | Transaction: **Account → OneTimeToken** | — |
| Errors | `INVALID_OTP` (unknown/banned account, no active token, wrong OTP), `OTP_EXPIRED`, `OTP_ATTEMPTS_EXCEEDED` (when already at limit), `429` | — |
| Attempt behavior | Wrong OTP increments `attempts`; on the 5th wrong attempt the token is consumed but the response code is still `INVALID_OTP`; a subsequent attempt returns `OTP_ATTEMPTS_EXCEEDED` | — |
| Side effects | Single-use recovery OTP consumed; single-use reset ticket issued; prior tickets invalidated | — |
| Email | None | — |

### 2.3 `POST /auth/recovery/reset`

| Aspect | Reference implementation | Committed source |
|---|---|---|
| Location | `recovery_reset`, lines 1113–1220 | `recovery_reset`, lines 713–715 |
| Status | Real | 501 placeholder |
| Request | `ResetPasswordRequest` → `{reset_token, new_password (min 8)}` | Same schema |
| Success response | `204 No Content` | None (501) |
| Auth / CSRF | Public; no session, no CSRF | Same route signature |
| Rate limit | `rate_limiter_recovery_reset`, key `"{client_ip}:recovery_reset"` → `429` | Limiter not defined |
| Data read | `OneTimeToken … FOR UPDATE` by `token_hash` + purpose + unconsumed; `Account … FOR UPDATE`; last 5 `PasswordHistory` rows | — |
| Data written | Consumes ticket; appends current password hash to `PasswordHistory`; updates `Account.password_hash` and `password_changed_at`; bulk-revokes **all** active `Session` rows | — |
| Locks / order | Transaction: **OneTimeToken (ticket) → Account** (opposite order to verify) | — |
| Password policy | Rejects reuse of the current password and the last 5 history hashes (`verify_password`); no additional complexity check beyond schema `min_length=8` | — |
| Session effects | All sessions revoked with `revocation_reason="password_reset"` (rows updated, not deleted) | — |
| Errors | `INVALID_RESET_TOKEN` (missing/used), `RESET_TOKEN_EXPIRED`, `PASSWORD_REUSE_FORBIDDEN`, `429` | — |
| Side effects | Password change, history append, full session revocation, one-time ticket consumption | — |

### 2.4 `PUT /profile/avatar`

| Aspect | Reference implementation | Committed source |
|---|---|---|
| Location | `set_avatar`, lines 743–836 | **Absent** |
| Status | Real | No route, no handler |
| Request | `SetAvatarRequest` → `{image_id: UUID}` | Schema exists (`schemas.py:113`) but unused |
| Success response | `200` full private profile: `{id, username, display_name, bio, email, role, created_at, avatar_url}` | — |
| Auth / CSRF | `get_current_session`; `Depends(verify_csrf_header)` | — |
| Rate limit | None | — |
| External call | `POST {content_service_url}/internal/attachments/{image_id}/claim-avatar`, JSON `{user_id}`, header `X-Internal-Key`, 5 s timeout | — |
| Error mapping | Content `404` → `404 ATTACHMENT_NOT_FOUND`; `403` → `403 FORBIDDEN_ATTACHMENT_CLAIM`; `409` → `409 INVALID_ATTACHMENT_STATUS`; other non-200 → `500 CONTENT_SERVICE_ERROR`; connection/other exception → `502 SERVICE_UNAVAILABLE` | — |
| Data written | `Account.avatar_object_key = str(image_id)` | — |
| Transaction / locks | External claim happens **before** DB transaction; then `Account … FOR UPDATE`; update; commit | — |
| Old-avatar handling | If a different previous avatar exists, best-effort `POST …/release-avatar` **after** commit; failures only logged | — |
| Side effects | Content attachment status → `avatar`; old attachment → `deleted` (best effort); account avatar key updated | — |

**Dependency conflict:** the reference sends `X-Internal-Key`, while `src/services/content/auth.py:149–167` reads `X-Internal-Service-Key`. Content Service allows a missing key only when `environment` is `development` or `test`, which masks the mismatch locally. In `staging`/`production`, the claim/release calls would receive `401`. See §4.

### 2.5 `DELETE /profile/avatar`

| Aspect | Reference implementation | Committed source |
|---|---|---|
| Location | `delete_avatar`, lines 838–865 | **Absent** |
| Status | Real | No route, no handler |
| Request | No body | — |
| Success response | `204 No Content` | — |
| Auth / CSRF | `get_current_session`; `Depends(verify_csrf_header)` | — |
| Rate limit | None | — |
| Data written | `Account.avatar_object_key = None` | — |
| Transaction / locks | `Account … FOR UPDATE` in a transaction; commit before external call | — |
| External call | If an old key existed, best-effort `POST …/release-avatar`; failures logged; response body/status ignored | — |
| Errors | `401`/`403` from auth/CSRF only; external release failure does not change the `204` | — |
| Side effects | Account avatar cleared; content attachment marked `deleted` (best effort) | — |

### 2.6 `POST /profile/change-email/request`

| Aspect | Reference implementation | Committed source |
|---|---|---|
| Location | `change_email_request`, lines 1223–1389 | `change_email_request`, lines 718–722 |
| Status | Real | 501 placeholder |
| Request | `ChangeEmailRequest` → `{new_email: str, password: str \| None}` | Same schema defined |
| Success response | `202` neutral: `{"status": "accepted", "message": "If the email is valid and available, ..."}` | None (501) |
| Auth / CSRF | `get_current_session`; `Depends(verify_csrf_header)` | Route has CSRF dependency, no session dependency |
| Rate limit | `rate_limiter_change_email_request` (hardcoded `max_requests=5`, window from settings), key `"{client_ip}:change_email_request"` | Limiter not defined |
| Re-auth | Password is checked **only if supplied**; wrong password → `400 INVALID_PASSWORD`; omission is allowed | — |
| Validation | Manual email format check → `422 VALIDATION_ERROR`; same-as-current → `400 EMAIL_UNCHANGED` | — |
| Anti-enumeration | If another account owns the target email: neutral `202`, no record created, no OTP delivered | — |
| Data written | Invalidates prior active `EmailChangeRequest` rows; inserts a new one | — |
| Transaction / locks | Duplicate-email read in a plain session (no lock); then transaction with `Account … FOR UPDATE` and active `EmailChangeRequest … FOR UPDATE` | — |
| Request parameters | OTP TTL 600 s, `resend_available_at = now + 60s`, `resend_count` carried over, `attempts=0` | — |
| Email | `deliver_otp(recipient=new_email, purpose="email_change")`; on `EmailDeliveryError`, the new record is marked `completed_at=now` | — |
| Errors | `400 INVALID_PASSWORD`, `400 EMAIL_UNCHANGED`, `422 VALIDATION_ERROR`, `429`; cooldown/quota/banned return neutral `202` | — |
| Side effects | Active request replacement, OTP email, delivery-failure invalidation | — |

**Contract drift:** committed OpenAPI marks `password` as **required**; the schema and reference treat it as optional.

### 2.7 `POST /profile/change-email/resend-otp`

| Aspect | Reference implementation | Committed source |
|---|---|---|
| Location | `change_email_resend_otp`, lines 1391–1496 | **Absent** |
| Status | Real | No route, no handler |
| Request | No body | — |
| Success response | `200` `{"status": "sent", "message": "Verification code resent successfully."}` | — |
| Auth / CSRF | `get_current_session`; `Depends(verify_csrf_header)` | — |
| Rate limit | Reuses `rate_limiter_resend_otp` with key `"{client_ip}:resend_email_change_otp"` → `429` | Limiter exists but is used only for registration resend |
| Data read | `Account … FOR UPDATE`; latest active `EmailChangeRequest … FOR UPDATE` | — |
| Checks | No request → `404 USER_NOT_FOUND` (only if account missing) or `400 NO_ACTIVE_EMAIL_CHANGE`; cooldown → `429 RESEND_COOLDOWN_ACTIVE` with remaining seconds; quota `>= 3` → `400 RESEND_LIMIT_EXCEEDED`; expired → `400 REQUEST_EXPIRED` | — |
| Data written | New OTP hash and expiry; `resend_count += 1`; `resend_available_at = now + 60s` | — |
| Transaction / locks | Single transaction: **Account → EmailChangeRequest** | — |
| Email | `deliver_otp(recipient=new_email_normalized, purpose="email_change")` after commit | — |
| Failure behavior | `EmailDeliveryError` is only logged; the record keeps the new OTP and the endpoint still returns `200 "sent"` | — |
| Side effects | OTP reissue and quota/cooldown consumption | — |

### 2.8 `POST /profile/change-email/verify`

| Aspect | Reference implementation | Committed source |
|---|---|---|
| Location | `change_email_verify`, lines 1498–1627 | `change_email_verify`, lines 725–729 |
| Status | Real | 501 placeholder |
| Request | `ChangeEmailVerifyRequest` → `{otp: 6 digits}` | Same schema |
| Success response | `200` full private profile with `avatar_url` | None (501) |
| Auth / CSRF | `get_current_session`; `Depends(verify_csrf_header)` | Route has CSRF dependency, no session dependency |
| Rate limit | `rate_limiter_change_email_verify` (hardcoded `max_requests=10`), key `"{client_ip}:change_email_verify"` | Limiter not defined |
| Data read | Active `EmailChangeRequest … FOR UPDATE`; `Account … FOR UPDATE`; duplicate `Account` by normalized email `FOR UPDATE` | — |
| Data written | `Account.email`, `email_normalized`, `email_verified_at`; request `verified_at` and `completed_at` | — |
| Transaction / locks | Single transaction: **EmailChangeRequest → Account** (opposite order to request/resend) | — |
| Checks | No active request, expiry, attempt cap, OTP hash, duplicate-email uniqueness | — |
| Errors | `400 NO_ACTIVE_EMAIL_CHANGE`, `400 OTP_EXPIRED`, `400 OTP_ATTEMPTS_EXCEEDED`, `400 INVALID_OTP`, `409 EMAIL_ALREADY_IN_USE`, `429` | — |
| Attempt behavior | On the 5th wrong OTP the request is completed and the response is immediately `OTP_ATTEMPTS_EXCEEDED` (different from recovery verify) | — |
| Session effects | **No session revocation**, no forced re-login | — |
| Side effects | Email/verification timestamp update; request completion; no outbox/audit event | — |

---

## 3. Dependency Inventory

### 3.1 Already present in committed source and already used

| Dependency | Location | Used by |
|---|---|---|
| `datetime`, `UTC`, `timedelta`, `uuid` | `app.py` header | Existing routes and all reference handlers |
| `select`, `update` | `app.py` header | Existing routes and all reference handlers |
| `Depends`, `Request`, `Response`, `status`, `JSONResponse` | FastAPI imports | All reference handlers |
| `error_response` | `src/common/errors.py` | All reference error paths |
| `ensure_utc` | `src/common/models.py` | Cooldown/expiry comparisons |
| `SlidingWindowRateLimiter`, `get_client_ip` | `src/common/rate_limit.py` | Rate limits |
| `get_current_session`, `verify_csrf_header` | `src/services/user/auth.py` | Avatar and change-email handlers |
| `user_db` | `src/services/user/database.py` | All DB access |
| `user_settings` | `src/services/user/config.py` | Rate limits, trusted proxies, content URLs |
| `Account`, `PasswordHistory`, `Session` | `src/services/user/models.py` | Handlers plus existing routes |
| `deliver_otp`, `EmailDeliveryError`, `otp_delivery_sink` | `src/services/user/otp_delivery.py` | Already imported and used by registration flows |
| `generate_otp`, `hash_secret`, `verify_otp_hash`, `verify_password`, `hash_password` | `src/services/user/security.py` | All reference handlers |
| `rate_limit_recovery_request_max`, `rate_limit_recovery_verify_max`, `rate_limit_recovery_reset_max`, `rate_limit_window_seconds`, `trusted_proxies` | `src/services/user/config.py`, `src/common/config.py` | Recovery handlers |
| Content Service `claim-avatar` / `release-avatar` endpoints | `src/services/content/app.py:1520, 1593` | Avatar handlers |
| `user_settings.content_service_url`, `internal_service_secret` | `src/common/config.py:30, 41` | Avatar handlers |

### 3.2 Present in committed source but not imported/used by the current app handlers

| Dependency | Location | Status |
|---|---|---|
| `OneTimeToken` model | `src/services/user/models.py:102` | Defined and migrated, but not imported by `app.py`; needed by recovery handlers |
| `EmailChangeRequest` model | `src/services/user/models.py:171` | Defined and migrated, but not imported by `app.py`; needed by change-email handlers |
| `SetAvatarRequest` schema | `src/services/user/schemas.py:113` | Defined, not imported by `app.py`; needed by avatar handler |
| `generate_reset_token()` | `src/services/user/security.py:65` | Defined, not imported by `app.py`; needed by recovery verify |
| `httpx` | declared in `pyproject.toml` (`httpx>=0.27.0`) | Installed, not imported by `app.py`; needed by avatar handlers |
| `ChangeEmailResendRequest` schema | `src/services/user/schemas.py:109` | Defined but unused anywhere; resend route takes no body |
| `verify_internal_service_key` | `src/services/user/auth.py:138` | Imported and used by `GET /internal/users/{user_id}` — **must remain** |
| `not_implemented_response` | `src/common/errors.py` | Used by the 5 stubs; becomes unused after restoration (Ruff F401 risk) |
| `rate_limiter_resend_otp` | `app.py:115` | Already defined; reference reuses it for change-email resend |

### 3.3 Available only in the preserved reference

| Item | Evidence |
|---|---|
| 8 handler bodies | `set_avatar`, `delete_avatar`, `recovery_request`, `recovery_verify`, `recovery_reset`, `change_email_request`, `change_email_resend_otp`, `change_email_verify` |
| 5 rate limiter instances | `rate_limiter_recovery_request`, `_recovery_verify`, `_recovery_reset`, `rate_limiter_change_email_request`, `_change_email_verify` (reference lines 123–142) |
| Tests-hook resets for the 5 new limiters | Reference `reset_rate_limiters_hook`, lines 193–198 |
| `avatar_url` in `GET /profile` | Reference lines 685–690 and response field |
| `avatar_url` in `PATCH /profile` response | Reference lines 720–735 |
| `avatar_url` in `GET /users/{user_id}` | Reference line 1716 |
| `X-Internal-Key` request header choice | Reference lines 752, 854 (conflicts with Content Service) |

### 3.4 Import delta required for restoration

Add to committed `app.py`:

```python
import httpx

# models
EmailChangeRequest
OneTimeToken

# schemas
SetAvatarRequest

# security
generate_reset_token
```

Do **not** remove `verify_internal_service_key` (committed-only, needed by `/internal/users`).
After the five stubs are replaced, remove or retain `not_implemented_response` only if it is still referenced; otherwise Ruff will report `F401`.

### 3.5 Route/response changes outside the eight handlers

These are required for the avatar feature and its tests, and are present in the reference but outside the eight handler bodies:

| Change | Reference location | Why required |
|---|---|---|
| `GET /profile` adds `avatar_url` | 685–690 | `tests/test_avatar_email_change_pg.py:178` asserts `profile["avatar_url"] is None` after delete |
| `PATCH /profile` response adds `avatar_url` | 720–735 | OpenAPI `UserProfileOwn` includes `avatar_url`; keeps profile shapes consistent |
| `GET /users/{user_id}` adds `avatar_url` | 1716 | OpenAPI `UserPublic` includes `avatar_url` |

`UserProfileOwn` and `UserPublic` both already define `avatar_url` in `src/services/user/schemas.py:26, 123`, and committed OpenAPI documents it, so these are response-completeness fixes rather than new API design.

### 3.6 Route ordering / duplicate-route hazards

- The five stub definitions in committed source must be **replaced in place**. Appending the reference blocks would register duplicate paths and shadow function names.
- The new `resend-otp` route should be inserted between the change-email request and verify routes.
- The two avatar routes should be inserted near the other `/profile` routes (before the recovery section).
- `GET /internal/users/{user_id}` currently sits at the **end** of committed `app.py`. The reference file ends without it; applying the reference tail would delete it. This is the single most important invariant of the merge.
- Expected post-restoration route count: **27** `@app.*` decorators (24 + 3 new paths: avatar PUT, avatar DELETE, resend-otp).

---

## 4. Behavioral and Security Considerations

### 4.1 Confirmed from code

1. **Anti-enumeration is consistent across recovery and email-change request paths.** Unknown/banned accounts, cooldown windows, resend quotas, and already-taken target emails all return the same neutral `202`. The message wording differs between flows but is neutral in both.
2. **OTP/ticket storage uses SHA-256 hashes, not plaintext.** `hash_secret()` is used for OTPs and reset tickets; comparison for OTPs is constant-time (`hmac.compare_digest`). Reset tickets are looked up by hash.
3. **Single-use guarantees.** Recovery verify consumes the OTP and invalidates any earlier reset tickets; reset consumes the ticket. Change-email request invalidates prior active requests; verify completes the request.
4. **Lockout and quotas.** Recovery and change-email OTPs permit 5 attempts; resend quota is 3; cooldown is 60 seconds. Attempt/expiry errors are persisted (`consumed_at` / `completed_at`).
5. **Password reset revokes all sessions**, with `revocation_reason="password_reset"`. Email change does **not** revoke sessions and does not require re-authentication if the optional password is omitted.
6. **No outbox/audit events are emitted** by any of the eight handlers. OTP delivery is direct via `deliver_otp`; `UserOutboxEvent` is untouched.
7. **Error envelope** is the canonical `error_response(...)` shape (`{"error": {code, message, details, request_id}}`), except `204` responses which have no body.
8. **CSRF** is enforced on all authenticated state-changing routes (avatar and change-email); the public recovery routes correctly have no CSRF dependency.

### 4.2 Risks and mismatches

1. **`X-Internal-Key` vs `X-Internal-Service-Key` (high).**
   - User reference sends `X-Internal-Key` (`app.py` reference lines 752, 854).
   - Content Service reads `X-Internal-Service-Key` (`src/services/content/auth.py:151`).
   - Content Service only bypasses a missing key when `environment` is `development`/`test` (`auth.py:161`).
   - Consequence: avatar claim/release works locally by accident but returns `401` in staging/production. The design/change-log docs also say `X-Internal-Key`, so documentation and Content code disagree. A decision is required before or during the avatar restoration task. The safer alignment is to send `X-Internal-Service-Key`, matching Content Service and the User Service's own `/internal/users` convention.
2. **Optional password on email change (security/contract).** The reference accepts `password=None`; OpenAPI marks it required. Anyone with a stolen session (and CSRF token) can change the account email without knowing the current password. Tests always supply it. Recommend deciding whether to enforce it; do not silently preserve the permissive behavior without documenting it.
3. **Lock-order inversion (concurrency risk).**
   - Recovery verify locks `Account → OneTimeToken`; reset locks `OneTimeToken → Account`.
   - Change-email request/resend lock `Account → EmailChangeRequest`; verify locks `EmailChangeRequest → Account`.
   - Concurrent operations on the same account can deadlock; PostgreSQL will abort one transaction, likely surfacing as a 500 because there is no deadlock/`DBAPIError` handling. No committed test covers verify+reset or request+verify concurrency.
4. **Uniqueness race not handled (correctness).** `change_email_verify` checks for a duplicate account with `FOR UPDATE`, but a concurrent insert/update can still violate the unique `email_normalized` constraint at commit. `IntegrityError` is imported but not caught in this handler, so the request may return 500 instead of `409 EMAIL_ALREADY_IN_USE`. Existing tests do not exercise cross-account concurrent claims.
5. **External/DB consistency windows.**
   - `set_avatar` claims the content attachment **before** the DB transaction. If the DB commit fails, the attachment remains `avatar` with no account reference (orphan).
   - `delete_avatar` clears the DB first and releases content best-effort; failure leaves content `avatar` while the account has no avatar.
   - Old-avatar release failures only produce warnings.
6. **Email delivery failure asymmetry.** Recovery request and change-email request invalidate their token/record on `EmailDeliveryError`; resend logs the failure but keeps the new OTP and still returns `200 "sent"`. A user can be told "sent" when nothing was delivered.
7. **OTP hash strength.** 6-digit OTPs are stored as unsalted SHA-256 hashes. Online attempts are capped, but an offline dump of `one_time_tokens`/`email_change_requests` is trivially brute-forced. This is inherited design, not introduced by restoration; note for a future hardening task.
8. **Minor TTL documentation mismatch.** `deliver_otp` body text says codes expire in 10 minutes; recovery OTPs are 900 s (15 min). Change-email OTPs are 600 s.
9. **PII logging.** `change_email_verify` logs the new email address at INFO. Not a secret, but PII in logs.
10. **Rate limiter coupling.** Change-email resend reuses `rate_limiter_resend_otp`; keys are distinct (`…:resend_email_change_otp` vs `…:resend_otp`), so quota buckets are separate. Change-email limits are hardcoded (5/10), while recovery limits are configurable.
11. **SQLite does not enforce `FOR UPDATE`.** The in-process auth test suite runs on SQLite; concurrency guarantees can only be validated by the PostgreSQL tests, which require a rebuilt live service.
12. **`not_implemented_response` cleanup.** The reference keeps an unused import. Copying its header verbatim would fail the repository's Ruff `F` rules; remove the import once no stub uses it.

### 4.3 OpenAPI vs reference behavior

| Endpoint | OpenAPI | Reference / schema |
|---|---|---|
| Change-email request | `password` required | Optional (`str \| None`); omission allowed |
| Avatar PUT | documents `400`, no `409` | No `400` path; can return `409 INVALID_ATTACHMENT_STATUS` |
| Change-email verify | documents `400`, `401` only | Also returns `409` and `429` |
| Recovery verify | documents `200`, `400`, `422` | Also returns `429` |
| Recovery reset | documents `204`, `400`, `422` | Also returns `429` |
| Change-email request | documents `401`, `422` | Also returns `400`, `429` |

These are contract drifts to resolve consciously, not blockers for source restoration.

---

## 5. Minimal Restoration Sequence

### 5.1 Invariants for every step

- **Never copy the reference file wholesale.** Merge blocks into committed `app.py`.
- **Protect `GET /internal/users/{user_id}` and its `verify_internal_service_key` import.** After every step, assert both are present.
- Replace stub definitions in place; do not append duplicate routes.
- Keep all Task 031/031.1 uncommitted work untouched.
- No rebuild, restart, migration, or live request during source restoration.
- Run static checks and in-process tests after each group; defer live-stack tests to the rebuild task.

### 5.2 Ordered groups

#### Group R – Recovery (smallest self-contained, best in-process coverage)

**Restore:**
- Imports: `OneTimeToken`, `generate_reset_token`.
- Rate limiters: `rate_limiter_recovery_request`, `_recovery_verify`, `_recovery_reset`.
- Add their resets to `reset_rate_limiters_hook`.
- Replace the three stubs with reference bodies: `recovery_request`, `recovery_verify`, `recovery_reset`.

**Why first:** no external service dependency, all dependencies already committed, and an in-process ASGI test already exists that covers request → verify → reset → session revocation.

**Dependencies:** `OneTimeToken` model, `generate_reset_token`, recovery config limits, `deliver_otp`.

**Focused tests:** `test_password_recovery_and_set_reset_lifecycle` (in-process), `test_tier1_api.py` (in-process), plus the route-table assertion in §6.

**Risk:** low. Main review points are attempt-limit asymmetry and lock ordering (documented, not necessarily fixed here).

**Smallest justified next task:** restore **Group R only** (Task 031.2.3c), with static checks and in-process tests. No rebuild.

#### Group E – Email change

**Restore:**
- Import `EmailChangeRequest`.
- Rate limiters: `rate_limiter_change_email_request`, `_change_email_verify`.
- Add their resets to the test hook.
- Replace the two stubs and insert the new `resend-otp` route with reference bodies.

**Why second:** no external service dependency; model and migration already committed. Live PostgreSQL tests exist but require a rebuilt service, so this group can only be statically/in-process validated at source level.

**Dependencies:** `EmailChangeRequest`, `deliver_otp`, migration `0003`, account-uniqueness constraint.

**Focused tests:** `test_tier1_api.py` (unauthenticated change-email → 401), static checks, route-count assertion. Live `tests/test_avatar_email_change_pg.py` email-change cases after rebuild.

**Risk:** medium. Review lock-order inversion, optional-password decision, and unhandled `IntegrityError`.

#### Group A – Avatar + supporting profile response fields

**Restore:**
- Import `httpx`, `SetAvatarRequest`.
- Replace `GET /profile` and `PATCH /profile` bodies with reference versions adding `avatar_url`.
- Add `avatar_url` to `GET /users/{user_id}`.
- Insert `PUT /profile/avatar` and `DELETE /profile/avatar` handlers.
- Resolve the `X-Internal-Key` vs `X-Internal-Service-Key` decision before merge.

**Why last:** only group with a cross-service dependency and an open header-mismatch question. Highest integration risk.

**Dependencies:** Content Service `claim-avatar`/`release-avatar`, `content_service_url`, `internal_service_secret`, `SetAvatarRequest`, `httpx`.

**Focused tests:** static checks + route assertion immediately; live `tests/test_avatar_email_change_pg.py -k avatar` after rebuild; manual check that `/profile` includes `avatar_url`.

**Risk:** medium-to-high. Review claim-before-DB ordering, best-effort release, and header alignment.

#### Group C – Cleanup and invariants

- Remove `not_implemented_response` import if no stub remains.
- Confirm `verify_internal_service_key` and `GET /internal/users/{user_id}` are intact.
- Confirm 27 `@app.*` decorators.
- Run Ruff and Pyright on `src/services/user/app.py`.
- Review the final diff against the reference to ensure only intended blocks were merged.

### 5.3 Sequence summary

| Order | Group | Dependency on live stack? | Why this order |
|---|---|---|---|
| 1 | R – Recovery | No (in-process test) | Self-contained, existing behavioral test |
| 2 | E – Email change | No (source-level) | Model/migration ready; no external calls |
| 3 | A – Avatar + profile fields | Yes (Content Service) | Cross-service integration and header decision |
| 4 | C – Cleanup/invariants | No | Final consistency and lint gate |
| 5 | Separate rebuild/validation task | Yes | Live PG + Mailpit/browser tests after code is settled |

**Do not combine the rebuild with source restoration.** The preserved image (`forumplatform-user-service:recovery-4a1c1198a0ed`) must remain available for rollback until live validation completes.

---

## 6. Focused Validation Commands for Later Tasks

These commands are recommended for the restoration tasks. **None were executed during this analysis.**

### 6.1 Static analysis

```bash
./venv/bin/ruff check src/services/user/app.py
./venv/bin/pyright --pythonpath venv/bin/python src/services/user/app.py
```

### 6.2 In-process tests (no rebuild required)

```bash
# Recovery lifecycle (ASGITransport + SQLite)
./venv/bin/pytest -q tests/test_user_auth_lifecycle.py::test_password_recovery_and_set_reset_lifecycle

# Full auth lifecycle regression
./venv/bin/pytest -q tests/test_user_auth_lifecycle.py

# Unauthenticated change-email request must be 401, not 501
./venv/bin/pytest -q tests/test_tier1_api.py::test_unimplemented_endpoints_return_501_not_implemented
```

### 6.3 Route and invariant checks

```bash
# Expected post-restoration route count: 27
grep -c '^@app\.' src/services/user/app.py

# Internal endpoint and its dependency must still exist
grep -n '"/internal/users/{user_id}"\|async def get_internal_user_contact\|verify_internal_service_key' src/services/user/app.py

# All eight handlers and the three new paths must be registered
grep -n 'async def recovery_request\|async def recovery_verify\|async def recovery_reset\|async def set_avatar\|async def delete_avatar\|async def change_email_request\|async def change_email_resend_otp\|async def change_email_verify' src/services/user/app.py
grep -n '"/profile/avatar"\|"/profile/change-email/resend-otp"' src/services/user/app.py

# Route table assertion (imports the app; no server/startup)
./venv/bin/python - <<'PY'
from src.services.user.app import app
paths = {getattr(r, "path", "") for r in app.routes}
required = {
    "/auth/recovery/request", "/auth/recovery/verify", "/auth/recovery/reset",
    "/profile/avatar", "/profile/change-email/request",
    "/profile/change-email/resend-otp", "/profile/change-email/verify",
    "/internal/users/{user_id}",
}
missing = required - paths
assert not missing, f"Missing routes: {missing}"
assert len([r for r in app.routes if getattr(r, "path", "") in required]) == 8
print("route assertions passed")
PY
```

### 6.4 Live-stack tests (only after a controlled rebuild/recreate task)

Prerequisites: PostgreSQL on `localhost:5432`, gateway `localhost:8000`, user service `localhost:8001`, Content Service, Mailpit, allowlist configured. These tests make real HTTP requests and write test rows.

```bash
# Recovery concurrency (PostgreSQL + live gateway/user service)
./venv/bin/pytest -q tests/test_recovery_concurrency_pg.py

# Avatar + email-change PostgreSQL integration
./venv/bin/pytest -q tests/test_avatar_email_change_pg.py

# Optional end-to-end browser recovery journey
./venv/bin/pytest -q tests/test_browser_e2e.py::test_browser_account_recovery_and_password_reset_journey
```

**Coverage gaps to add (in the restoration tasks, not this one):**
- Normal email-change request → verify success path.
- Resend-otp success, cooldown, quota, and expired-request paths.
- Avatar 404 (non-existent attachment) and 409 (invalid content status) mappings.
- Concurrent cross-account claim of the same new email (uniqueness race).
- Concurrent recovery verify + reset (lock-order/deadlock check).

---

## 7. Uncertainties and Items Not Verified

1. **Reference revision fidelity.** The preserved `app.py` is the image's installed file; it is not proven to be the exact final intended implementation or the exact revision against which the committed tests were written. The tests are the acceptance authority.
2. **Runtime behavior not executed.** All handler behavior above is derived from code reading. No endpoint was called, and no test was run.
3. **Header mismatch not exercised.** The `X-Internal-Key` / `X-Internal-Service-Key` mismatch is confirmed in code, but its production effect (401) was not reproduced. The development/test bypass explains local success but was not exercised here.
4. **Deadlock behavior not verified.** Lock-order inversion is visible in code; whether PostgreSQL actually deadlocks under realistic interleavings, and how the application surfaces it, is not established.
5. **Uniqueness race not verified.** The unhandled `IntegrityError` path is inferred from code, not reproduced.
6. **Database migration state not inspected.** Source/migrations define the required columns (`one_time_tokens.resend_count/resend_after`, `email_change_requests.resend_count/resend_available_at`, `accounts.avatar_object_key`), but the running database's applied revision was not checked (out of scope; no DB access).
7. **No config fields for change-email limits.** The reference hardcodes `5` and `10`; whether these should become settings is a design decision.
8. **`ChangeEmailResendRequest` is unused.** It exists in schemas but no route consumes a body; the reference resend route takes no request model. The class may be dead code.
9. **OpenAPI drift is unresolved.** The committed OpenAPI and reference behavior disagree on required password, documented status codes, and some error paths. The desired contract must be chosen before or during restoration.
10. **`PasswordHistory` retention is unbounded.** The reset handler reads the latest 5 but never prunes older rows; this matches the existing change-password behavior. Not a restoration blocker.
11. **In-process test fixtures reset only the original four rate limiters.** The recovery lifecycle test calls `/test/reset-rate-limiters` (which will reset the new ones after restoration), but extending the autouse fixture to reset all nine would improve isolation. This is test hygiene, not application code.
12. **Docs vs code on internal header.** `docs/audit/task_030_design.md` and `task_030_change_log.md` say `X-Internal-Key`, while Content Service code expects `X-Internal-Service-Key`. Neither is authoritative without a decision.
13. **Route count assumption.** 27 is derived from 24 committed decorators plus three new paths. If any restoration task adds/removes another route, re-derive this invariant.

---

## Summary

- The reference contains real implementations for all eight missing routes; committed source contains stubs for five and no routes for three.
- Required imports/objects are almost entirely already present in committed source; only `httpx`, two models, one schema, one security helper, and five rate-limiter instances need to be wired in.
- The reference must be merged selectively: it lacks `GET /internal/users/{user_id}` and its `verify_internal_service_key` import, and it adds profile-response fields outside the eight handlers.
- The most important open issue is the Content Service header mismatch (`X-Internal-Key` vs `X-Internal-Service-Key`), masked by the development/test bypass.
- Recommended restoration order: **Recovery → Email change → Avatar/profile fields → cleanup**, with static/in-process validation after each group and live PostgreSQL tests only after a separate rebuild task.
- The smallest justified next task is **recovery-only restoration** (Group R), which is self-contained and has an existing in-process lifecycle test.
