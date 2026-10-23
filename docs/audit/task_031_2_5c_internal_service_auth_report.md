# Task 031.2.5c — Internal Service Secret Alignment and Fail-Closed Authentication

**Date:** 2026-10-23  
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`  
**Branch:** `master`  
**HEAD:** `bf3384c4c3b53b5f826ef119f2ad549997ff1d63`  
**Mode:** Source, configuration, and isolated-test correction. No deployment, rebuild, container operation, image operation, live database access, migration, or runtime change.

---

## 1. Verdict

**READY WITH EXPLICIT RISKS**

The source-level configuration mismatch and implicit development bypass were corrected. Internal endpoints now fail closed by default, all four receivers use one constant-time verification policy, the outbox worker no longer has a hardcoded credential fallback, and Compose now supplies the same environment variable name that the application reads.

The remaining risks are deployment and architecture risks, not unfinished source work:

1. Existing deployments that still supply only `INTERNAL_SERVICE_KEY` must add `INTERNAL_SERVICE_SECRET` before the next rebuild/start. Compose now fails explicitly when it is absent.
2. Staging/production validators only run when `ENVIRONMENT` is actually set to `staging` or `production`. Compose now passes `ENVIRONMENT` through, but deployment configuration must set it correctly.
3. The existing shared cluster-wide credential model is preserved. There is no per-service credential identity; compromise of one service still permits calls to all protected internal endpoints. Re-architecting that trust model is outside this task.
4. One pre-existing formatting failure remains in `src/services/content/auth.py` and was not reformatted.

No live runtime was changed.

---

## 2. Initial snapshot

At the start of Task 031.2.5c:

- Branch: `master`
- HEAD: `bf3384c4c3b53b5f826ef119f2ad549997ff1d63`
- Staged files: none (`git diff --cached` empty)
- Tracked modified files: 10
- Untracked files: 16

Tracked modified files at start:

```text
.env.example
docker-compose.yml
migrations/notifications/env.py
src/services/notification/config.py
src/services/notification/email_worker.py
src/services/notification/models.py
src/services/user/app.py
src/services/user/schemas.py
tests/test_email_dispatch_worker.py
tests/test_user_auth_lifecycle.py
```

The 16 untracked files were the prior Task 031.1/031.2.3–031.2.5b audit reports, the Notification lease migration, and the PostgreSQL lease test. All prior work remains intact.

No files were staged, committed, stashed, reset, cleaned, or discarded during this task.

---

## 3. Discovered canonical contract

### 3.1 Protected internal endpoints

| Service | Endpoint | Protection |
|---|---|---|
| User | `GET /internal/users/{user_id}` | `verify_internal_service_key` dependency |
| Content | `POST /internal/attachments/{attachment_id}/claim-avatar` | `verify_internal_service_key` dependency |
| Content | `POST /internal/attachments/{attachment_id}/release-avatar` | `verify_internal_service_key` dependency |
| Content | `POST /internal/events` | `verify_internal_service_key` dependency |
| Notification | `POST /internal/events` | `verify_internal_service_key` dependency |
| Search | `POST /internal/events` | `verify_internal_service_key` dependency |

### 3.2 Canonical header

`X-Internal-Service-Key`

All internal callers already used this header before the task:

- User avatar claim/release calls to Content: `src/services/user/app.py`
- Notification-to-User recipient lookup: `src/services/notification/events.py`
- Outbox delivery worker: `src/common/outbox_worker.py`
- Benchmark Search ingestion: `scripts/run_benchmarks.py`

No production caller uses `X-Internal-Key`. No source or test caller uses the old `INTERNAL_SERVICE_KEY` environment name after this change.

### 3.3 Canonical environment variable

`INTERNAL_SERVICE_SECRET`

Evidence for choosing this name over `INTERNAL_SERVICE_KEY`:

- The Pydantic field is `internal_service_secret` in `src/common/config.py`, whose default environment name is `INTERNAL_SERVICE_SECRET`.
- `.env.example`, `.env.staging.example`, the staging deployment runbook, and the incident recovery runbook all document `INTERNAL_SERVICE_SECRET`.
- Compose was the only outlier and supplied `INTERNAL_SERVICE_KEY` with a hardcoded default.

### 3.4 Trust model

The current architecture uses one shared cluster-wide credential. There is no repository evidence of distinct per-service credentials, service identities, signed requests, or credential rotation scopes. This task preserves that model rather than inventing a new one.

Gateway behavior:

- The Gateway forwards only public API routes.
- The Gateway strips client-supplied `x-internal-service-key` through `UNTRUSTED_CLIENT_HEADERS` in `src/gateway/forwarder.py`.
- The Gateway does not attach an internal credential to public forwarding.
- Direct service-to-service calls attach the shared credential.

### 3.5 Environment behavior

| Environment | Internal secret | Development bypass |
|---|---|---|
| `development` | Required, non-empty | Disabled by default; can only be enabled explicitly |
| `test` | Required, non-empty | Disabled by default; can only be enabled explicitly |
| `staging` | Non-empty, non-default, at least 32 characters | Rejected if enabled |
| `production` | Non-empty, non-default, at least 32 characters | Rejected if enabled |

A missing, empty, or whitespace-only `INTERNAL_SERVICE_SECRET` now fails settings validation. There is no hardcoded secret fallback.

The explicit bypass variable is `INTERNAL_SERVICE_AUTH_BYPASS`. It defaults to `false`.

---

## 4. Exact changes and rationale

### 4.1 Shared verification policy

**New file:** `src/common/internal_auth.py`

- Defines the canonical header constant `X-Internal-Service-Key`.
- Verifies supplied credentials with `hmac.compare_digest`.
- Rejects an empty expected secret.
- Rejects a missing header unless `internal_service_auth_bypass` is explicitly enabled.
- Still rejects an incorrect supplied header when bypass is enabled.
- Preserves the existing `401` contract and details:
  - `Internal service key missing.`
  - `Internal service key invalid.`

### 4.2 Receiver alignment

Updated these functions to delegate to the shared policy:

- `src/services/user/auth.py`
- `src/services/content/auth.py`
- `src/services/search/auth.py`
- `src/services/notification/auth.py`

The previous implicit `environment in ("development", "test")` bypass was removed from all four receivers.

### 4.3 Settings

Updated `src/common/config.py`:

- `internal_service_secret` no longer defaults to a predictable value. Missing configuration resolves to an empty value and is rejected by a field validator.
- Added `internal_service_auth_bypass: bool = False`.
- The model validator rejects bypass outside `development` and `test`.
- Staging/production still require a non-default secret of at least 32 characters.
- Added documented example values to the insecure-secret set:
  - `default-internal-service-secret-key-32chars`
  - `default-dev-internal-key`
  - `replace-with-secure-internal-secret-min-32-chars`
  - `staging-internal-service-secret-must-be-32-chars-long`

### 4.4 Outbox worker

Updated `src/common/outbox_worker.py`:

- `internal_key` is now a required constructor argument with no default.
- Empty or whitespace-only `internal_key` raises `ValueError`.
- The outbound client uses the shared canonical header constant.
- All application call sites already pass each service’s `internal_service_secret`.

### 4.5 Compose and environment templates

Updated `docker-compose.yml` for all six application services:

- Replaced `INTERNAL_SERVICE_KEY` with required `INTERNAL_SERVICE_SECRET`.
- Added pass-through `ENVIRONMENT` so staging/production validation can actually run in Compose deployments.
- Added pass-through `INTERNAL_SERVICE_AUTH_BYPASS`, defaulting to `false`.
- Compose now fails explicitly if `INTERNAL_SERVICE_SECRET` is not set.

Updated `.env.example`:

- Documents that `INTERNAL_SERVICE_SECRET` is required.
- Documents the explicit, local/test-only bypass.

Updated `.env.staging.example`:

- Documents that the example staging secret must be replaced.
- Documents that bypass must remain false in staging.

No real `.env` file or secret-bearing runtime file was edited.

### 4.6 Caller and test alignment

- Updated `scripts/run_benchmarks.py` to use `search_settings.internal_service_secret` instead of a hardcoded credential.
- Added `tests/conftest.py` with a deterministic test-only internal secret and bypass disabled.
- Updated existing tests that instantiated `OutboxWorker` to pass the required credential.
- Added internal auth headers to existing in-process tests that formerly relied on the implicit development bypass.
- Added focused coverage in `tests/test_internal_service_auth.py`.

---

## 5. Test coverage

New focused tests cover:

1. Missing internal secret fails settings validation.
2. Empty/whitespace internal secret fails settings validation.
3. Staging and production reject the bypass setting.
4. Documented example/default secrets are rejected in staging.
5. Missing header is rejected with bypass disabled for User, Content, Search, and Notification receivers.
6. Incorrect header is rejected with bypass disabled for all four receivers.
7. Correct header is accepted for all four receivers.
8. Explicit bypass allows a missing header but still rejects an incorrect header.
9. `OutboxWorker` has no default internal key and rejects an empty key.
10. `OutboxWorker` sends `X-Internal-Service-Key`.
11. Notification-to-User lookup sends `X-Internal-Service-Key`.
12. User avatar claim sends `X-Internal-Service-Key`.
13. `GET /internal/users/{user_id}` returns the canonical `401 UNAUTHORIZED` envelope for missing and incorrect headers.
14. The Gateway strips an externally supplied `X-Internal-Service-Key`.
15. Public session authentication still requires a cookie.
16. Public CSRF authentication still requires a CSRF token.

### 5.1 Test results

```text
venv/bin/pytest -q \
  tests/test_internal_service_auth.py \
  tests/test_config.py \
  tests/test_outbox_worker.py \
  tests/test_security_hardening.py \
  tests/test_gateway.py \
  tests/test_staging_operational_readiness.py::test_staging_configuration_rejects_unsafe_defaults \
  tests/test_notification_service.py \
  tests/test_search_service.py \
  tests/test_e2e_integration.py \
  tests/test_content_service.py

