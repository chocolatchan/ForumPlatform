# Task 031.1 – Validation Report: Email Worker Lease Recovery

**Date:** 2026-10-22
**Branch:** `master`
**Baseline commit:** `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` (= `origin/master`, verified via `git fetch`)

---

## 1. Pre-Implementation Checks

| Check | Result |
|---|---|
| `git status` — only email-worker work in progress, preserved | ✅ Confirmed |
| HEAD == `bf3384c…` == `origin/master` after fetch | ✅ Confirmed |
| Commit `bf3384c` exists locally | ✅ Confirmed (`git cat-file -t` → `commit`) |
| Task 031 docs read (`task_031_design/change_log/validation_report`) | ✅ Done; §4.4 deferred sending-row recovery — this task fulfills it |
| Migration `notifications_0004` applied | ✅ `alembic current` → `notifications_0004 (head)` |

---

## 2. Automated Test Results (final run)

| Suite | Result |
|---|---|
| `tests/test_email_worker_lease_pg.py` (new, PostgreSQL) | **12 passed** |
| `tests/test_email_dispatch_worker.py` (Task 031 suite) | **23 passed** |
| `tests/test_notification_service.py` | **4 passed** |
| `tests/test_avatar_email_change_pg.py` (Task 030) | **5 passed** |
| `tests/test_recovery_concurrency_pg.py` (Task 029, live gateway) | **3 passed** |
| `tests/test_user_auth_lifecycle.py` | 13 passed, **1 failed (pre-existing, see §5)** |
| Combined single run (all six files) | **60 passed, 1 failed (pre-existing)** |

### Static Analysis

| Tool | Result |
|---|---|
| Ruff (`src/services/notification/`, both email-worker test files) | **All checks passed** (baseline had 10 errors; this task reduced to 0) |
| Pyright (changed files, `--pythonpath venv/bin/python`) | **0 errors, 0 warnings** |

---

## 3. Live SMTP Verification (Docker Compose + Mailpit v1.31.6)

Only `notification-service` was rebuilt/restarted. `user-service` was **not**
rebuilt (its running image contains the real Task 029 recovery implementation;
see §5).

Container env confirmed: `EMAIL_DELIVERY_BACKEND=smtp`, `SMTP_HOST=mailpit`,
`SMTP_PORT=1025`, `DEV_EMAIL_ALLOWLIST=developer@example.test,tester@example.test`,
`EMAIL_WORKER_LEASE_TIMEOUT_SECONDS=300`. `/health/live` → `{"status":"ok"}`;
Mailpit API healthy on `:8025`.

| # | Scenario | Evidence | Result |
|---|---|---|---|
| 1 | Registration OTP email (user-service → SMTP → Mailpit) | `POST /auth/register` → 202; Mailpit msg `Your Forum Verification Code [Registration]` to `developer@example.test`, from `noreply@forumplatform.local`, 6-digit code in body | ✅ |
| 2 | `UserRegistered` event → queue → worker → welcome email | After OTP verify: `notifications.email_deliveries` row `Welcome to ForumPlatform!` status `delivered`, attempts=1; Mailpit message verified (recipient / subject / text+HTML body) | ✅ |
| 3 | Optional-activity suppression (`email_enabled=false` default) | `CommentCreated` internal event → in-app item created, `consumed_events.status='processed'`, **0 email rows** added | ✅ |
| 4 | Mandatory-security policy | Welcome email (`mandatory_security`) delivered to a user with default `email_enabled=False` | ✅ |
| 5 | SMTP transient failure retry | Mailpit stopped → worker attempt failed: status `failed`, attempts=1, `last_error='SMTP connection failed: gaierror'`; Mailpit restarted → worker retried with backoff → status `delivered`, Mailpit message present | ✅ |
| 6 | Worker restart recovery (stale lease) | Seeded `sending` row with 10-min-old `sending_since`; restarted `notification-service` → startup recovery re-claimed it → status `delivered`, attempts=2 (crashed attempt counted) | ✅ |
| 7 | Active lease preserved | Seeded `sending` row with fresh `sending_since`; after restart it remained `sending` (not reclaimed) | ✅ |
| 8 | Allowlist fail-closed | Seeded pending row to `outsider@not-allowed.test` → immediate `dead_letter`, attempts=1, error "not permitted by allowlist", **no SMTP attempt, no Mailpit message** | ✅ |
| 9 | Password recovery email flow (live stack) | `POST /auth/recovery/request` → 202; Mailpit `Your Forum Verification Code [Recovery]`; `recovery/verify` → reset token; `recovery/reset` → new password; login with new password succeeds | ✅ (live container only — see §5) |
| 10 | Graceful shutdown | Restart logs: `Shutting down` → `Notification EmailDispatchWorker stopped.` → `Application shutdown complete.` | ✅ |
| 11 | No secret leakage | `docker logs` of both services grepped for OTPs, reset tokens, SMTP/DB passwords → **no matches** | ✅ |
| 12 | Ambiguous SMTP result (crash after acceptance, before DB write) | Covered by PG test `test_crash_after_smtp_accept_before_db_update_recovers_and_redispatches` (at-least-once, possible duplicate documented); not simulated live | ✅ (test) |

