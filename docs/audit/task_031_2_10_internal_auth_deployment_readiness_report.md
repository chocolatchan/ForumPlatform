# Task 031.2.10 — Internal Service Authentication Deployment Readiness Report

**Date:** 2026-10-23
**Scope:** Source-level and configuration-level internal service authentication readiness
**Status:** Source and configuration validated; live deployment behavior not verified
**Canonical contract:** `INTERNAL_SERVICE_SECRET`, `X-Internal-Service-Key`, and
`src/common/internal_auth.py`

---

## 1. Executive Summary

The repository already used the canonical internal authentication contract in the
service callers, receivers, Compose wiring, and Gateway forwarding path. One
fail-open configuration gap was demonstrated and corrected:

- Enabling `INTERNAL_SERVICE_AUTH_BYPASS` while leaving `ENVIRONMENT` unset was
  accepted because `environment` defaulted to `development`.
- The bypass now requires `ENVIRONMENT` to be explicitly set to `development` or
  `test`. A missing, staging, production, empty, or unknown environment is rejected
  when the bypass is enabled.

No new authentication mechanism was introduced. The shared verifier remains
`src/common/internal_auth.py`; internal receivers continue to use their service-specific
`verify_internal_service_key` dependency, which delegates to the shared verifier.

Source-level authentication checks, Compose static validation, and focused tests pass.
The running containers, images, runtime configuration, and databases were not inspected
as a live deployment, were not modified, and must not be assumed to use the canonical
contract.

---

## 2. Initial Findings

### 2.1 Confirmed canonical behavior

| Check | Finding |
|---|---|
| Shared verifier | `src/common/internal_auth.py` defines `X-Internal-Service-Key` and a single fail-closed `verify_internal_service_request` path. |
| Missing header | Rejected with 401 unless `internal_service_auth_bypass` is enabled. |
| Incorrect header | Always rejected with 401, including when bypass is enabled. |
| Empty configured secret | Rejected at settings startup by `BaseServiceSettings`. |
| Legacy environment variable | No source, Compose, active runbook, or template use of `INTERNAL_SERVICE_KEY` was found. |
| Legacy header | No source use of `X-Internal-Key` was found. |
| User receiver | `GET /internal/users/{user_id}` uses `verify_internal_service_key`. |
| Content receivers | `POST /internal/attachments/{id}/claim-avatar`, `POST /internal/attachments/{id}/release-avatar`, and `POST /internal/events` use `verify_internal_service_key`. |
| Notification receiver | `POST /internal/events` uses `verify_internal_service_key`. |
| Search receiver | `POST /internal/events` uses `verify_internal_service_key`. |
| Interaction/Moderation receivers | No internal HTTP endpoints were found. Their outbox workers use the canonical header. |
| Outbox callers | `OutboxWorker` creates one HTTP client with the canonical header and sends `/internal/events` payloads. |
| Notification caller | Notification-to-User email lookup sends `X-Internal-Service-Key` to `GET /internal/users/{user_id}`. |
| User callers | User-to-Content avatar claim and release calls send `X-Internal-Service-Key`. |
| Compose | All six application services receive `INTERNAL_SERVICE_SECRET` with required interpolation and `INTERNAL_SERVICE_AUTH_BYPASS` defaulting to `false`. |
| Gateway routing | No internal path pattern is registered; `/api/v1/internal/*` is not forwarded. |
| Gateway forwarding | `filter_headers` strips client-supplied `x-internal-service-key`. |

### 2.2 Demonstrated gap: missing environment silently enabled the bypass

Before correction, the following configuration was accepted:

- `internal_service_auth_bypass=True`
- no explicit `ENVIRONMENT`
- no environment file (`_env_file=None`)

Pydantic supplied the default `environment="development"`, and the validator only
checked whether the resulting value was `development` or `test`. The configuration was
therefore accepted even though the deployment had not declared an environment.

This was demonstrated directly:

```text
bypass=True environment='development' environment_explicitly_set=False
```

After correction, the same configuration raises a validation error requiring explicit
`ENVIRONMENT`. An explicit process environment or constructor value continues to work:

```text
ENVIRONMENT=development + bypass=True -> accepted with environment_explicitly_set=True
ENVIRONMENT missing + bypass=True    -> rejected
```

### 2.3 Other observations

