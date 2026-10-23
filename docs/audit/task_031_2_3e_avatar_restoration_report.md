# Task 031.2.3e – Avatar Handler and Profile Avatar-Field Restoration Report

**Date:** 2026-10-23
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`
**Type:** Source restoration (no deployment, no database writes)
**Reference:** `/tmp/opencode/user_app_container_4a1c1198a0ed.py`
**Target:** `src/services/user/app.py`
**Predecessors:** `docs/audit/task_031_2_3b_restoration_map.md`, `docs/audit/task_031_2_3c_recovery_restoration_report.md`, `docs/audit/task_031_2_3d_email_change_restoration_report.md`

---

## 1. Baseline and Working-Tree State

### 1.1 Initial baseline

| Item | Value |
|---|---|
| Branch | `master` |
| HEAD | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| Target `app.py` before Task 031.2.3e | 1,792 lines |
| Reference | 1,848 lines |
| Reference SHA-256 | `7a1d3c6f4222703af6dcdb2bfde7ced1b7da10eb31f73b1e3efc46bbe51ea098` |
| Reference matches Task 031.2.3a hash | ✅ Yes |
| Reference modified by this task | ❌ No |

### 1.2 Initial working-tree state

Pre-existing Task 031/031.1 changes and audit artifacts were present, including the Task 031.2.3c/031.2.3d restorations. All were preserved:

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
?? docs/audit/task_031_2_3d_email_change_restoration_report.md
?? migrations/notifications/versions/20261022_0004_email_delivery_lease.py
?? tests/test_email_worker_lease_pg.py
```

### 1.3 Final working-tree state

`src/services/user/app.py` is now 1,949 lines. Final status is in §9.

---

## 2. Reference Integrity

- SHA-256 before: `7a1d3c6f4222703af6dcdb2bfde7ced1b7da10eb31f73b1e3efc46bbe51ea098`
- SHA-256 after: `7a1d3c6f4222703af6dcdb2bfde7ced1b7da10eb31f73b1e3efc46bbe51ea098`

The reference was read only and was not copied wholesale.

---

## 3. Avatar Handlers and Dependencies Restored

### 3.1 Handlers

| Route | Method | Handler | Location |
|---|---|---|---|
| `/profile/avatar` | `PUT` | `set_avatar` | `app.py:744` |
| `/profile/avatar` | `DELETE` | `delete_avatar` | `app.py:847` |

Both routes carry `Depends(verify_csrf_header)` and resolve the caller through `Depends(get_current_session)`.

### 3.2 Dependencies added

| Dependency | Change | Reason |
|---|---|---|
| `httpx` | Added import | Internal calls to Content Service |
| `SetAvatarRequest` | Added to schema import | `PUT /profile/avatar` body `{image_id}` |

Reused without changes:

- `Account.avatar_object_key` (already present in the model),
- `user_settings.content_service_url`,
- `user_settings.internal_service_secret`,
- `get_current_session`, `verify_csrf_header`,
- `error_response` and the existing error envelope,
- `SetAvatarRequest` (`image_id: uuid.UUID`), already committed in `schemas.py`.

No schema, migration, OpenAPI, config, or Compose change was required. `httpx` was already a declared project dependency.

---

## 4. Content Service Header Verification

The committed Content Service internal dependency reads:

```python
# src/services/content/auth.py:151
key = request.headers.get("X-Internal-Service-Key")
```

The expected header is exactly **`X-Internal-Service-Key`**.

The preserved reference User Service sent `X-Internal-Key`, which does not match. In `development`/`test` the Content Service currently tolerates a missing key (`if key:` short-circuit plus environment bypass), which masked the defect in the running image. No bypass was added or relied on for this restoration.

Both avatar calls now send:

```python
headers = {"X-Internal-Service-Key": user_settings.internal_service_secret}
```

There are no remaining `X-Internal-Key` senders anywhere in the repository (`grep` returned none), so no unrelated caller refactor was needed or performed.

---

## 5. Claim/Release Behavior and Transaction/Failure Semantics

### 5.1 `PUT /profile/avatar`

1. Calls `POST {content_service_url}/internal/attachments/{image_id}/claim-avatar` with:
   - `X-Internal-Service-Key`,
   - `{"user_id": "<authenticated user id>"}`.
2. Maps Content Service responses:
   - `404` → `404 ATTACHMENT_NOT_FOUND`
   - `403` → `403 FORBIDDEN_ATTACHMENT_CLAIM`
   - `409` → `409 INVALID_ATTACHMENT_STATUS`
   - other non-`200` → `500 CONTENT_SERVICE_ERROR`
   - transport failure (`httpx.HTTPError`) → `502 SERVICE_UNAVAILABLE`