87 passed in 16.88s
```

The focused file alone produced `33 passed`.

Tests intentionally **not** run because they require a live PostgreSQL instance or are outside the isolated-test constraint:

- `tests/test_outbox_hardening.py`
- `tests/test_outbox_concurrency_live.py`
- The worker lifecycle tests in `tests/test_staging_operational_readiness.py`
- `tests/test_email_worker_lease_pg.py`
- Browser E2E, migration, and stress tests

Their `OutboxWorker` call sites were updated for the required `internal_key`, but they were not executed.

### 5.2 Static validation

```text
venv/bin/ruff check <all files changed by this task>
All checks passed!

venv/bin/pyright --pythonpath venv/bin/python
0 errors, 0 warnings, 0 informations

git diff --check
pass

INTERNAL_SERVICE_SECRET=<compose-config-only dummy> docker compose config --quiet
pass
```

Static consistency checks confirmed:

- No `INTERNAL_SERVICE_KEY` environment variable remains in source, tests, templates, or Compose.
- No `X-Internal-Key` header remains.
- All internal callers use `X-Internal-Service-Key`.
- All six Compose application services receive the canonical secret and bypass variables.

### 5.3 Pre-existing formatting failure

`ruff format --check` reports one pre-existing issue:

```text
src/services/content/auth.py:121
extra blank line before def require_role(...)
```

This exists in `HEAD` and was not introduced by Task 031.2.5c. It was deliberately not reformatted because the file is already part of the preserved working tree and the change is unrelated to internal authentication. All other changed Python files are formatted.

---

## 6. Remaining compatibility and deployment prerequisites

1. **Set `INTERNAL_SERVICE_SECRET` before the next deployment.**
   - Compose now requires it.
   - The old `INTERNAL_SERVICE_KEY` is no longer read.
   - The running containers were not changed and still have the old environment. They will need the new variable on the next rebuild/restart.

2. **Set `ENVIRONMENT` correctly in staging and production.**
   - Compose now passes `ENVIRONMENT` through.
   - If it remains unset, the default is `development`, so staging/production secret and bypass validation will not run.
   - Production-like deployments must set `ENVIRONMENT=staging` or `ENVIRONMENT=production`.

3. **Local development.**
   - A non-empty `INTERNAL_SERVICE_SECRET` is still required.
   - To allow missing internal headers during local development or tests, set `INTERNAL_SERVICE_AUTH_BYPASS=true` explicitly.
   - The bypass is rejected in staging and production.
   - The bypass does not permit an incorrect supplied credential.

4. **Replace staging example values.**
   - `.env.staging.example` intentionally contains a documented example secret that staging validation now rejects.
   - Replace it with a random, non-default value of at least 32 characters before deploying.

5. **Shared credential risk remains.**
   - Any service that holds the shared secret can call every protected internal endpoint.
   - This is the existing architecture and was not expanded into per-service credentials or request signing.

6. **No secret rotation was performed.**
   - Existing deployments must rotate or provision the canonical secret through their normal secret-management process.

---

## 7. Runtime and repository integrity confirmation

- No container was rebuilt, restarted, stopped, recreated, or removed.
- No image was loaded, retagged, replaced, or deleted.
- No live database was connected to or mutated.
- No migration or schema operation was performed.
- No real `.env` or secret-bearing environment file was edited.
- No running runtime configuration was changed.
- `docker compose config` was run only as a static configuration check with a dummy value; it did not start or modify any container.
- No browser E2E, live database, migration, or stress test was run.
- No real secret value was added to source, logs, or this report. The test configuration contains only a deterministic, non-production test credential.
- Nothing was staged or committed. `HEAD` remains `bf3384c4c3b53b5f826ef119f2ad549997ff1d63`.
- All Task 031.1 and Task 031.2.3–031.2.5b changes remain in the working tree.