- `docker-compose.yml` sets `ENVIRONMENT: ${ENVIRONMENT:-development}` for services.
  A Compose deployment therefore injects an explicit container value; staging and
  production must still set `ENVIRONMENT=staging` or `ENVIRONMENT=production`
  deliberately.
- The Compose file publishes application ports `8001-8006` directly to the host.
  The Gateway does not expose internal routes, but direct host/network access to a
  service would bypass the Gateway. Firewall/security-group restrictions remain an
  operational requirement and outside this task's network-policy scope.
- No `.github` workflow or other CI configuration was present in the repository, so
  CI-based validation could not be audited.
- The currently running images/containers were not inspected during this task. No
  conclusion is made about whether they predate the canonical contract or which
  environment variable/header they use.

---

## 3. Internal-Call Inventory

The inventory below distinguishes protected `/internal/*` calls from service-to-service
calls to public APIs. The latter do not use the internal key and continue to use caller
cookies or service-appropriate identity.

### 3.1 Protected internal-auth calls

All protected receivers use the shared verifier through their service-specific
`verify_internal_service_key` dependency. The caller and receiver both use the same
configured `INTERNAL_SERVICE_SECRET`.

| # | Caller | Receiver | Method and path | Header | Caller configuration | Receiver dependency / configuration | Timeout and retry | Public Gateway reachable | Coverage |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Notification Service (`events.py`) | User Service | `GET /internal/users/{user_id}` | `X-Internal-Service-Key` | `notification_settings.internal_service_secret` | `user_auth.verify_internal_service_key`; `user_settings.internal_service_secret` | 5 s HTTP client timeout; no retry on failure; lookup returns `None`/logs on failure | No; no Gateway route for `internal/*` | Notification caller test; User endpoint test; shared-verifier test |
| 2 | User Service (`set_avatar`) | Content Service | `POST /internal/attachments/{attachment_id}/claim-avatar` | `X-Internal-Service-Key` | `user_settings.internal_service_secret` | `content_auth.verify_internal_service_key`; `content_settings.internal_service_secret` | 5 s HTTP client timeout; no automatic retry; caller maps failures to 502/404 | No | User claim caller test; Content endpoint test; shared-verifier test |
| 3 | User Service (`set_avatar` replacement and `delete_avatar`) | Content Service | `POST /internal/attachments/{attachment_id}/release-avatar` | `X-Internal-Service-Key` | `user_settings.internal_service_secret` | `content_auth.verify_internal_service_key`; `content_settings.internal_service_secret` | 5 s HTTP client timeout; release is best-effort and does not retry | No | User release caller test; Content endpoint test; shared-verifier test |
| 4 | Outbox producers whose routing targets Content Service (currently Interaction `ThreadViewCountIncremented`) | Content Service | `POST /internal/events` | `X-Internal-Service-Key` injected by `OutboxWorker` | Producer settings passed as `OutboxWorker(internal_key=...)`; outbox client header | `content_auth.verify_internal_service_key`; `content_settings.internal_service_secret` | 10 s HTTP client timeout; outbox retry with exponential backoff/jitter, max 5 attempts; 4xx except 408/429 are permanent | No | Outbox header test; producer wiring tests; Content endpoint test |
| 5 | Outbox producers routing to Notification Service (User, Content, Interaction, Moderation) | Notification Service | `POST /internal/events` | `X-Internal-Service-Key` injected by `OutboxWorker` | Producer settings passed as `OutboxWorker(internal_key=...)`; outbox client header | `notification_auth.verify_internal_service_key`; `notification_settings.internal_service_secret` | 10 s HTTP client timeout; outbox retry with exponential backoff/jitter, max 5 attempts | No | Outbox header test; producer wiring tests; Notification endpoint test |
| 6 | Outbox producers routing to Search Service (Content, Interaction, Moderation) | Search Service | `POST /internal/events` | `X-Internal-Service-Key` injected by `OutboxWorker` | Producer settings passed as `OutboxWorker(internal_key=...)`; outbox client header | `search_auth.verify_internal_service_key`; `search_settings.internal_service_secret` | 10 s HTTP client timeout; outbox retry with exponential backoff/jitter, max 5 attempts | No | Outbox header test; producer wiring tests; Search endpoint test |

**Outbox producer wiring verified:** User, Content, Interaction, and Moderation each
construct `OutboxWorker` with their own `internal_service_secret`. Notification and
Search have no outbox producer worker in the current service layout.