3. On `200`, updates `Account.avatar_object_key` under an `Account … FOR UPDATE` transaction.
4. If a different avatar was previously set, best-effort calls `release-avatar` for the old attachment after the DB commit.
5. Returns the private profile with `avatar_url = /uploads/images/{image_id}`.

Behavior preserved:

- **Replacing** an existing avatar releases the previous attachment.
- **Assigning the same avatar** is accepted (Content Service permits `status == "avatar"`), does not release anything, and returns the same URL.
- Ownership is enforced by the Content Service using the authenticated `user_id`; the User Service does not accept an arbitrary owner id.

Not atomic across services. The remote claim commits before the User Service DB transaction. If the DB update fails after a successful claim, the attachment remains claimed as an avatar while the account does not reference it. No compensation release was added: a blind rollback release could race with a concurrent successful assignment of the same image and delete a valid avatar. This orphan case is recorded as a residual risk.

The reference caught broad `Exception` around HTTP calls. This restoration catches only `httpx.HTTPError` (transport/timeout errors), so programming errors are not converted into service-unavailable responses.

### 5.2 `DELETE /profile/avatar`

1. Clears `Account.avatar_object_key` under `Account … FOR UPDATE` in its own transaction.
2. If an avatar existed, best-effort calls `release-avatar` with `X-Internal-Service-Key`.
3. Returns `204 No Content`.

Behavior preserved:

- **Removing an absent avatar** performs no Content Service call and still returns `204`.
- Release is best-effort: an HTTP error is logged, not surfaced. The account reference is already cleared, so the worst case is an unreferenced attachment still marked `avatar` in Content Service (orphan), not a broken profile reference.
- Ordering (DB first, release second) is intentional: clearing locally before releasing avoids leaving a profile pointing at deleted storage.

### 5.3 Secrets

Neither endpoint returns the internal service key, the object-storage key, or the Content Service response body. Clients receive only the public `/uploads/images/{id}` URL already used by the rest of the application.

---

## 6. `avatar_url` Wiring

The existing canonical OpenAPI (`UserPublic` and `UserProfileOwn`) and Pydantic schemas already include `avatar_url`, so no schema/OpenAPI change was required. The established URL format is `/uploads/images/{avatar_object_key}`, already used by the Task 031.2.3d email-change response.

| Endpoint | Handler | Change |
|---|---|---|
| `GET /profile` | `get_own_profile` (`app.py:684`) | Added `avatar_url` derived from `ctx.user.avatar_object_key` |
| `PATCH /profile` | `update_own_profile` (`app.py:709`) | Added `avatar_url` derived from the updated `account.avatar_object_key` |
| `GET /users/{user_id}` | `get_user_profile` (`app.py:1758`) | Added `avatar_url` derived from `account.avatar_object_key` |

All pre-existing fields (`id`, `username`, `display_name`, `bio`, `email`/`role`/`created_at` where applicable) are unchanged. The public profile still exposes no `email` and no internal storage key.

---

## 7. Tests and Static Checks Executed

All commands ran from the repository root using `./venv`.

| Check | Command | Result |
|---|---|---|
| Ruff | `./venv/bin/ruff check src/services/user/app.py` | **All checks passed** (exit 0) |
| Pyright | `./venv/bin/pyright --pythonpath venv/bin/python src/services/user/app.py` | **0 errors, 0 warnings, 0 informations** (exit 0) |
| Dirty-diff whitespace | `git diff --check` | Clean (exit 0) |
| Route registration | Imported `app`; inspected `app.routes` | `PUT /profile/avatar` and `DELETE /profile/avatar` each registered exactly once |
| Route count | `grep -c '^@app\.'` | **27** decorators (25 previous + 2 avatar routes) |
| Response wiring | `inspect.getsource` on the three profile handlers | `avatar_url` present in `get_own_profile`, `update_own_profile`, `get_user_profile` |
| Handler wiring | `inspect.getsource` on avatar handlers | `X-Internal-Service-Key` in both; `/claim-avatar` in PUT; `/release-avatar` in DELETE; no `X-Internal-Key` |
| Tier-1 regression | `pytest -q tests/test_tier1_api.py` | **4 passed** |
| Auth/recovery regression | `pytest -q tests/test_user_auth_lifecycle.py` | **14 passed** |
| Combined | `pytest -q tests/test_tier1_api.py tests/test_user_auth_lifecycle.py` | **18 passed in 8.20s** |

