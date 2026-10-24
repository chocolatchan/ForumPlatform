# Environment-Aware Email Routing and Test-Domain Suppression

**Task:** 031.3.H - Environment-Aware Email Routing and Test-Domain Suppression

**Date:** 2026-10-24

**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`

**Base commit:** `b38a693a7e140584cdf7eae0e1e3f67945cdb328` (worktree also contains the uncommitted Task 031.3.A-G changes)

**Overall verdict:** **PASS WITH EXPLICIT RISKS**

The implementation removes recipient allowlist authorization, pins development/test
SMTP delivery to Mailpit, adds exact production suppression for `test.dev` and its
subdomains, and maps suppression to non-delivered outcomes in both User OTP delivery
and the Notification outbox. Automated email, workflow, configuration, Compose, and
guard tests pass. No external email was sent and no runtime container was rebuilt or
restarted for this task.

Production readiness is **not** claimed from mocked SMTP tests. Live production SMTP
delivery, provider-level bounce handling, and a post-change live Mailpit workflow
remain explicit risks because the running Task 031.3.G containers predate this change
and were not recreated.

## 1. Executive Summary

| Area | Result |
|---|---|
| Remove `DEV_EMAIL_ALLOWLIST` authorization | PASS |
| Development/test routes every recipient to Mailpit | PASS - configuration + adapter tests |
| Preserve development/test recipient metadata | PASS - original `To` plus routing headers |
| Production ordinary external delivery | PASS - mocked SMTP transport |
| Production exact `test.dev` + subdomain suppression | PASS |
| Case-insensitive suppression | PASS |
| Lookalike domains such as `not-test.dev` allowed | PASS |
| Suppression before SMTP transport | PASS - adapter checks before client construction |
| Suppressed messages never reported delivered | PASS - adapter error, User safe failure, outbox dead-letter |
| Explicit/validated environment and fail-closed SMTP config | PASS |
| Development/test and production transport separation | PASS - Compose maps only `DEV_SMTP_*`; production creds rejected by dev/test config |
| Shared adapter retained for all senders | PASS |
| OTP, account uniqueness, and unrelated services unchanged | PASS |
| Email/routing/workflow/config/guard tests | PASS - 112 focused + 52 workflow/config tests |
| Ruff / Pyright / Compose config / `git diff --check` | PASS |
| Full repository test suite | 369 passed, 21 skipped, 3 unrelated failures |
| Live Mailpit after this change | NOT RUN - containers not rebuilt |
| Live production SMTP | NOT RUN |

## 2. Implemented Behavior

### 2.1 Development and test

- `src/common/email/config.py` validates `ENVIRONMENT` against an explicit allowed
  set and requires SMTP delivery to use exactly `mailpit:1025`, plaintext, and no
  credentials.
- `SmtpEmailAdapter` sends every application recipient to the configured Mailpit
  transport. There is no recipient allowlist or recipient-based rejection in
  development/test.
- The original recipient remains in the SMTP `To` header and in the envelope
  recipient list. Messages also carry:
  - `X-Forum-Routing-Environment: development|test`
  - `X-Forum-Original-Recipients: ...`
- Mailpit no longer has `MP_SMTP_ALLOWED_RECIPIENTS`, so external-looking recipients
  can be observed in Mailpit without a Mailpit-level rejection.
- The per-container SMTP egress guard remains configured for `user-service` and
  `notification-service`; guard regression tests pass.

### 2.2 Production

- `src/common/email/policy.py` defines exact-domain suppression:
  - `test.dev`
  - any dot-delimited subdomain such as `sub.test.dev`
  - case-insensitive matching
  - a single trailing dot is normalized
  - no substring matching, so `not-test.dev`, `mytest.dev`, `test.devil`, and
    `test.dev.example.com` are allowed
- `SmtpEmailAdapter.send()` checks production suppression before building or invoking
  the SMTP client. Suppressed messages raise `EmailSuppressedError` with:
  - `is_suppressed=True`
  - `suppressed_recipients`
  - `domains`
  - a warning log containing the suppressed domains and recipients
- There is no redirect or fallback external address.
- If any recipient in a multi-recipient message is suppressed, the whole message is
  suppressed before transport.

### 2.3 Outcome mapping

| Path | Successful delivery | SMTP/transient error | Suppression |
|---|---|---|---|
| User OTP (`deliver_otp`) | sink write and pending challenge remains usable | `EmailDeliveryError`, pending state becomes `delivery_failed` | `EmailDeliveryError(is_suppressed=True)`, no sink write, pending state becomes `delivery_failed` |
| Notification outbox | row becomes `delivered` | row becomes `failed` with backoff, then `dead_letter` at max attempts | row becomes `dead_letter` immediately, `delivered_at` remains `NULL`, `last_error` records suppression |

The Notification worker logs suppression separately and does not retry a suppressed
message.

## 3. Configuration Evidence

### 3.1 Development/test Compose mapping

`docker-compose.yml` maps only non-production transport variables:

```yaml
x-email-environment: &email-environment
  EMAIL_DELIVERY_BACKEND: ${EMAIL_DELIVERY_BACKEND:-smtp}
  SMTP_HOST: ${DEV_SMTP_HOST:-mailpit}
  SMTP_PORT: ${DEV_SMTP_PORT:-1025}
  SMTP_USER: ${DEV_SMTP_USER:-}
  SMTP_PASSWORD: ${DEV_SMTP_PASSWORD:-}
  SMTP_USE_TLS: ${DEV_SMTP_USE_TLS:-false}
  SMTP_STARTTLS: ${DEV_SMTP_STARTTLS:-false}