### 3.2 Other service-to-service calls (not internal-key protected)

These calls use public routes and do not use `X-Internal-Service-Key`. They are included
so the inventory is not limited to routes whose path starts with `/internal`.

| Caller | Receiver | Method and path | Credential / headers | Timeout and retry | Gateway path |
|---|---|---|---|---|---|
| Content, Notification, Search, Interaction, Moderation | User Service | `GET /auth/session` | Session cookie and `X-Request-ID`; no internal key | 5 s; no retry | Public `/api/v1/auth/session` route |
| Search Service | Content Service | `GET /threads?limit=50` | `X-Request-ID`; no internal key | 5 s; no retry | Public `/api/v1/threads` route |
| Interaction Service | Content Service | `GET /threads/{thread_id}` for vote, bookmark, and view flows | `X-Request-ID`; no internal key | 5 s; no retry | Public `/api/v1/threads/{id}` route |
| Moderation Service | Content Service | `GET /threads/{id}`, `GET /comments/{id}`, `PUT /threads/{id}/lock`, `PUT /threads/{id}/pin`, `DELETE /threads/{id}`, `DELETE /comments/{id}`, `PATCH /threads/{id}` | Caller cookies, CSRF token where applicable, `X-Request-ID`; no internal key | 5 s; no retry | Public content/Gateway routes |
| Moderation Service | User Service | `GET /users/{id}`, `PUT /admin/users/{id}/ban`, `PATCH /admin/users/{id}/role` | Caller cookies, CSRF token where applicable, `X-Request-ID`; no internal key | 5 s; no retry | Public user/admin/Gateway routes |

Interaction and Moderation have no internal receiver endpoints. Their protected
service-to-service effects are delivered through their outbox workers or through public
Content/User APIs using the caller's existing authenticated session.

---

## 4. Changes and Rationale

### 4.1 Source change

**`src/common/config.py`**

- When `internal_service_auth_bypass` is `true`, validation now requires
  `"environment"` to be present in `model_fields_set` and to equal `development` or
  `test`.
- The existing staging/production rejection remains in place.
- The field description now states that bypass requires an explicitly configured
  development/test environment and that missing or other environments are rejected.
- No verification code was weakened. A supplied credential is still always checked;
  bypass only permits a missing canonical header in an explicitly permitted environment.

### 4.2 Tests

**`tests/test_internal_service_auth.py`**

Added or expanded coverage for:

- Missing and incorrect internal keys for all four receiver auth modules.
- Shared-verifier delegation for User, Content, Notification, and Search.
- All six protected internal routes rejecting missing, incorrect, and legacy-only
  headers at the ASGI endpoint level.
- Legacy `INTERNAL_SERVICE_KEY` not satisfying the canonical secret requirement.
- Bypass requiring an explicit `ENVIRONMENT`.
- Bypass accepted only with explicitly configured `development` or `test`.
- Bypass accepted when the explicit environment comes from the process environment.
- Staging and production bypass rejection.
- Empty secret rejection.
- Canonical header use by the outbox client, Notification-to-User calls, and both
  User-to-Content avatar calls.
- Outbox producer wiring in User, Content, Interaction, and Moderation.
- Gateway non-routing of internal paths and stripping of the internal header.
- Compose requiring the canonical secret for all six application services and not
  defining a legacy environment key.

### 4.3 Configuration/documentation

**`.env.example`**

- Clarified that `INTERNAL_SERVICE_AUTH_BYPASS` requires `ENVIRONMENT` to be explicitly
  set to `development` or `test`; missing or other environments reject it.

**`docs/operations/incident_recovery_runbook.md`**

- Removed the unsupported zero-downtime/multi-key rotation recommendation.
- Documented the current single-secret contract as a controlled single-cutover
  rotation and explicitly prohibited enabling the bypass or disabling authentication as
  a rotation shortcut.
- Linked the full transition plan in this report.

**`docs/operations/staging_deployment_runbook.md`**

- Added a pre-container-recreation reference to the ordered transition and rollback
  plan, with the caveat that source-only changes do not update running containers.

### 4.4 Deliberately unchanged

- `src/common/internal_auth.py` was already canonical.
- Service-specific receiver auth modules already delegated to the shared verifier.
- `docker-compose.yml` already required the canonical secret for all six services and
  did not define the legacy key.