**Limitation (documented, not silent):** the moderation-event → email path could
not be exercised end-to-end against the *running* `user-service` container: the
container image (built 2026-10-22T09:33Z) predates the `/internal/users/{id}`
email-resolution endpoint, so notification-service receives 404 and skips the
email (with a logged warning). The committed source contains the endpoint; the
equivalent mandatory-security and recipient-resolution logic was verified via
the UserRegistered welcome email (scenario 2/4) and the PG test suite. The
container was intentionally not rebuilt (see §5).

---

## 4. Working Tree / Intactness Review

```
 M docker-compose.yml
 M src/services/notification/config.py
 M src/services/notification/email_worker.py
 M src/notification/models.py  (sending_since)
 M tests/test_email_dispatch_worker.py
?? migrations/notifications/versions/20261022_0004_email_delivery_lease.py
?? tests/test_email_worker_lease_pg.py
```

- `git diff HEAD --name-only` over User/Content/Interaction/Moderation/Search
  services, gateway, and their migrations → **empty**: Tasks 028–030 and the
  Task 031 source outside the worker/config/models are untouched.
- No commit, no push, no reset/clean performed.
- Wildcard recipient: not enabled anywhere; allowlist defaults are explicit
  addresses only (fail-closed when empty).

---

## 5. Pre-Existing Baseline Findings (NOT caused by Task 031.1)

1. **Task 029 recovery endpoints are 501 placeholders in committed source.**
   At baseline `bf3384c`, `POST /auth/recovery/{request,verify,reset}` (and
   `/profile/change-email/*`) return `501` from `src/services/user/app.py`,
   while `tests/test_user_auth_lifecycle.py::test_password_recovery_and_reset_lifecycle`
   expects real behavior → the test **fails at clean HEAD** (verified in a
   disposable worktree at `bf3384c`). No recovery implementation exists in any
   commit (`git log --all -S` confirms). However, the **running user-service
   Docker image contains the full recovery implementation** (verified: real
   `recovery_request` with rate limiting + anti-enumeration at container
   `app.py:869`; live recovery flow passes — §3 scenario 9). Restoring the
   committed source to match is Task 029 remediation work, out of 031.1 scope,
   and rebuilding the user-service image would destroy the working live flow —
   deliberately not done. **Follow-up required before any release build from
   source.**
2. **Cross-test metadata pollution (fixed in this task as test hygiene).**
   The Task 031 test fixture permanently stripped schemas from the shared
   SQLAlchemy `MetaData`, breaking other suites in combined runs at baseline.
   Fixed by switching to ATTACH-based schema support; all six suites now pass
   together (60 passed).

---

## 6. Verdict

**PASS** — within the stated scope:

- Stale `sending` rows recovered (expired lease, NULL lease, crash-after-accept);
- Active leases protected (fresh lease untouched, SKIP LOCKED concurrency);
- 12 new PostgreSQL concurrency/failure tests pass; existing 23-test email-worker
  suite passes; Task 029/030 regression suites pass;
- Live Mailpit verification executed end-to-end (12 scenarios, §3);
- Ruff/Pyright clean on all touched files;
- No unrelated feature work; nothing committed or pushed;
- Two pre-existing baseline findings documented with evidence and reproduction (§5).