```

The application still reads `SMTP_*`; the Compose stack supplies those values from
development-specific variables. Production must use a separate deployment
configuration with its own `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, and
`SMTP_PASSWORD`.

### 3.2 Fail-closed rules

- Missing/invalid environment is rejected by `validate_email_configuration`.
- SMTP delivery additionally requires the environment to be explicitly supplied.
- Development/test rejects:
  - non-Mailpit hosts
  - loopback hosts
  - non-1025 ports
  - TLS/STARTTLS
  - any SMTP credentials
- Production rejects Mailpit and loopback hosts and requires an external host.
- Memory delivery remains available for deterministic tests and still requires any
  supplied environment value to be valid.

### 3.3 Allowlist removal

No `DEV_EMAIL_ALLOWLIST`, `dev_email_allowlist`, `get_allowed_recipients`, or
`is_recipient_allowed` references remain in `src/`, `tests/`, `docker-compose.yml`,
`.env.example`, or `.env.staging.example`. Mailpit recipient filtering was removed.
Only the new negative Compose tests mention the removed setting names.

## 4. Test Evidence

### 4.1 Focused email, configuration, policy, and guard suites

Command:

```bash
./venv/bin/pytest tests/common/email tests/test_email_delivery.py \
  tests/test_email_dispatch_worker.py tests/network -q
```

Result: **112 passed**.

Coverage includes:

- development/test routing for recipients inside and outside `test.dev`
- Mailpit-only development/test configuration and rejection of external SMTP
- rejection of production credentials in development/test
- production ordinary external delivery through mocked SMTP
- exact/case-insensitive suppression of `test.dev` and subdomains
- lookalike-domain non-suppression
- suppression observability via logs and typed error metadata
- SMTP connection and recipient-refusal error mapping
- User registration success/failure, resend failure, and suppression mapping
- memory backend accepting every recipient with no allowlist
- Notification delivery, transient retry, max-attempt dead-letter, recipient
  rejection dead-letter, and suppressed dead-letter transitions
- Notification duplicate-event idempotency and mandatory/optional event behavior
- SMTP egress guard static regression tests

### 4.2 User workflows and service configuration

Command:

```bash
./venv/bin/pytest tests/test_user_auth_lifecycle.py \
  tests/test_security_hardening.py tests/test_config.py \
  tests/test_staging_operational_readiness.py tests/test_frontend_e2e.py -q
```

Result: **52 passed**.

These suites cover registration OTP, OTP resend, password recovery, recovery resend,
email-change verification, login/session behavior, CSRF/security settings, and the
frontend lifecycle without allowlist-based email fixtures.

### 4.3 Full repository suite

Command:

```bash
./venv/bin/pytest -q
```

Result: **369 passed, 21 skipped, 3 failed** in 248.78 seconds.

The three failures are outside the email routing path:

1. `tests/test_avatar_email_change_pg.py::test_avatar_lifecycle_and_permissions` -
   live gateway returned HTTP 500 for `PUT /profile/avatar`.
2. `tests/test_avatar_email_change_pg.py::test_avatar_unowned_attachment_rejection` -
   same live avatar endpoint returned HTTP 500 instead of 403.
3. `tests/test_browser_e2e.py::test_browser_thread_deletion_conflict_modal` -
   Playwright timed out waiting for `#btn-detail-delete` in the browser E2E fixture.

Both avatar failures reproduced when re-run alone. They do not exercise email
configuration, the shared adapter, Mailpit, or the Notification outbox. They are
recorded as pre-existing/unrelated live-stack failures rather than claimed as passing.

### 4.4 Static validation

| Check | Result |
|---|---|
| `./venv/bin/ruff check src tests` | PASS |
| `./venv/bin/pyright --pythonpath ./venv/bin/python` | PASS - 0 errors |
| `INTERNAL_SERVICE_SECRET=default-dev-internal-key docker compose config --quiet` | PASS |
| `git diff --check` | PASS |

