# Task 031.2.3f – Email-Change Password Schema Alignment Report

**Date:** 2026-10-23
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`
**Type:** Schema correction (no deployment, no database access)
**Targets:** `src/services/user/schemas.py`, `src/services/user/app.py`, `tests/test_user_auth_lifecycle.py`
**Reference:** `docs/audit/task_031_2_3d_email_change_restoration_report.md`

---

## 1. Baseline and Working-Tree State

### 1.1 Baseline

| Item | Value |
|---|---|
| Branch | `master` |
| HEAD | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| Schema before | `ChangeEmailRequest.password: str \| None = None` |
| Canonical OpenAPI before | `required: [new_email, password]` |
| Handler before | Handler-level `None` guard returning `422 VALIDATION_ERROR` |

### 1.2 Initial working-tree state

Pre-existing Task 031/031.1 work and the Task 031.2.3c/031.2.3d/031.2.3e restorations were present and preserved:

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
?? docs/audit/task_031_2_3e_avatar_restoration_report.md
?? migrations/notifications/versions/20261022_0004_email_delivery_lease.py
?? tests/test_email_worker_lease_pg.py
```

### 1.3 Final working-tree state

Newly modified by this task:

```
 M src/services/user/app.py
 M src/services/user/schemas.py
 M tests/test_user_auth_lifecycle.py
```

No staged changes. Final status is in §7.

---

## 2. Contract Evidence

| Artifact | Evidence |
|---|---|
| `src/docs/forum_public_api.openapi.yaml:421` | `required: [new_email, password]` |
| `src/docs/forum_public_api.openapi.yaml:426–427` | `password: type: string` |
| `docs/audit/task_030_design.md:62–64` | Request payload `{new_email, password}`; "Verify password (re-authentication)" |
| `docs/audit/task_031_2_3d_email_change_restoration_report.md:100–117` | Confirms the contract and records the schema as the remaining drift |
| `src/services/user/schemas.py:104–106` (before) | `password: str \| None = None` |

Conclusion: the canonical contract requires **both** `new_email` and `password`, and the email-change request is explicitly a re-authentication step. No separate inconsistency in the OpenAPI file was found, so the OpenAPI and API contract documents were **not** modified.

---

## 3. Files Changed

### 3.1 `src/services/user/schemas.py`

```python
class ChangeEmailRequest(BaseModel):
    new_email: str
    password: str
```

`password` is now required. Missing and explicit `null` values are rejected by Pydantic/FastAPI validation before the handler runs, producing the canonical `422 VALIDATION_ERROR` envelope.

### 3.2 `src/services/user/app.py`

Removed the now-redundant handler-level `if req.password is None` guard and its comment. The incorrect-password branch is unchanged:

```python
# Re-authentication is required by the committed OpenAPI contract and enforced
# by ChangeEmailRequest; this check rejects an incorrect password.
if not verify_password(ctx.user.password_hash, req.password):
    return error_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="INVALID_PASSWORD",
        message="Current password is incorrect.",
        request=request,
    )
```

No workflow, authentication, CSRF, rate-limiting, OTP, or database logic changed.

### 3.3 `tests/test_user_auth_lifecycle.py`

Added `test_change_email_request_requires_password` (`tests/test_user_auth_lifecycle.py:968`), which exercises the full in-process flow and asserts:

1. Missing `password` → `422 VALIDATION_ERROR`, with a `password` detail.
2. Explicit `password: null` → `422 VALIDATION_ERROR`.
3. Wrong password → `400 INVALID_PASSWORD`.
4. Correct password → `202`, proving the request reaches the handler and delivers an `email_change` OTP.
5. Generated OpenAPI resolves `ChangeEmailRequest` and lists `password` in `required`.

---

## 4. Tests and Static Checks Executed

All commands ran from the repository root using `./venv`.