- Gateway routing and header filtering were already fail-closed for internal paths.
- No new authentication mechanism, service mesh, or network-policy implementation was
  added.

---

## 5. Authentication and Environment Behavior Matrix

| Configuration / request | Missing canonical header | Correct canonical header | Incorrect canonical header | Legacy-only header | Startup result |
|---|---|---|---|---|---|
| `ENVIRONMENT` missing, bypass disabled | 401 | Accepted if secret matches | 401 | 401 (legacy header is ignored) | Valid if a non-empty canonical secret is provided |
| `ENVIRONMENT` missing, bypass enabled | — | — | — | — | **Rejected: explicit development/test environment required** |
| Explicit `development`, bypass disabled | 401 | Accepted | 401 | 401 | Valid with non-empty secret |
| Explicit `test`, bypass disabled | 401 | Accepted | 401 | 401 | Valid with non-empty secret |
| Explicit `development`, bypass enabled | Allowed as missing credential | Accepted | 401 | Allowed only because the canonical header is missing; the legacy header is not authenticated | Valid with non-empty secret |
| Explicit `test`, bypass enabled | Allowed as missing credential | Accepted | 401 | Allowed only because the canonical header is missing; the legacy header is not authenticated | Valid with non-empty secret |
| `staging`, bypass disabled | 401 | Accepted if strong secret matches | 401 | 401 | Requires non-default secret of at least 32 characters |
| `production`, bypass disabled | 401 | Accepted if strong secret matches | 401 | 401 | Requires non-default secret of at least 32 characters |
| `staging` or `production`, bypass enabled | — | — | — | — | **Rejected** |
| Empty or whitespace-only configured secret | 401 / startup failure | — | — | — | **Rejected** |
| Legacy `INTERNAL_SERVICE_KEY` only | — | — | — | — | **Rejected: canonical `INTERNAL_SERVICE_SECRET` remains required** |
| Compose without `INTERNAL_SERVICE_SECRET` | — | — | — | — | **Compose interpolation fails** |
| Compose with a non-empty dummy value | — | — | — | — | Compose configuration validates |

**Note:** Under an explicit development/test bypass, a request with only a legacy
header is allowed because no canonical header is present. That is the documented
missing-credential bypass behavior; the legacy header is not treated as a valid
credential. A wrong canonical header is still rejected even when bypass is enabled.

---

## 6. Validation Results

### 6.1 Source-level authentication tests

Command:

```bash
venv/bin/pytest tests/test_internal_service_auth.py -q
```

Actual result:

```text
62 passed in 2.20s
```

### 6.2 Broader affected test subset

Command:

```bash
venv/bin/pytest tests/test_internal_service_auth.py \
  tests/test_staging_operational_readiness.py \
  tests/test_security_hardening.py \
  tests/test_gateway.py \
  tests/test_outbox_worker.py -q
```

Actual result:

```text
101 passed in 8.01s
```

### 6.3 Ruff

Command:

```bash
venv/bin/ruff check src/common/config.py src/common/internal_auth.py tests/test_internal_service_auth.py
```

Actual result:

```text
All checks passed!
```

### 6.4 Pyright

Command:

```bash
venv/bin/pyright --pythonpath venv/bin/python
```

Actual result:

```text
0 errors, 0 warnings, 0 informations
```

A first invocation without `--pythonpath` used an interpreter that could not resolve
the venv-installed dependencies; the venv-aware invocation above is the authoritative
result.

### 6.5 Compose static validation

Commands were run with `--env-file /dev/null` so the shell environment was the only
source of interpolation values. No configuration output or secret value was printed.

| Case | Command shape | Actual result |
|---|---|---|
| Secret absent | `docker compose --env-file /dev/null config --quiet` | Exit code 1; required `INTERNAL_SERVICE_SECRET` missing |
| Non-empty dummy secret | Same command with a non-production dummy value in the environment | Exit code 0; configuration valid |

This validates Compose interpolation and the required-secret guard. It does not validate
a live container.

### 6.6 Diff hygiene

Command:

```bash
git diff --check
```

Actual result: no output; no whitespace errors.

### 6.7 Validation scope

- **Source-level authentication validated:** yes, through unit/ASGI tests and settings
  validation.
- **Compose configuration validated:** yes, statically, including the missing-secret
  failure and successful interpolation with a dummy value.
