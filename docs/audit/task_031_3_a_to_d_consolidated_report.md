# Task 031.3.A–D — Consolidated Email Integration Report

**Date:** 2026-10-24  
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`  
**Branch:** `master`  
**Base commit:** `b38a693a7e140584cdf7eae0e1e3f67945cdb328`  
**Scope:** Consolidated status for Tasks 031.3.A, 031.3.B, 031.3.C, and 031.3.D

## 1. Executive Status

| Task | Status | Summary |
|---|---|---|
| 031.3.A — Email sender inventory/audit | **Not evidenced** | The required `docs/audit/email_sender_inventory.md` is absent. A source scan found direct SMTP senders, but the authoritative audit checklist cannot be certified. |
| 031.3.B — Shared email adapter | **Implemented** | Shared adapter, message model, configuration module, factories, and offline adapter tests exist under `src/common/email/` and `tests/common/email/`. |
| 031.3.C — Mailpit configuration contract | **Implemented and statically verified** | Shared validation, service startup validation, Compose Mailpit wiring, examples, docs, and deterministic tests are present. |
| 031.3.D — Outbound SMTP guard | **Implemented and controlled-network verified** | Per-container netfilter guard blocks non-Mailpit SMTP ports in development/test and was tested over IPv4 and IPv6. Running application containers are not yet recreated with it. |

Task 031.3.E (migration of all senders) is **blocked** because the authoritative
Task A inventory is missing. The current direct senders remain:

- `src/services/user/otp_delivery.py`
- `src/services/notification/email_worker.py`

The shared adapter itself also uses `smtplib`, which is expected; it is the
centralized transport, not a duplicate service-level sender.

## 2. Task 031.3.A — Inventory Audit

### Finding

The prerequisite file was not found:

```text
docs/audit/email_sender_inventory.md
```

No equivalent inventory report was found elsewhere in the repository. This means
the requirement to account for every inventoried sender cannot be certified.

### Provisional source snapshot

A repository scan of current source found these direct SMTP call sites:

| Location | Responsibility | Current state |
|---|---|---|
| `src/services/user/otp_delivery.py` | Registration OTP, resend OTP, password recovery, recovery resend/verify, email-change verification delivery | Direct `smtplib` implementation remains |
| `src/services/notification/email_worker.py` | Notification outbox email dispatch | Direct `smtplib` implementation remains |
| `src/common/email/adapter.py` | Shared SMTP transport | Expected centralized `smtplib` usage |

This snapshot is **not a substitute** for the missing authoritative inventory and
does not prove that every sender has been accounted for.

## 3. Task 031.3.B — Shared Adapter

### Implemented artifacts

- `src/common/email/__init__.py`
- `src/common/email/adapter.py`
- `src/common/email/config.py`
- `src/common/email/models.py`
- `tests/common/email/test_adapter.py`

### Adapter behavior

The shared adapter provides:

- An asynchronous `EmailAdapter` protocol with `async send(message)`.
- `EmailMessage` and recipient-list models.
- `SmtpEmailAdapter` using `smtplib` in a worker thread.
- User-service and notification-service adapter factories.
- Recipient allowlist enforcement before sending.
- Explicit `EmailAdapterError` handling for allowlist rejection, SMTP rejection,
  server errors, and connection failures.
- Configuration validation during adapter initialization.

The adapter factories are:

- `get_user_email_adapter()`
- `get_notification_email_adapter()`

## 4. Task 031.3.C — Mailpit Configuration Contract

### Configuration contract

The shared validator in `src/common/email/config.py` enforces:

| Setting | Development/test | Production |
|---|---|---|
| `EMAIL_DELIVERY_BACKEND` | `smtp` or `memory` | `smtp` or `memory` |
| `SMTP_HOST` | Exactly `mailpit` for SMTP | Explicit external host; Mailpit/loopback rejected |
| `SMTP_PORT` | `1025` for SMTP | Valid explicit port |
| `SMTP_USE_TLS` / `SMTP_STARTTLS` | Both `false` for Mailpit | Not restricted by this task |
| `DEV_EMAIL_ALLOWLIST` | Non-empty for SMTP; fail-closed | Still enforced fail-closed |
| `MAILPIT_UI_PORT` | `8025` host UI/API mapping | Not a production SMTP setting |

The User and Notification settings models call the shared validator at startup.
The adapter validates again at initialization.

### Compose integration

`docker-compose.yml` provides the shared `x-email-environment` anchor to both
`user-service` and `notification-service`:

- `SMTP_HOST=mailpit`
- `SMTP_PORT=1025`
- `EMAIL_DELIVERY_BACKEND=smtp`
- Plain SMTP (`SMTP_USE_TLS=false`, `SMTP_STARTTLS=false`)
- Shared sender and allowlist settings

Mailpit remains:

- Internal SMTP listener: `1025`
- UI/API listener: `8025`
- Host mapping only for UI/API: `${MAILPIT_UI_PORT:-8025}:8025`
- SMTP port `1025` is not published to the host.

### Documentation

- `.env.example`
- `.env.staging.example`
- `docs/operations/staging_deployment_runbook.md`
- `docs/audit/task_031_3_c_email_configuration_mailpit_report.md`

## 5. Task 031.3.D — Outbound SMTP Guard

### Mechanism

A per-container netfilter guard applies only to:

- `user-service`
- `notification-service`

It is active only for `ENVIRONMENT=development` or `test`. It is skipped in
`staging` and `production`; unknown environments fail startup rather than guess a
policy.

### Policy

Allowed:

- `mailpit:1025` only as the SMTP destination.
- Loopback.
- Established/related traffic.
- DNS over UDP/TCP `53`.
- Mailpit UI/API `8025`.
- Non-SMTP ports, preserving database, Redis, health checks, and service-to-service
  connectivity.

Blocked to all other destinations:

- `25`
- `465`
- `587`
- `1025`
- `2525`

The allowed target is independent of application `SMTP_HOST` / `SMTP_PORT`, so
changing application email settings cannot widen the network allowlist.

### Capability handling

Protected containers start with `CAP_NET_ADMIN`, install the rules in their own
network namespace, then drop `NET_ADMIN` with `setpriv` before running the app.
No host-wide firewall or `DOCKER-USER` changes were made.

### Implemented artifacts

- `scripts/network/smtp_egress_guard.sh`
- `scripts/network/verify_smtp_egress_guard.sh`
- `docs/operations/smtp_egress_guard_runbook.md`
- `tests/network/test_smtp_egress_guard_config.py`
- Dockerfile packages: `iptables`, `util-linux` (`setpriv`)
- Compose entrypoint/capability settings for the two protected services

## 6. Verification Evidence

### 6.1 Latest consolidated test run

```bash
./venv/bin/pytest \
  tests/common/email tests/network tests/test_config.py \
  tests/test_email_delivery.py tests/test_email_dispatch_worker.py \
  tests/test_staging_operational_readiness.py -q
