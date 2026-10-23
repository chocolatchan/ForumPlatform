# Task 031.2.8 — Staging Security Guard Remediation Report

**Date:** 2026-10-23  
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`  
**Scope:** Test-only/development-only HTTP route exposure in staging and production  
**Verdict:** **COMPLETE FOR SOURCE-LEVEL REMEDIATION** — the running containers remain on the old images and are **not** protected by this source change until a future rebuild.

**Evidence legend**

- **Verified** — checked directly in the current worktree or by an in-process ASGI test.
- **Pre-existing** — present before this task; not introduced by this change.
- **Not run** — intentionally not executed because it would require a rebuild, live container request, migration, or database mutation.

---

## 1. Initial finding and evidence

The audit reports identified that the User service test hooks were blocked only when the environment was exactly `production`:

- `docs/audit/task_031_2_6_rebuild_readiness_and_rollback_plan.md:840`
- `docs/audit/task_031_2_7_pre_rebuild_blocker_remediation_plan.md:195-196`

The finding was verified in the current source before modification:

- `GET /test/otp` checked `if user_settings.environment == "production"`.
- `POST /test/reset-rate-limiters` checked `if user_settings.environment == "production"`.
- Both routes were registered unconditionally at module import and used `include_in_schema=False`.
- `ENVIRONMENT` defaulted to `development` in `docker-compose.yml` and `.env.example`; `.env.staging.example` sets `staging`.

Because the old guard only rejected `production`, an explicit `staging` deployment still reached both handlers. Hiding a route from OpenAPI does not make it unreachable.

No other test-only or development-only HTTP routes were found in the application services or Gateway.

---

## 2. Discovered test-only/development-only routes

### 2.1 User Service

| Method | Route | Source / handler | Registration | Guard before change | Gateway exposure | Existing tests |
|---|---|---|---|---|---|---|
| `GET` | `/test/otp` | `src/services/user/app.py`, `get_test_otp` | Module-level decorator; always registered; `include_in_schema=False` | `environment == "production"` only | Not mapped by Gateway router | `tests/test_security_hardening.py` production check; no staging check |
| `POST` | `/test/reset-rate-limiters` | `src/services/user/app.py`, `reset_rate_limiters_hook` | Module-level decorator; always registered; `include_in_schema=False` | `environment == "production"` only | Not mapped by Gateway router | `tests/test_security_hardening.py`; `tests/test_user_auth_lifecycle.py`; `tests/test_recovery_concurrency_pg.py`; `tests/test_browser_e2e.py` |

### 2.2 Other application services

Searches across `src/services/content`, `src/services/interaction`, `src/services/moderation`, `src/services/notification`, and `src/services/search` found no `/test`, `/debug`, or `/dev` HTTP routes and no test-only route registrations.

### 2.3 API Gateway

- The Gateway exposes `@app.api_route("/api/v1/{path:path}", ..., include_in_schema=False)` at `src/gateway/app.py:107`.
- `src/gateway/router.py` maps public paths to known service prefixes: `auth`, `profile`, `admin/users`, `users`, `categories`, `threads`, `comments`, `uploads`, `reports`, `moderation`, `notifications`, and `search`.
- There is no `test` rule in the router.
- Therefore `/api/v1/test/otp` and `/api/v1/test/reset-rate-limiters` resolve to no route and return `404 ROUTE_NOT_FOUND`. This behavior is independent of the User service environment.
- The Gateway's other `include_in_schema=False` routes are the proxy itself, `/` (frontend/status), and `/metrics`; they are not test-only routes and were not changed.

### 2.4 Configuration defaults

- `docker-compose.yml` sets `ENVIRONMENT: ${ENVIRONMENT:-development}` for the six application services.
- `.env.example` sets `ENVIRONMENT=development`.
- `.env.staging.example` sets `ENVIRONMENT=staging`.
- `tests/conftest.py` now sets `ENVIRONMENT=test` for deterministic in-process tests.
- The `UserSettings.environment` field itself still defaults to `development`; the new route policy requires the environment to be **explicitly configured** as `development` or `test`.

---

## 3. Exact changes made

### 3.1 Fail-closed test-endpoint policy

`src/services/user/config.py` adds:

```python
@property
def test_endpoints_enabled(self) -> bool:
    """Return whether test-only HTTP hooks may be served."""
    return "environment" in self.model_fields_set and self.environment in (
        "development",
        "test",
    )