- **Live container behavior validated:** no. No running container was queried.
- **Deployment readiness:** source and configuration are ready for a controlled future
  cutover, subject to the prerequisites and runtime checks in Section 7. The running
  deployment is not certified by this report.

---

## 7. Ordered Future Deployment Transition Plan

This plan is documented for a future controlled deployment. It was **not executed** in
Task 031.2.10.

### 7.1 Why source-only changes are insufficient

The running containers execute the image and environment that were started previously.
Changing tracked source, configuration templates, tests, or this report does not update
those processes. A future deployment must rebuild/recreate the application containers
with the canonical source and the same canonical secret on all six services. Because
the running images were not inspected, they must not be assumed to use the canonical
header or environment variable.

### 7.2 Prerequisites before container recreation

1. Confirm a current verified backup and the Task 031.2.9 restore procedure are available.
2. Record the complete current image inventory before changing anything:
   - image names, tags, image IDs, and digests for the Gateway and all six application
     services;
   - the current Compose configuration and container status;
   - the previous environment file or secret reference, stored securely.
3. Generate a new 32+ character random `INTERNAL_SERVICE_SECRET` if rotating. Do not use
   any documented placeholder.
4. Set `ENVIRONMENT=staging` or `ENVIRONMENT=production` explicitly for
   staging/production. Do not rely on an omitted value.
5. Set `INTERNAL_SERVICE_AUTH_BYPASS=false` in staging/production.
6. Ensure the same canonical secret is supplied to User, Content, Interaction,
   Moderation, Notification, and Search. The Gateway does not need the internal secret.
7. Confirm no deployment environment file, host environment, or secret manager entry
   still supplies `INTERNAL_SERVICE_KEY` as an alternate credential.
8. Confirm direct service ports `8001-8006` are blocked from public ingress by firewall
   or security-group policy. Gateway non-routing is not a substitute for network policy.
9. Run the source, Ruff, Pyright, focused-test, Compose, and `git diff --check` gates on
   the exact revision to be deployed.
10. Build and tag replacement images before the cutover, but do not start them until the
    maintenance window.

### 7.3 Cutover sequence

Because the implementation supports one shared secret and one canonical header, there
is no dual-key/dual-header compatibility window. Avoid an uncontrolled rolling restart
in which old and new callers/receivers overlap.

1. Announce the maintenance window and stop external writes through the Gateway.
2. Stop the six application services and the Gateway in a controlled manner. Keep
   PostgreSQL, Redis, and Mailpit available.
3. Preserve the previous image and environment inventory for rollback.
4. Update the protected environment to the canonical secret and explicit environment.
5. Recreate all six application services from the replacement image with the same new
   secret and `INTERNAL_SERVICE_AUTH_BYPASS=false`.
6. Recreate the Gateway. It has no internal route and strips client-supplied internal
   headers.
7. Wait for `/health/live` and `/health/ready` on every application service and the
   Gateway before restoring external traffic.
8. Inspect logs and metrics for startup/configuration failures, 401s on internal calls,
   and outbox delivery errors. Do not print or log the secret value.

### 7.4 Internal-call smoke tests

Use a controlled shell inside the service network. Do not put the secret in shell
history, logs, or report output.

1. With the canonical header and a valid test user ID, call
   `GET /internal/users/{user_id}` and expect the documented contact response.
2. Call the same endpoint without the header and with an incorrect header; expect 401
   for both.
3. Call the same endpoint with only a legacy header; expect 401 unless an explicit
   development/test bypass is active (which must not be the case in staging/production).
4. For event receivers, use an approved, non-destructive synthetic event with a unique
   test ID and verify the documented 200/idempotent behavior, or verify the receiver's
   401 behavior without mutating data if no approved synthetic event is available.
5. Verify `POST /api/v1/internal/events` through the public Gateway is not routed.
6. Verify an externally supplied `X-Internal-Service-Key` is stripped before forwarding.
7. Confirm outbox pending/failed/dead-letter metrics return to normal and no sustained
   401 burst appears after the cutover.

### 7.5 Rollback criteria

Roll back if any of the following occur:

- Any application service or Gateway fails `/health/live` or `/health/ready`.
- Internal calls show sustained 401/403 responses after the new secret is deployed.
- Outbox dead-letter count grows because of authentication failures.
- Avatar claim/release, Notification email lookup, or event ingestion fails.
- Logs show mixed-version or header-contract errors, or the deployment cannot prove all
  six services received the same canonical secret.