```

Result: **69 passed**.

### 6.2 Lint and type checks

```bash
./venv/bin/ruff check \
  src/common/email tests/common/email tests/network \
  src/services/user/config.py src/services/notification/config.py
```

Result: `All checks passed!`

```bash
./venv/bin/pyright \
  src/common/email src/services/user/config.py src/services/notification/config.py
```

Result: `0 errors, 0 warnings, 0 informations`.

### 6.3 Compose and whitespace validation

```bash
INTERNAL_SERVICE_SECRET=<validation placeholder> docker compose config --quiet
git diff --check
```

Result: exit 0; whitespace check clean.

### 6.4 Controlled network guard tests

IPv4 and IPv6 controlled-listener tests confirmed:

- Mailpit SMTP `mailpit:1025` reachable.
- Mailpit UI/API `mailpit:8025` reachable.
- DNS resolution works.
- Non-Mailpit SMTP ports `25`, `465`, `587`, `1025`, `2525` blocked.
- Non-SMTP control port `8080` remains reachable.
- Mailpit port `25` blocked.

No email was sent. The IPv6 test used a temporary Docker IPv6 network. The IPv4
test is reproducible with:

```bash
./scripts/network/verify_smtp_egress_guard.sh
```

## 7. Current Runtime State

- No application containers were restarted or recreated.
- Existing application containers remained running and healthy.
- `forumplatform-user-service:latest` was rebuilt for guard verification.
- `forumplatform-notification-service` was not rebuilt.
- The running `forum_user_service` and `forum_notification_service` containers
  still use their previous images/entrypoints.
- Therefore, the D guard is verified in controlled containers but is not yet
  active in the running application containers.
- No live email delivery through rebuilt application containers was verified.
- No host firewall or host-wide network policy was changed.

## 8. Changed and Preserved Work

Tracked changes from these tasks:

- `.env.example`
- `.env.staging.example`
- `Dockerfile`
- `docker-compose.yml`
- `docs/operations/staging_deployment_runbook.md`
- `src/services/notification/config.py`
- `src/services/user/config.py`

New untracked task artifacts include:

- `src/common/email/`
- `tests/common/email/`
- `tests/network/`
- `scripts/network/`
- `docs/operations/smtp_egress_guard_runbook.md`
- `docs/audit/task_031_3_c_email_configuration_mailpit_report.md`
- `docs/audit/task_031_3_d_smtp_sink_outbound_guard_report.md`
- this consolidated report

Unrelated pre-existing untracked work was preserved.

## 9. Remaining Work and Risks

1. **Task A:** provide or reconstruct `docs/audit/email_sender_inventory.md`.
2. **Task E:** migrate the inventoried direct senders to the shared adapter,
   preserving outbox/worker semantics, retries, idempotency, allowlists, and
   delivery-state behavior.
3. Rebuild and recreate both protected services under explicit authorization.
4. Verify runtime delivery to Mailpit only, then confirm no external SMTP path is
   usable from the recreated containers.
5. Verify production SMTP separately with explicit external configuration.
6. Refresh the guard if Mailpit's resolved IP changes and the protected containers
   are not recreated.

## 10. Safety Statement

No Git reset, clean, commit, push, or branch change was performed. No real email
was sent. No host firewall changes were made. Pre-existing worktree changes and
unrelated untracked files were preserved.