Plain `./venv/bin/pyright` without `--pythonpath` reports missing third-party imports
because it does not resolve the project virtual environment. The explicit virtualenv
invocation reports zero errors.

## 5. Changed Files

### Production source

| File | Change |
|---|---|
| `src/common/email/policy.py` | New exact-domain suppression and environment policy helpers |
| `src/common/email/config.py` | Removed allowlist contract; added explicit environment, credential separation, and fail-closed validation |
| `src/common/email/adapter.py` | Mailpit-only dev/test routing, pre-transport production suppression, typed suppression error, routing metadata headers |
| `src/common/email/__init__.py` | Exports `EmailSuppressedError` |
| `src/services/user/config.py` | Removed allowlist settings/helpers; validates new shared contract |
| `src/services/user/otp_delivery.py` | Removed allowlist preflight; maps suppression to safe User delivery failure |
| `src/services/notification/config.py` | Removed allowlist settings/helpers; validates new shared contract |
| `src/services/notification/email_worker.py` | Removed allowlist preflight; maps suppression to immediate dead-letter |

### Configuration and operations

| File | Change |
|---|---|
| `docker-compose.yml` | Removed allowlist and Mailpit recipient filter; maps development transport from `DEV_SMTP_*` only |
| `.env.example` | Removed allowlist; documented `DEV_SMTP_*` and production separation |
| `.env.staging.example` | Removed allowlist/Mailpit filter; aligned non-production transport variables |
| `docs/operations/staging_deployment_runbook.md` | Updated email contract and suppression policy |

### Tests

| File | Change |
|---|---|
| `tests/common/email/test_routing_policy.py` | New exact-domain policy tests |
| `tests/common/email/test_adapter.py` | Rewritten routing, suppression, credential, and SMTP-error tests |
| `tests/common/email/test_email_config_contract.py` | Rewritten environment/transport contract tests |
| `tests/common/email/test_mailpit_compose.py` | Removed allowlist expectations; added credential-separation and no-filter checks |
| `tests/test_email_delivery.py` | Removed allowlist tests; added no-allowlist and suppression mapping tests |
| `tests/test_email_dispatch_worker.py` | Removed allowlist mocks; added suppression dead-letter tests |
| `tests/test_user_auth_lifecycle.py` | Removed allowlist fixture mutations |
| `tests/test_security_hardening.py` | Removed allowlist fixture mutations |
| `tests/test_frontend_e2e.py` | Removed allowlist fixture mutation |

## 6. Safety and Scope

- No external email was sent during validation. Any live-stack test delivery was
  directed to the controlled Mailpit sink; Task H's own focused tests used mocked or
  memory transports.
- No external SMTP provider was contacted.
- No container was rebuilt, recreated, or restarted for Task 031.3.H.
- OTP generation/verification, account uniqueness, registration semantics, database
  schemas, and unrelated services were not changed.
- The SMTP egress guard and its narrow destination rules were not weakened.
- The development Compose stack no longer consumes generic production `SMTP_*`
  credential variables; it maps only `DEV_SMTP_*`.

## 7. Explicit Risks and Remaining Work

1. **No live post-change Mailpit acceptance.** Task 031.3.G verified the previous
   image through Mailpit. This change has unit/configuration coverage only; the
   running User and Notification containers still run the earlier image. A future
   authorized rebuild should repeat the live Mailpit workflows and inspect the new
   original-recipient headers.
2. **No live production SMTP validation.** Production delivery and suppression are
   verified with mocked SMTP transports. Provider authentication, TLS negotiation,
   bounce handling, and suppression observability in a real provider are not proven.
3. **Production transport is deployment-owned.** This repository's Compose stack is
   non-production and pins `DEV_SMTP_*`. A production deployment must supply a
   separate transport configuration. Using the development Compose file with
   `ENVIRONMENT=production` will fail validation rather than silently using Mailpit.
4. **Staging is not the production suppression class.** `staging` remains an allowed
   explicit environment for the existing staging stack, but production-domain
   suppression is applied only when `ENVIRONMENT=production`. Staging behavior is
   outside this task's acceptance criteria.
5. **Whole-message suppression.** If a multi-recipient production message contains
   any suppressed recipient, the entire message is suppressed before SMTP. Current
   application senders use one recipient per message.
6. **Mocked-transport limitation.** The SMTP tests prove control flow and error
   mapping, not network/provider behavior. They must not be presented as production
   readiness evidence.
7. **Unrelated full-suite failures.** The avatar PG and browser E2E failures remain
   unresolved and unrelated to this change; they should be triaged separately.