- A database restore is required; use the Task 031.2.9 backup/restore procedure and a
  disposable target rather than restoring over live data.

Do not enable `INTERNAL_SERVICE_AUTH_BYPASS`, remove the authentication dependency, or
accept the legacy header as a rollback shortcut.

---

## 8. Rollback Requirements

1. Preserve the previous Gateway and six application image names, tags, IDs, and
   digests, plus the previous Compose configuration and protected environment reference.
2. Preserve a verified database backup and the Task 031.2.9 restore instructions.
3. Keep the previous authenticated secret available in the secure secret store; a
   rollback must restore the previous authenticated contract, not disable authentication.
4. Roll back all six application services and the Gateway as a single controlled
   cutover. Do not leave a mixed set of old and new internal-auth contracts.
5. After rollback, verify health, internal authentication behavior, outbox delivery, and
   the absence of sustained 401/5xx errors.
6. If a database restore is required, use explicit disposable targets and the verified
   backup procedure; do not restore into a live shared database.

---

## 9. Remaining Risks

1. **Live runtime is unverified.** No container, image, runtime environment, or live
   internal request was inspected in this task. The running deployment may not match
   the current source or may use the legacy contract.
2. **Direct service ports remain published.** Compose publishes `8001-8006`; Gateway
   non-routing does not prevent direct access if host/network policy is absent. This was
   not changed because network-policy implementation is outside this task.
3. **Compose supplies a development default for `ENVIRONMENT`.** Staging/production
   deployments must set the environment explicitly; an omitted Compose environment is
   not evidence of a safe deployment.
4. **Unknown environment names do not receive staging/production strong-secret checks.**
   The bypass is fail-closed for any non-development/test value, but a typo such as
   `prod` with bypass disabled still bypasses the staging/production secret-strength
   branch. Deployments must use the documented environment names.
5. **No multi-key rotation window exists.** Secret rotation requires a controlled
   single-cutover or an approved future compatibility design; mixed-version rotation is
   not safe with the current verifier.
6. **No repository CI workflow was found.** CI enforcement of these checks cannot be
   claimed from repository contents.
7. **The bypass remains intentionally available for explicit development/test use.**
   It is not a production mechanism and must remain `false` in staging/production.

---

## 10. Explicit Non-Mutation Statement

The following were **not** changed or executed in Task 031.2.10:

- No container was started, stopped, recreated, inspected for live authentication
  behavior, or otherwise modified.
- No image was built, retagged, pulled, pushed, or modified.
- No application, staging, or production database was mutated, migrated, restored, or
  queried for state-changing behavior.
- No state-changing application endpoint was invoked.
- No secret value or full environment dump was printed.
- No file was staged or committed.
- No existing tracked modification or untracked artifact was reset, cleaned, stashed, or
  discarded.

Task 031.2.9 backup/restore safety work was preserved.

---

## 11. Files Modified or Created by This Task

The workspace already contained substantial pre-existing tracked modifications and
untracked artifacts, including Task 031.2.9 work. The following are the changes made for
Task 031.2.10 on top of that preserved state:

### Modified

- `src/common/config.py`
- `tests/test_internal_service_auth.py`
- `.env.example`
- `docs/operations/incident_recovery_runbook.md`
- `docs/operations/staging_deployment_runbook.md`

### Created

- `docs/audit/task_031_2_10_internal_auth_deployment_readiness_report.md`

### Confirmed unchanged by this task

- `src/common/internal_auth.py`
- Service-specific internal-auth modules
- `docker-compose.yml`
- Gateway routing and forwarding source
- `.env.staging.example`

---

## 12. Acceptance Criteria Assessment

| Criterion | Result |
|---|---|
| Canonical internal authentication contract consistent in source/configuration | Pass for audited source and Compose; live runtime not inspected |
| Unsafe staging/production bypass configurations fail closed | Pass; bypass requires explicit development/test |
| Focused tests cover callers, receivers, and configuration | Pass; 62 focused tests, 101-test affected subset |
| Internal endpoints not publicly exposed through the Gateway | Pass by static routing and header-filter tests |
| Future deployment transition plan documented | Pass; Section 7 |
| No runtime, image, or database mutation | Pass; no such operation was performed |
| Task 031.2.9 preserved | Pass; no reset, clean, stash, or destructive action performed |