| Check | Command | Result |
|---|---|---|
| Focused contract test | `pytest -q tests/test_user_auth_lifecycle.py::test_change_email_request_requires_password` | **1 passed in 1.76s** |
| Full auth lifecycle file | `pytest -q tests/test_user_auth_lifecycle.py` | **15 passed in 6.14s** |
| Tier-1 API regression | `pytest -q tests/test_tier1_api.py` | **4 passed in 2.35s** |
| Ruff | `ruff check src/services/user/schemas.py src/services/user/app.py tests/test_user_auth_lifecycle.py` | **All checks passed** |
| Pyright | `pyright --pythonpath venv/bin/python src/services/user/schemas.py src/services/user/app.py` | **0 errors, 0 warnings, 0 informations** |
| Whitespace | `git diff --check` | Clean |

In-process generated OpenAPI check:

```
required: ['new_email', 'password']
password property type: {'type': 'string', 'title': 'Password'}
```

The generated schema now matches the canonical contract.

---

## 5. Tests Not Run and Why

| Test | Reason |
|---|---|
| `tests/test_avatar_email_change_pg.py` | PostgreSQL integration tests that write to a database and target the live stack; explicitly out of scope. |
| `tests/test_browser_e2e.py` email-change journey | Requires a rebuilt/live stack and browser; deployment out of scope. |
| Full repository test suite | Includes the excluded PG/live-stack suites. |

The focused in-process test provides the requested validation for missing/null/wrong/correct password behavior; no live integration claim is made.

---

## 6. Remaining Risks

1. **Client compatibility:** any client that previously omitted `password` now receives `422` instead of continuing. This is the intended contract behavior, but callers must be checked before deployment.
2. **No live/PostgreSQL validation:** only in-process SQLite/ASGI behavior was exercised.
3. **Running container remains stale:** `forum_user_service` still runs the preserved `4a1c1198a0ed` image; the schema correction is not deployed.
4. **Password field still has no minimum length in this schema.** The canonical OpenAPI only declares `type: string`; adding a length constraint would exceed this task's contract-alignment scope.
5. **`ChangeEmailResendRequest` remains unused.** It is unrelated to this correction and was left untouched.

---

## 7. Diff-Scope Summary

Task-only changes:

```
src/services/user/schemas.py      | 2 +-
tests/test_user_auth_lifecycle.py | 95 ++++++++++++++++++++++++++++++++++++++++
src/services/user/app.py          | handler guard removed (part of the larger
                                    uncommitted 031.2.3c–e restoration diff)
```

Cumulative `app.py` diff remains the prior restoration work plus this guard removal. Verified:

| Check | Result |
|---|---|
| Only schema/app/test files changed by this task | ✅ |
| Canonical OpenAPI modified | ❌ No |
| Recovery functionality modified | ❌ No |
| Avatar functionality modified | ❌ No |
| Email-change workflow/auth/CSRF/rate-limit/OTP/DB behavior changed | ❌ No |
| Task 031.1, Notification, Compose, env, migrations modified | ❌ No |
| Database accessed/written | ❌ No |
| Containers touched | ❌ No |
| Staged/committed/pushed | ❌ No |

Final `git status --short`:

```
 M .env.example
 M docker-compose.yml
 M src/services/notification/config.py
 M src/services/notification/email_worker.py
 M src/services/notification/models.py
 M src/services/user/app.py
 M src/services/user/schemas.py
 M tests/test_email_dispatch_worker.py
 M tests/test_user_auth_lifecycle.py
?? docs/audit/task_031_1_change_log.md
?? docs/audit/task_031_1_design.md
?? docs/audit/task_031_1_validation_report.md
?? docs/audit/task_031_2_1_inventory_report.md
?? docs/audit/task_031_2_2_discrepancy_report.md
?? docs/audit/task_031_2_3a_image_preservation_report.md
?? docs/audit/task_031_2_3b_restoration_map.md
?? docs/audit/task_031_2_3c_recovery_restoration_report.md
?? docs/audit/task_031_2_3d_email_change_restoration_report.md
?? docs/audit/task_031_2_3e_avatar_restoration_report.md
?? docs/audit/task_031_2_3f_email_change_schema_report.md
?? migrations/notifications/versions/20261022_0004_email_delivery_lease.py
?? tests/test_email_worker_lease_pg.py
```

`tests/test_user_auth_lifecycle.py` was clean at task start; the only change in it is the new focused test. No other audit report was modified.

**Recommended next task:** verify clients always send the password, then proceed to the separately authorized rebuild/recreate and live PG/browser validation wave.
