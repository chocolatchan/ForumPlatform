# Task 031.3.C — Email Configuration Standardization and Mailpit Integration

**Date:** 2026-10-24  
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`  
**Branch:** `master`  
**Base commit:** `b38a693a7e140584cdf7eae0e1e3f67945cdb328`  
**Scope:** Shared email configuration contract, Mailpit Compose wiring, deterministic tests, and documentation.

## 1. Summary

Task 031.3.C standardizes development/test email configuration around one shared
contract in `src/common/email/config.py`. Both the User and Notification service
settings validate that contract at startup, and the shared SMTP adapter validates
it again during initialization.

Development and test SMTP delivery now resolves to the existing Mailpit service:

- Host: `mailpit` (Compose service DNS name)
- Internal SMTP port: `1025`
- Transport: plain SMTP, with TLS/STARTTLS disabled
- Mailpit UI/API host mapping: `8025`
- Recipient allowlist: non-empty for SMTP delivery and enforced fail-closed

Production is kept separate: Mailpit, `localhost`, and loopback SMTP hosts are
rejected when `ENVIRONMENT=production`. There is no fallback to a public SMTP
endpoint when Mailpit is unavailable.

## 2. Configuration Contract

The shared validator rejects invalid configurations without opening a network
connection or sending mail.

| Setting | Development/test contract | Production behavior |
|---|---|---|
| `EMAIL_DELIVERY_BACKEND` | `smtp` or `memory` | `smtp` or `memory` |
| `SMTP_HOST` | Must be exactly `mailpit` for SMTP; localhost/loopback rejected | Must be an explicitly configured external host; `mailpit`/loopback rejected |
| `SMTP_PORT` | Must be `1025` for SMTP | Must be a valid port |
| `SMTP_USE_TLS` / `SMTP_STARTTLS` | Both must be `false` | Not restricted by this task |
| `DEV_EMAIL_ALLOWLIST` | Must be non-empty for SMTP delivery | Still enforced fail-closed |
| `MAILPIT_UI_PORT` | `8025` host-side UI/API mapping | Not a production SMTP setting |

`memory` backend is reserved for deterministic tests. It does not require an SMTP
endpoint; delivery code still checks the allowlist before writing to the in-memory
sink, preserving fail-closed behavior.

## 3. Changes Made

### 3.1 Shared contract

Added `src/common/email/config.py`:

- Canonical Mailpit constants: `mailpit`, `1025`, and UI port `8025`.
- `EmailConfigurationError`.
- `validate_email_configuration(...)`, which validates backend, host, port,
  transport flags, environment-specific rules, and allowlist presence for SMTP.
- No socket connections or message delivery.

### 3.2 Shared adapter

Updated `src/common/email/adapter.py`:

- `_SmtpConfig` now carries `environment` and `backend`.
- Both factory methods read the same fields from the User/Notification settings.
- `SmtpEmailAdapter.__init__` validates the shared contract and wraps
  `EmailConfigurationError` as `EmailAdapterError`, failing closed at
  initialization.
- Recipient lists are normalized to a concrete list before allowlist checks and
  sending.

### 3.3 Service startup validation

Updated:

- `src/services/user/config.py`
- `src/services/notification/config.py`

Both settings models now call `validate_email_configuration` in an
`after` model validator. This makes missing or unsafe Mailpit configuration fail
at service startup rather than at first delivery attempt.

The obsolete no-op Mailpit warning block in the User production validator was
replaced by the real fail-closed validation.

### 3.4 Compose consolidation

Updated `docker-compose.yml`:

- Added the `x-email-environment` YAML anchor containing the shared SMTP settings.
- Merged that anchor into both `user-service` and `notification-service`.
- Removed duplicated SMTP environment blocks.
- Preserved the Mailpit service:
  - `MP_SMTP_BIND_ADDR: 0.0.0.0:1025`
  - `MP_UI_BIND_ADDR: 0.0.0.0:8025`
  - Host mapping only for the UI/API: `${MAILPIT_UI_PORT:-8025}:8025`
- Preserved healthy `mailpit` dependencies for both application services.
- SMTP remains internal-only; port `1025` is not published to the host.

### 3.5 Documentation

Updated:

- `.env.example`
- `.env.staging.example`
- `docs/operations/staging_deployment_runbook.md`

The examples now document the internal SMTP endpoint versus the host-published UI
port, explicitly set plain-SMTP flags, and state that production requires a
separate explicit SMTP configuration. No real secrets were added.

### 3.6 Tests

Added:

- `tests/common/email/test_email_config_contract.py`
- `tests/common/email/test_mailpit_compose.py`

Updated:

- `tests/common/email/test_adapter.py` to use deterministic `mailpit` settings.

Coverage includes:

- Valid Mailpit configuration.
- Rejection of `localhost`, `127.0.0.1`, and `::1`.
- Rejection of public SMTP hosts in development/test.
- Rejection of the wrong Mailpit SMTP port.
- Rejection of TLS/STARTTLS for Mailpit.
- Empty allowlist rejection for SMTP.
- Production rejection of Mailpit/loopback hosts.
- Service startup rejection of unsafe SMTP configuration.
- Compose checks for shared environment values, healthy Mailpit dependency, UI
  port `8025`, and absence of a host-published SMTP port.

All tests are offline and mock SMTP where necessary.

## 4. Verification Evidence

| Check | Command | Result |
|---|---|---|
| Focused email/broader tests | `./venv/bin/pytest tests/common/email tests/test_config.py tests/test_email_delivery.py tests/test_email_dispatch_worker.py tests/test_staging_operational_readiness.py -q` | `63 passed` |
| Ruff | `./venv/bin/ruff check src/common/email tests/common/email src/services/user/config.py src/services/notification/config.py` | `All checks passed!` |
| Pyright | `./venv/bin/pyright src/common/email src/services/user/config.py src/services/notification/config.py` | `0 errors, 0 warnings, 0 informations` |
| Compose validation | `INTERNAL_SERVICE_SECRET=<validation placeholder> docker compose config --quiet` | Exit 0, no output |
| Whitespace check | `git diff --check` | Clean |

The Compose validation used a placeholder value only to satisfy the pre-existing
required interpolation variable; no real secret was written to disk or included
here.

## 5. Runtime Status

Containers were **not rebuilt or restarted** during this task. No live SMTP
connection was made and no message was sent to Mailpit or any external provider.

Therefore:

- Static configuration validation and offline tests pass.
- Runtime email delivery through the rebuilt application containers has **not**
  been verified.
- Mailpit UI/API was not queried for a delivered message.

The existing application containers still run their previous images. Source
changes take effect only after an approved rebuild/recreate.

## 6. Remaining Runtime Integration Steps

1. Obtain explicit authorization to rebuild/recreate the affected containers.
2. Rebuild and recreate the `user-service` and `notification-service` containers.
3. Confirm `forum_mailpit` is healthy and the UI/API remains reachable on the
   configured host port (`8025` by default).
4. Trigger one allowlisted development/test email through the User or Notification
   flow.
5. Verify the message in Mailpit's UI/API and confirm the SMTP endpoint used was
   `mailpit:1025`, not a host loopback or external endpoint.
6. Verify production configuration separately with an explicitly supplied
   external SMTP host; do not reuse Mailpit settings in production.

## 7. Preservation and Safety Notes

- No Git reset, commit, push, branch change, file deletion, or container restart
  was performed.
- Pre-existing untracked work, including unrelated audit reports and Compose test
  files, was preserved.
- The shared adapter and its tests were already present as Task 031.3.B work and
  were updated only where required for the shared configuration contract.
- No SMTP credentials or sensitive values were logged or added to examples.