No existing test was modified.

---

## 8. Tests Not Executed and Why

| Test | Reason |
|---|---|
| `tests/test_avatar_email_change_pg.py` (avatar and email-change integration cases) | PostgreSQL tests that write to a database and target the live stack. Explicitly excluded. |
| `tests/test_browser_e2e.py::test_browser_avatar_and_email_change_lifecycle` | Requires a rebuilt/live service, Content Service, and browser stack; deployment is out of scope. |
| Live Content Service claim/release calls | Would require running/reachable services; not performed. The corrected header was verified statically against `src/services/content/auth.py`. |
| Full repository test suite | Includes the excluded PG/live-stack suites. |

There is no in-process avatar lifecycle test in the repository, so no end-to-end avatar claim/release behavior was exercised. The static and regression checks above do not constitute live integration coverage.

---

## 9. Diff-Scope Summary

`app.py` grew from 1,792 to 1,949 lines (net +157). Cumulative working-tree diff for the file (including Tasks 031.2.3c/031.2.3d) is:

```
src/services/user/app.py | 988 ++++++++++++++++++++++++++++++++++++++++++++++-
1 file changed, 976 insertions(+), 12 deletions(-)
```

Task 031.2.3e's own changes:

- 2 avatar handlers and routes (`PUT`, `DELETE`).
- 1 import (`httpx`) and 1 schema import (`SetAvatarRequest`).
- `avatar_url` added to `GET /profile`, `PATCH /profile`, and `GET /users/{user_id}`.
- Corrected internal header on both Content Service calls.
- Narrowed HTTP exception handling to `httpx.HTTPError` and added warning logs for non-2xx release responses.

Verified:

| Check | Result |
|---|---|
| Only `src/services/user/app.py` changed during this task | ✅ |
| Both avatar routes registered exactly once | ✅ |
| `avatar_url` wired into all three intended endpoints | ✅ |
| Recovery handlers intact | ✅ (`recovery_request` 883, `recovery_verify` 1004, `recovery_reset` 1128) |
| Email-change handlers intact | ✅ (`change_email_request` 1263, `change_email_resend_otp` 1438, `change_email_verify` 1545) |
| `GET /internal/users/{user_id}` intact and protected | ✅ (route 1918, dependency 1920, handler 1922) |
| Avatar calls use `X-Internal-Service-Key` | ✅ matches `src/services/content/auth.py:151` |
| Schema / OpenAPI / migration / Compose / env modified | ❌ None |
| Task 031.1 or Notification files modified | ❌ None |
| Containers or database touched | ❌ None |
| Staged or committed | ❌ None |

The other modified/untracked files in `git status` are pre-existing Task 031/031.1 work and audit artifacts; their contents were not changed by this task.

---

## 10. Recovery, Email-Change, and Internal Endpoint Confirmation

- Password recovery routes and handlers from Task 031.2.3c remain present and unchanged.
- Email-change routes and handlers from Task 031.2.3d remain present and unchanged.
- `GET /internal/users/{user_id}` remains registered exactly once with `Depends(verify_internal_service_key)`, and `verify_internal_service_key` remains imported and used.
- The tier-1 and auth/recovery regression suites still pass.

---

## 11. Remaining Risks and Recommended Next Task

### Remaining risks

1. **No live avatar claim/release test.** The corrected header is verified against source only; no request has been sent to a running Content Service.
2. **Cross-service non-atomicity.** A successful claim followed by a User Service DB failure leaves an orphaned avatar attachment; a failed release after deletion leaves an unreferenced attachment. Both are logged at best.
3. **No avatar rate limiting.** The reference has none; an authenticated client can repeatedly claim/release. This was not added to avoid scope expansion.
4. **No avatar-specific regression tests.** Only profile-field and route wiring were asserted statically.
5. **Running container remains stale.** `forum_user_service` still runs the preserved `4a1c1198a0ed` image and none of the restored source is deployed.
6. **Public avatar URLs are direct, unversioned paths.** They follow the existing `/uploads/images/{object_key}` convention; cache invalidation after replacement relies on URL change.

### Recommended next task

The source restoration wave (recovery, email change, avatar) is now complete. The next task should be a separately authorized **rebuild/recreate of the User Service**, followed by:

- `tests/test_recovery_concurrency_pg.py`,
- `tests/test_avatar_email_change_pg.py`,
- live Content Service claim/release checks with the correct header,
- Mailpit delivery checks,
- the browser avatar/email-change/recovery E2E journey.

No deployment, rebuild, migration, or integration test was performed in this task.