```

Behavior:

- Explicit `development` or `test` enables the hooks.
- Explicit `staging` or `production` disables them.
- Missing `ENVIRONMENT` disables them, even though the ordinary settings default remains `development`.
- Empty, unknown, mixed-case, or whitespace-padded values disable them.
- No new bypass flag was added.

### 3.2 Request-guard dependency

`src/services/user/app.py` adds `require_test_endpoints()` and attaches it to both routes:

```python
def require_test_endpoints() -> None:
    if not user_settings.test_endpoints_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not available",
        )
```

Both routes retain `include_in_schema=False` and now use:

```python
dependencies=[Depends(require_test_endpoints)]
```

The dependency runs before endpoint query-parameter validation. A request such as `GET /test/otp` without the required `recipient` therefore returns `404` in a prohibited environment rather than a `422` that would reveal the route's parameter contract. The generic `"Not available"` detail does not expose OTPs, credentials, or implementation details.

### 3.3 Test environment configuration

`tests/conftest.py` now explicitly sets:

```python
os.environ["ENVIRONMENT"] = "test"
```

This makes the intended in-process test behavior explicit and prevents a missing environment from silently disabling the hooks during the test suite.

### 3.4 Focused tests

`tests/test_security_hardening.py` replaces the old production-only test with:

- `test_test_endpoints_allowed_in_development_and_test`
  - Exercises both routes in explicit `development` and `test`.
  - Verifies the reset route returns success and the OTP route returns the dummy in-memory test value.
- `test_test_endpoints_blocked_outside_development_and_test`
  - Covers `staging`, `production`, empty string, `prod`, and `staging `.
  - Asserts both routes return `404`.
  - Asserts the malformed `/test/otp` request also returns `404`.
- `test_test_endpoints_blocked_when_environment_is_missing`
  - Constructs settings without an explicit environment.
  - Verifies the ordinary default is still `development`, but `test_endpoints_enabled` is false.
  - Replaces the application settings object and verifies the actual routes return `404`.

`tests/test_gateway.py` adds:

- Router assertions that `test/otp` and `test/reset-rate-limiters` are not resolvable.
- An HTTP-level test that `/api/v1/test/reset-rate-limiters` and `/api/v1/test/otp` return `404 ROUTE_NOT_FOUND`.

No Gateway source change was required because the current router does not map test routes.

---

## 4. Environment behavior matrix

| Configuration | `/test/otp` | `/test/reset-rate-limiters` | Basis |
|---|---|---|---|
| `ENVIRONMENT=development` (explicit) | Allowed | Allowed | `test_endpoints_enabled` true |
| `ENVIRONMENT=test` (explicit) | Allowed | Allowed | `test_endpoints_enabled` true |
| `ENVIRONMENT=staging` | `404` | `404` | Explicit allowlist rejects |
| `ENVIRONMENT=production` | `404` | `404` | Explicit allowlist rejects |
| `ENVIRONMENT` unset | `404` | `404` | Models default to `development`, but `environment` is absent from `model_fields_set` |
| `ENVIRONMENT=` (empty) | `404` | `404` | Not in allowlist |
| `ENVIRONMENT=prod` | `404` | `404` | Unknown value; fail closed |
| `ENVIRONMENT=staging ` | `404` | `404` | Exact-value allowlist; fail closed |
| Gateway `/api/v1/test/...` | `404 ROUTE_NOT_FOUND` | `404 ROUTE_NOT_FOUND` | Gateway router has no test route in any environment |

**Operational note:** an explicit `ENVIRONMENT=development` is intentionally trusted. If staging is incorrectly configured as `development`, the hooks remain available. Deployment configuration must set `staging`; the code cannot distinguish an intentionally misconfigured environment from a genuine local development process.

---

## 5. Validation results

### 5.1 Focused tests

Command:

```text
venv/bin/python -m pytest tests/test_security_hardening.py tests/test_gateway.py tests/test_user_auth_lifecycle.py -q
```

Result:

```text
49 passed in 8.07s
```

Command:

```text
venv/bin/python -m pytest tests/test_staging_operational_readiness.py -q -k 'configuration or secrets'
```

Result:

```text
1 passed, 4 deselected in 1.25s
```

The tests use `httpx.ASGITransport` against the in-process FastAPI apps. They do not send requests to the running containers.

### 5.2 Ruff

Command:

```text
venv/bin/ruff check src/services/user/app.py src/services/user/config.py tests/conftest.py tests/test_security_hardening.py tests/test_gateway.py
```

Result: **passed** — `All checks passed!`

`ruff format --check` was also run. It reports existing formatting differences in:

- `src/services/user/app.py` (pre-existing multi-line expressions outside this task's block)
- `tests/test_gateway.py` (pre-existing multipart/body formatting)
- `tests/test_security_hardening.py` (pre-existing allowlist string formatting)

The new code was formatted to match Ruff's expected style. No mass reformat was applied because it would have changed unrelated pre-existing work. **This formatting check is not clean; the remaining differences are pre-existing and non-functional.**

### 5.3 Pyright

Initial command without the virtual-environment interpreter could not resolve third-party imports. The applicable command with the project interpreter was:

```text
venv/bin/pyright --pythonpath venv/bin/python src/services/user/app.py src/services/user/config.py
```

Result:

```text
0 errors, 0 warnings, 0 informations
```

### 5.4 Diff sanity check

```text
git diff --check -- src/services/user/app.py src/services/user/config.py tests/test_gateway.py tests/test_security_hardening.py
```

Result: no whitespace errors.

### 5.5 Not run

The following were intentionally **not** run:

- Live HTTP requests against `localhost:8000` or `localhost:8001`.
- Browser E2E (`tests/test_browser_e2e.py`), because it requires the running containers and Mailpit.
- PostgreSQL recovery/concurrency tests, because they would touch a database.
- Any migration, backup, restore, or database query.
- Any image build, retag, container start/stop/recreate, or deployment.
- Full repository-wide test suite.

No runtime protection is claimed. The running containers predate these source changes.

---

## 6. Remaining risks

1. **Running containers are not protected.** The current User image still has the old production-only guard. A rebuild is required before this source change has runtime effect.
2. **Direct service-port exposure remains a deployment concern.** The current Compose/runtime publishes application service ports including `8001`; the Gateway does not proxy `/test/*`, but direct host/network access depends on firewall and staging network policy. This task did not change Compose port publishing.
3. **Explicit `ENVIRONMENT=development` is trusted.** A staging deployment misconfigured as `development` would still expose the hooks. Configuration review and deployment gates must set `staging`.
4. **The routes remain registered but guarded.** A future refactor that removes the dependency or bypasses FastAPI routing could regress the behavior. The focused route tests are the regression guard.
5. **Gateway protection is route-table based.** The current Gateway has no test route; a future router rule could accidentally add one. The new Gateway test would catch that in CI, but there is no separate runtime denylist.
6. **Formatting check is not clean.** The remaining Ruff format differences are pre-existing in touched files; no unrelated reformat was performed.
7. **No live verification.** Staging/production behavior is proven at ASGI/source level only; it has not been verified against a rebuilt staging container or the live stack.

---

## 7. Safety confirmation

- No container or image was built, retagged, loaded, exported, deleted, started, stopped, restarted, or recreated.
- `docker ps` was used only to confirm the existing containers were unchanged.
- No migration, backup, restore, or database query was executed by this task.
- No database data or schema was modified.
- No source, configuration, or runtime deployment was changed beyond the files listed below.
- No secrets, OTP values, passwords, credentials, or sensitive environment values are included in this report.
- No work was staged, committed, reset, cleaned, or stashed.
- All pre-existing tracked and untracked work was preserved.
- Exactly one new report file was created.

---

## 8. Files modified or created by this task

**Modified**

1. `src/services/user/app.py` — replaced production-only checks with a request-level fail-closed dependency on both test routes.
2. `src/services/user/config.py` — added explicit-environment `test_endpoints_enabled` policy.
3. `tests/conftest.py` — set explicit `ENVIRONMENT=test` for the test suite. This file was already untracked before this task.
4. `tests/test_security_hardening.py` — added development/test allow, staging/production/invalid, malformed-request, and missing-environment coverage.
5. `tests/test_gateway.py` — added router and HTTP assertions that test-only routes are not exposed through the Gateway.

**Created**

6. `docs/audit/task_031_2_8_staging_security_guard_report.md`

No Gateway source, Compose file, migration, runtime configuration, image, container, or database file was modified.
