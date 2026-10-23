# Task 031.2.4 — Read-Only Integration Preflight Report

**Date:** 2026-10-23
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`
**Branch:** `master`
**HEAD:** `bf3384c4c3b53b5f826ef119f2ad549997ff1d63`
**Mode:** read-only preflight; no rebuild, restart, migration, test execution, or database write.

---

## 1. Executive verdict

**READY WITH EXPLICIT RISKS**

The accumulated Task 031.1 and Task 031.2.3a–f working-tree changes are source-level coherent enough to begin a controlled validation wave, but they are **not ready for an uncontrolled rebuild or deployment**. The main risks are:

1. The running User Service image is stale relative to the restored source. Live behavior currently tests the wrong code.
2. The live `users` database already contains duplicate unconsumed password-recovery tokens for one account, while the restored handlers use `scalar_one_or_none()` without a limit. That account can currently receive a 500 from recovery request/verify.
3. The new notification migration and its PostgreSQL test are untracked. A local Docker build sees them, but a clean checkout or CI build does not.
4. All PostgreSQL concurrency, Mailpit, worker-crash, and browser validations remain deferred. The lock-order corrections are statically reviewed only.
5. Model/migration drift and cross-service configuration drift exist and must be handled explicitly during validation.

This is not **BLOCKED** because the source can be rebuilt and validated with the prerequisites in §9. It is not plain **READY FOR CONTROLLED VALIDATION** because the live data and clean-build prerequisites must be addressed first.

---

## 2. Working-tree and commit snapshot

### 2.1 Git state at preflight

```text
branch: master
HEAD:   bf3384c4c3b53b5f826ef119f2ad549997ff1d63
date:   2026-10-22T22:41:09+07:00
staged: none
```

`git status --short`:

```text
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

No staged changes. This report is the only file added by this task.

### 2.2 Change ownership by task

| Change group | Files | Evidence |
|---|---|---|
| Task 031.1 email lease | `.env.example`, `docker-compose.yml`, `src/services/notification/{config,email_worker,models}.py`, `tests/test_email_dispatch_worker.py`, new `migrations/notifications/versions/20261022_0004_email_delivery_lease.py`, new `tests/test_email_worker_lease_pg.py` | Diffs and prior audit reports; source inspection |
| Task 031.2.3c recovery | `src/services/user/app.py` recovery handlers, limiter resets | `app.py:882–1254`; prior 031.2.3c report |
| Task 031.2.3d email change | `src/services/user/app.py` email-change handlers/limiters | `app.py:1258–1680`; prior 031.2.3d report |
| Task 031.2.3e avatar | `src/services/user/app.py` avatar/profile responses | `app.py:683–878`, `1749–1940`; prior 031.2.3e report |
| Task 031.2.3f required password | `src/services/user/schemas.py`, `tests/test_user_auth_lifecycle.py` | `schemas.py:104–106`; new test at `tests/test_user_auth_lifecycle.py:968` |

The `users_0003` migration is tracked and not part of the uncommitted set; it is present at HEAD.

---

## 3. Method and evidence boundaries

Performed read-only checks only:

- `git status`, `git diff`, `git diff --check`, `git log`, `git ls-files`, `git check-ignore`.
- Source, model, migration, schema, OpenAPI, Compose, Dockerfile, and test inspection.
- Read-only `docker ps`, `docker inspect`, and `docker exec … grep/printenv/python -c` against running containers.
- Read-only HTTP `GET` requests to live health, OpenAPI, and route probes.
- Read-only PostgreSQL `SELECT` queries for schema versions, columns, indexes, constraints, and data conditions.

Not performed:

- No container rebuild, recreate, restart, stop, or removal.
- No image load, retag, or replacement.
- No Alembic upgrade/downgrade or migration write.
- No database writes, including cleanup of the duplicate tokens described below.
- No pytest, browser E2E, Mailpit send, or live-stack mutation.
- No source, test, migration, Compose, `.env`, or documentation edits except this report.

Static source verification is distinguished below from live/runtime proof.

---

## 4. Findings by severity

### 4.1 Critical

**None found.** No source-level conflict or migration-chain break makes a controlled rebuild impossible.

### 4.2 High

#### H-1 — The running User Service is stale and currently validates the wrong code

Evidence:

- Running container: `forum_user_service`, image `forumplatform-user-service`.
- User image built: `2026-10-22T09:33:43Z`; container started `2026-10-22T09:33:45Z`.
- Current `src/services/user/app.py` mtime: `2026-10-23 02:43:13 +07:00` — about 10 hours newer.
- Running container source is 1,848 lines; working source is 1,941 lines.
- Running container source still sends the wrong avatar header: `X-Internal-Key` at `src/services/user/app.py:752,854`.
- Running container schema still has optional password: `schemas.py:105` → `password: str | None = None`.
- Live `GET http://localhost:8001/openapi.json` reports:
  - `/internal/users/{user_id}` absent;
  - `/profile/avatar` present;
  - `/profile/change-email/resend-otp` present;
  - `ChangeEmailRequest.required == ["new_email"]` (password not required).
- Live `GET http://localhost:8001/internal/users/…` returns `404 {"detail":"Not Found"}`.

Consequence: live testing today cannot validate Task 031.2.3d–f or the notification-service recipient lookup. The live avatar path also relies on the Content Service development bypass because the header is wrong. Do not treat any live result from this container as proof of the restored source.

#### H-2 — Live data violates the single-active-recovery-token invariant, and current queries can 500

Evidence:

- Live read-only query found account `2735494c-9a8d-4bd2-9461-d393351daccc` with **two** unconsumed `password_recovery` rows:
  - `c2bc32ec-8b04-493c-9824-1a304172cfcf`
  - `40e10b3d-6ad8-4954-8ad9-afce9d4c63d1`
- Both were created at `2026-10-22 09:16:19Z` (milliseconds apart), both expired at `2026-10-22 09:31:19Z`, both `consumed_at IS NULL`.
- There are 3 total unconsumed `password_recovery` rows in the live database, all expired.
- `recovery_request` selects the active token with `.scalar_one_or_none()` at `src/services/user/app.py:918–928`.
- `recovery_verify` does the same at `src/services/user/app.py:1032–1042`.
- Neither query has `.limit(1)` or an ordering tie-break that makes duplicates harmless.
- `users.one_time_tokens` has only a primary-key index; there is no partial unique index preventing multiple unconsumed recovery tokens per account.
- The live database has no duplicate active `email_change_requests` today, but `change_email_request`, `change_email_resend_otp`, and `change_email_verify` use the same single-result pattern at `app.py:1345–1354`, `1460–1469`, and `1562–1571`.

Consequence: for the affected account, a new recovery request or verification can raise `MultipleResultsFound` and return 500 before expiry/cooldown logic runs. This is a live-data prerequisite, not something a rebuild alone fixes. It must be quarantined or corrected in the controlled validation task before recovery tests are considered meaningful.

#### H-3 — The new notification migration and lease test are untracked

Evidence:

- `git ls-files migrations/notifications/versions/20261022_0004_email_delivery_lease.py tests/test_email_worker_lease_pg.py` returns nothing.
- Both files are present in the working tree and are copied into a local Docker image by `Dockerfile` (`COPY migrations/ /app/migrations/`).
- A clean checkout, CI job, or build from git will omit the migration and the test.
- The running database already has `sending_since`, but a fresh environment built without the untracked migration will not.

Consequence: local controlled rebuild can work, but release/CI reproducibility is not guaranteed. Commit or otherwise include the new migration before any clean build. Do not stage or commit in this task.

#### H-4 — Concurrency, worker-crash, Mailpit, and browser behavior remain unproven

Evidence:

- The lock-order corrections are reviewed statically:
  - recovery: Account → token (`app.py:1143–1179`);
  - email change: Account → EmailChangeRequest (`app.py:1330–1394`, `1449–1512`, `1554–1571`);
  - duplicate account read in email verify is intentionally unlocked (`app.py:1620–1628`).
- Existing PostgreSQL tests target the live gateway/user-service and write to live databases:
  - `tests/test_recovery_concurrency_pg.py`;
  - `tests/test_avatar_email_change_pg.py`;
  - `tests/test_email_worker_lease_pg.py`.
- Existing browser tests target Mailpit and the live stack:
  - `tests/test_browser_e2e.py:1012` recovery journey;
  - `tests/test_browser_e2e.py:1124` avatar/email-change journey.
- No tests were run in this preflight.

Consequence: deadlock avoidance, `SKIP LOCKED` behavior under competing workers, crash recovery, attachment lifecycle, and browser flows are **not runtime-proven**. They must be run after the stale User Service is replaced.

### 4.3 Medium

#### M-1 — Model/migration drift in the User Service

Evidence:

- `EmailChangeRequest` declares `ck_email_change_attempts` and `ck_email_change_resend_count` at `src/services/user/models.py:171–179`.
- The initial migration creates `email_change_requests` with `attempts` but no check constraints (`migrations/users/versions/20261020_0001_initial_users.py:207–232`).
- `users_0003` docstring says “attempt constraints,” but its upgrade only adds `resend_count`, `resend_available_at`, and two indexes (`migrations/users/versions/20261022_0003_email_change_resend_columns.py:1,19–46`).
- Live `users.email_change_requests` has only `pk_email_change_requests` and the account foreign key; no attempts/resend check constraints.
- `OneTimeToken` model declares only `ck_one_time_attempts` (`models.py:102–109`), while the initial migration creates both `ck_one_time_attempts` and `ck_one_time_resends` (`initial_users.py:150–151`); the live database has both.

Consequence: the database does not enforce the model’s declared bounds for email-change attempts/resend counts, and future autogenerate/`alembic check` work will produce drift noise. Application checks currently cover the normal paths.

#### M-2 — Migrations are not run automatically by Compose

Evidence:

- Compose service commands start Uvicorn directly (`docker-compose.yml:202` for notification, `:63` for user); no Alembic invocation.
- `scripts/migrate.py` is the explicit migration CLI; README documents manual migration.
- The notification worker’s `process_batch` and recovery queries select `sending_since` (`email_worker.py:315–338`, `364–381`), so the column must exist before the worker starts.

Consequence: current live databases are already at `users_0003` and `notifications_0004`, so a retained-volume rebuild needs no new migration. A fresh/reset environment must apply migrations explicitly before starting services.

#### M-3 — Avatar claim/update/release is not atomic across User and Content Services

Evidence:

- User Service claims remote attachment first (`app.py:754–761`).
- User Service then updates `Account.avatar_object_key` in a separate transaction (`app.py:801–811`).
- Old-avatar release is best-effort after commit (`app.py:813–828`).
- Delete clears the account reference first and releases best-effort afterwards (`app.py:852–876`).
- Content Service claim commits attachment status independently (`content/app.py:1548–1580`).
- Content Service release marks deleted and removes the stored file after commit (`content/app.py:1602–1625`).

Consequence: a crash or DB failure after a successful claim can orphan an attachment in `avatar` state; a failed old-avatar release can leave an orphan. There is no reconciliation job or compensating transaction. This is documented in source comments but remains a real consistency gap.

#### M-4 — Email-change resend reports success after delivery failure

Evidence:

- Resend overwrites `otp_hash`, extends expiry, increments `resend_count`, and sets cooldown at `app.py:1507–1512`.
- Delivery is attempted afterwards; `EmailDeliveryError` is only logged (`app.py:1514–1524`).
- The endpoint still returns `200 {"status":"sent"}` (`app.py:1526–1529`).

Consequence: the old OTP is already invalidated and the new code was never delivered. Repeated SMTP failures also consume the resend quota. This is inherited behavior, but it is a user-visible availability risk and should be covered by a Mailpit failure test.

#### M-5 — Internal-service key environment name is inconsistent with configuration

Evidence:

- Compose passes `INTERNAL_SERVICE_KEY` to services (`docker-compose.yml:85,118,213,254`).
- The Pydantic field is `internal_service_secret` (`src/common/config.py:30–33`), whose default environment name is `INTERNAL_SERVICE_SECRET`.
- `.env.example` documents `INTERNAL_SERVICE_SECRET` (`:65`), not `INTERNAL_SERVICE_KEY`.
- Live container inspection: `INTERNAL_SERVICE_KEY` is set, `INTERNAL_SERVICE_SECRET` is absent, `ENVIRONMENT` is unset (default `development`); both User and Content settings report `development` and the 43-character default secret.
- Both User and Content services fall back to the same Pydantic default, so avatar calls work locally by accident.
- Both `verify_internal_service_key` implementations allow a missing key in `development`/`test` (`src/services/user/auth.py:138–157`, `src/services/content/auth.py:149–167`).

Consequence: the configured Compose key is ignored. Local tests cannot prove header enforcement because the development bypass accepts a missing header. In staging/production, default-secret validation would fail unless the correct `INTERNAL_SERVICE_SECRET` is wired through.

#### M-6 — OpenAPI omits several status codes that the handlers can return

Evidence:

- Change-email request handler returns `400 INVALID_PASSWORD` / `400 EMAIL_UNCHANGED` and `429` (`app.py:1272–1310`); canonical OpenAPI lists only 202/401/422 (`forum_public_api.openapi.yaml:406–438`).
- Change-email verify returns `409 EMAIL_ALREADY_IN_USE` and `429` (`app.py:1546–1659`); OpenAPI lists only 200/400/401 (`:468–498`).
- Avatar set returns `409 INVALID_ATTACHMENT_STATUS` and `502 SERVICE_UNAVAILABLE` (`app.py:762–799`); OpenAPI lists 200/400/401/403/404 (`:356–390`).

Consequence: generated clients and contract tests will not know about these documented-in-code error paths. The success schemas and required password are aligned; the gap is error coverage.

### 4.4 Low

- **L-1 — Recovery request email format is not validated as the canonical spec declares.** Canonical OpenAPI declares `email: format: email` and a 422 response (`forum_public_api.openapi.yaml:205–231`), but `RecoveryRequest.email` is a plain `str` (`schemas.py:85–86`) and the handler only lowercases/strips (`app.py:897`). A malformed address therefore receives neutral 202. Anti-enumeration may justify this, but it is not documented as a deviation.
- **L-2 — Recovery’s fifth wrong OTP returns `INVALID_OTP`, not `OTP_ATTEMPTS_EXCEEDED`.** `app.py:1073–1083` returns `INVALID_OTP` on every wrong code, including the fifth. Email-change verify returns `OTP_ATTEMPTS_EXCEEDED` on the fifth (`app.py:1602–1612`). The status is 400 either way; the code differs.
- **L-3 — Active-token queries have no deterministic limit.** This is the code-level cause of H-2 for legacy duplicates. Email-change variants have the same shape but no live duplicates today.
- **L-4 — Lease recovery overwrites the previous SMTP error with a generic message.** `email_worker.py:331–333` sets `last_error = "Worker lease expired…"`. Useful for lease diagnosis, but it discards the original delivery error.
- **L-5 — Default recipient allowlist is intentionally narrow.** Compose defaults to `developer@example.test,tester@example.test` (`docker-compose.yml:220`) and Mailpit enforces a matching regex (`:46`). This is correct for local validation, but production recipients will be rejected unless the allowlist is replaced/fail-closed policy is revisited.
- **L-6 — No direct test for `GET /internal/users/{user_id}`.** The route exists and is protected (`app.py:1909–1940`), and notification calls it (`events.py:199–216`), but grep found no test that exercises that route or its key enforcement. Current live 404 evidence is a runtime divergence, not a test.

---

## 5. Scope review

### 5.1 Recovery and email-change integration

**Route registration and methods**

| Endpoint | Method | Handler | Auth/CSRF |
|---|---|---|---|
| `/auth/recovery/request` | POST | `app.py:882–1000` | public |
| `/auth/recovery/verify` | POST | `app.py:1003–1124` | public |
| `/auth/recovery/reset` | POST | `app.py:1127–1254` | reset token |
| `/profile/change-email/request` | POST | `app.py:1258–1422` | session + CSRF |
| `/profile/change-email/resend-otp` | POST | `app.py:1425–1529` | session + CSRF |
| `/profile/change-email/verify` | POST | `app.py:1532–1680` | session + CSRF |

**Schemas and behavior**

- Recovery request is a plain email string; verify requires 6 digits; reset requires token + new password min length 8 (`schemas.py:85–96`).
- Change-email request now requires both `new_email` and `password` (`schemas.py:104–106`).
- Change-email resend has no body; verify requires 6-digit OTP (`schemas.py:109–127`).
- Password verification happens in the handler after schema validation (`app.py:1281–1289`); missing/null password is rejected by Pydantic before the handler.
- Recovery OTPs are 15 minutes; reset tickets are 10 minutes; email-change OTPs are 10 minutes (`app.py:945, 1102, 1378, 1507`).
- Rate limiters are per-client-IP and use separate keys, except email-change resend reuses `rate_limiter_resend_otp` (`app.py:1438`).

**Lock order and concurrency**

- Recovery request/verify: Account → OneTimeToken (`app.py:906–928`, `1019–1042`).
- Recovery reset: unlocked ticket-owner pre-read, then Account → ticket under lock (`app.py:1143–1179`).
- Email-change request/resend/verify: Account → EmailChangeRequest (`app.py:1332–1354`, `1449–1469`, `1554–1571`).
- The duplicate-email read in verify is deliberately unlocked to avoid A→B/B→A cross-account cycles (`app.py:1620–1628`); the `IntegrityError` path maps the unique-constraint conflict to 409 (`app.py:1646–1659`).

This is consistent on inspection. It is not proven under PostgreSQL concurrency; H-2 is a live counterexample to the assumption that only one active recovery token exists.

**Password history and session revocation**

- Reset rejects the current password and the last five history hashes (`app.py:1199–1224`), appends the old hash (`:1229–1236`), updates the password (`:1238–1241`), and revokes all active sessions with reason `password_reset` (`:1243–1251`).
- The lifecycle test covers current-password reuse, one-time ticket use, old-password rejection, new-password login, and session invalidation (`tests/test_user_auth_lifecycle.py:925–964`).
- History is not pruned; this is inherited behavior, not a correctness break for the documented five-generation check.

**Delivery failure and challenge lifecycle**

- Recovery request and email-change request invalidate their new challenge on `EmailDeliveryError` (`app.py:973–991`, `1396–1414`).
- Email-change resend does not invalidate; see M-4.
- Expiry and attempt limits are checked before OTP verification; expired/attempt-exhausted challenges are marked completed/consumed.
- Recovery verify on the fifth wrong attempt consumes the challenge but returns `INVALID_OTP` (L-2).

### 5.2 Avatar and cross-service integration

**Flow**

- `PUT /profile/avatar` (`app.py:743–843`) sends `X-Internal-Service-Key` and `{"user_id": ...}` to `POST /internal/attachments/{image_id}/claim-avatar` (`:750–761`).
- Content Service verifies the internal key (`content/auth.py:149–167`), locks the attachment, checks ownership and status, and sets status `avatar` (`content/app.py:1548–1580`).
- User Service then locks the account, updates `avatar_object_key`, and records the old avatar for best-effort release (`app.py:801–811`).
- `DELETE /profile/avatar` clears the account reference under lock (`app.py:852–858`) and best-effort calls `release-avatar` (`:860–876`), always returning 204.
- Content Service release marks the attachment `deleted` and removes the file after commit (`content/app.py:1602–1625`).
- Content Service serves `avatar` attachments publicly without a session (`content/app.py:1437–1449`, `1494–1499`).
- `avatar_url` is consistently `/uploads/images/{attachment_id}` in own profile (`app.py:688–704`), profile update (`:723–739`), avatar set (`:830–842`), public profile (`:1770–1774`), and email-change response (`:1663–1678`).

**Findings**

- Header name now matches Content Service; the running User container still has the old `X-Internal-Key` (H-1).
- Same-avatar replacement is idempotent at the Content layer because status `avatar` is claimable (`content/app.py:1571–1580`); User Service does not release the same ID (`app.py:808–809`).
- Missing object → 404; unowned → 403; invalid status → 409; unexpected upstream → 500; transport failure → 502 (`app.py:762–799`).
- Non-atomic orphan/reconciliation gaps remain (M-3).
- No rate limit on avatar set/delete; not a correctness blocker but an abuse surface.

### 5.3 Email worker and delivery lifecycle

**Lifecycle**

- Queue creation: notification event processing resolves recipient email if absent (`events.py:145–166`), honoring mandatory-security override and `email_enabled` preference (`:137–142`).
- Claim: `process_batch` selects `pending`/`failed` rows whose `next_attempt_at <= now` and `attempts < max_attempts`, using `FOR UPDATE SKIP LOCKED` (`email_worker.py:362–376`), sets `status='sending'` and `sending_since` (`:378–381`), and commits before remote SMTP.
- Send: remote dispatch occurs outside the DB lock (`:386–400`).
- Completion: success sets `delivered`, clears `last_error` and `sending_since`; failure increments attempts, clears lease, and either retries with backoff or dead-letters (`:402–439`).
- Stale recovery: startup and periodic recovery select `sending` rows with `sending_since <= cutoff` or NULL, using `FOR UPDATE SKIP LOCKED`, increment the crashed attempt, and move to `failed` or `dead_letter` (`:258–351`).
- Migration: `notifications_0004` adds nullable `sending_since` (`migrations/notifications/versions/20261022_0004_email_delivery_lease.py:19–28`); model matches (`models.py:110`); config default is 300 seconds (`config.py:52–55`); Compose passes it (`docker-compose.yml:221`).

**Current runtime evidence**

- Running notification container source contains `recover_stale_leases`, `sending_since`, and the lease timeout (container grep at lines matching source).
- Live `notifications.email_deliveries` has `sending_since` (`timestamp with time zone`, nullable).
- Live version table `notifications.alembic_version_notifications` is `notifications_0004`.
- Live delivery rows: 4 `delivered`, 1 `dead_letter` (recipient rejected by allowlist), **no `sending` rows**. Startup recovery would currently have nothing to reclaim.

**Gaps**

- No live crash-during-SMTP or competing-worker test was run here.
- The worker’s at-least-once ambiguity is documented (`email_worker.py:304–308`): a crash after SMTP acceptance but before DB completion can duplicate an email.
- `stop()` cancels the polling task; cancellation during send can leave a `sending` row for later recovery. This is expected at-least-once behavior, not a silent failure, but it is not runtime-tested here.
- The allowlist is development-oriented and fails closed; production configuration must be explicit.

### 5.4 Database and migration readiness

**Revision chains**

| Service | Chain | Live version |
|---|---|---|
| Users | `users_0001` → `users_0002` → `users_0003` | `users_0003` |
| Notifications | `notifications_0001` → `notifications_0002` → `notifications_0003` → `notifications_0004` | `notifications_0004` |

Both chains are linear and have matching `down_revision` links. No duplicate revision identifiers were found.

**Live schema evidence**

- `users.alembic_version_users = users_0003`; `notifications.alembic_version_notifications = notifications_0004`.
- `users.email_change_requests` contains `resend_count` and `resend_available_at`.
- `notifications.email_deliveries` contains `sending_since`.
- `users.accounts` has unique indexes on `email_normalized` and `username_normalized`, plus the lower-case check constraints.
- `users.one_time_tokens` has primary key, account FK, attempts/resends checks, but no uniqueness guard for “one unconsumed token per account/purpose.”
- `users.email_change_requests` has primary key, account FK, account/new-email indexes, but **no** attempts/resend check constraints.

**Readiness**

- Retained-volume controlled rebuild: migrations are already at heads; still verify after rebuild and before starting the worker.
- Fresh/reset environment: apply all migrations before starting services; the new notification migration must be present.
- Model/migration drift (M-1) should be resolved separately; do not use autogenerate as a validation shortcut.

### 5.5 Contracts and OpenAPI

**Aligned**

- Canonical OpenAPI requires `new_email` and `password` for email-change request (`forum_public_api.openapi.yaml:421–427`); source schema now requires both (`schemas.py:104–106`); focused test asserts generated OpenAPI includes `password` in `required` (`tests/test_user_auth_lifecycle.py:1050–1059`).
- Missing/null password → 422; wrong password → 400 `INVALID_PASSWORD` (`app.py:1283–1289`; test `:1010–1039`).
- Change-email verify returns the private profile shape, including `avatar_url` (`app.py:1663–1679`), matching `UserProfileOwn` (`openapi.yaml:1891–1917`).
- Avatar responses expose `avatar_url`; public profile matches `UserPublic` (`openapi.yaml:1869–1889`).
- Recovery verify success returns `reset_token` + `expires_at` (`app.py:1118–1124`); OpenAPI shape matches (`:245–257`).
- Recovery reset returns 204 (`app.py:1254`); OpenAPI matches (`:282–283`).

**Mismatches**

- Error status coverage gaps (M-6).
- Recovery request does not enforce `format: email` and can return 202 for malformed input (L-1).
- Top-level `/internal/users/{user_id}` is intentionally not in the public OpenAPI; this is expected, not a missing public contract.
- The **live** User Service OpenAPI still shows `password` optional (H-1); this is runtime divergence, not a source contract mismatch.

### 5.6 Restoration and runtime divergence

- `GET /internal/users/{user_id}` remains present in the working source (`app.py:1909–1940`) with `Depends(verify_internal_service_key)`.
- The preserved reference lacks that route; no wholesale copy was performed.
- The working source uses `X-Internal-Service-Key`; the running reference image uses `X-Internal-Key`.
- The working source requires email-change password; the running image does not.
- Worktree app is 1,941 lines; running image app is 1,848 lines.
- Notification runtime appears to contain the Task 031.1 lease code; User runtime does not contain the restored source.
- Running containers observed: `forum_user_service` (18h, stale), `forum_notification_service` (10h), `forum_api_gateway`, `forum_content_service`, `forum_postgres`, `forum_redis`, `forum_mailpit` healthy. No container was altered.
- The Content Service’s development missing-key bypass is active because `ENVIRONMENT` is unset; therefore live avatar success would not prove the header is correct (M-5).

---

## 6. Migration and contract readiness summary

| Item | Status | Evidence |
|---|---|---|
| User migration chain | Ready | `users_0001` → `0002` → `0003`; live `users_0003` |
| Notification migration chain | Ready, but untracked head | `notifications_0001` → `0002` → `0003` → `0004`; live `notifications_0004`; new file untracked |
| `sending_since` column | Present in live DB and model | Read-only column query; `models.py:110` |
| Email-change resend columns | Present in live DB | Read-only column query |
| Email-change check constraints | Drift | Model declares; migrations and live DB lack them |
| Recovery token uniqueness | Not enforced; live duplicates exist | H-2 |
| OpenAPI required password | Source and canonical align; live runtime stale | `schemas.py:104–106`; live OpenAPI differs |
| OpenAPI error statuses | Incomplete | M-6 |
| Migration auto-run | No | Compose starts services directly; explicit migration needed |
| Clean-checkout reproducibility | Not yet | H-3 |

---

## 7. Requirement-to-test coverage matrix

Status meanings: **Reported pass** = prior task report says it passed, not re-run here; **Static only** = source inspection only; **Deferred** = requires live DB/stack/browser and was not run.

| Requirement | Existing test/evidence | Status / gap |
|---|---|---|
| Neutral recovery request for known/unknown account | `tests/test_user_auth_lifecycle.py::test_password_recovery_and_reset_lifecycle` | Reported pass; not re-run |
| Recovery OTP verify, replay prevention, reset-token issuance | Same lifecycle test | Reported pass; not re-run |
| Recovery expiry, attempt lockout, concurrent single winner | `tests/test_recovery_concurrency_pg.py` (3 tests) | Deferred; writes live PostgreSQL and uses live gateway |
| Password history (current + last 5) and session revocation | Lifecycle test; `test_recovery_concurrency_pg.py::test_postgres_concurrent_reset_ticket_single_winner` | In-process reported pass; PG deferred |
| Email-change password required / null / wrong / correct | `tests/test_user_auth_lifecycle.py::test_change_email_request_requires_password` | Reported pass; not re-run |
| Email-change anti-enumeration and attempt lockout | `tests/test_avatar_email_change_pg.py` | Deferred; live stack |
| Concurrent email-change verify single winner | `tests/test_avatar_email_change_pg.py::test_concurrent_email_change_verification` | Deferred; live stack |
| Avatar set/replace/delete and public serving | `tests/test_avatar_email_change_pg.py::test_avatar_lifecycle_and_permissions` | Deferred; live stack |
| Avatar ownership rejection | `tests/test_avatar_email_change_pg.py::test_avatar_unowned_attachment_rejection` | Deferred; live stack |
| Avatar missing/upstream failure/reconciliation | No dedicated test found | Gap; static inspection only |
| `GET /internal/users/{user_id}` and key enforcement | No direct test found | Gap; route static only; live route currently 404 |
| Worker claim/send/retry/dead-letter | `tests/test_email_dispatch_worker.py` (23 tests) | Reported pass; not re-run |
| Lease recovery, active-lease protection, crash recovery, concurrent workers | `tests/test_email_worker_lease_pg.py` (12 tests) | Reported pass in 031.1; not re-run |
| Mandatory security vs optional preference | `test_email_dispatch_worker.py::test_moderation_email_queued_regardless_of_preference`, `test_email_not_queued_when_email_disabled_for_optional_activity` | Reported pass; not re-run |
| SMTP/Mailpit end-to-end | Prior live checks in 031.1 report; `tests/test_email_delivery.py` unit mocks | Unit only or prior claim; current live Mailpit not re-run |
| Browser recovery and avatar/email-change journeys | `tests/test_browser_e2e.py:1012`, `:1124` | Deferred; requires rebuilt stack + Mailpit |
| Contract required password | `test_change_email_request_requires_password` generated-schema assertion | Reported pass; canonical/source aligned |
| Contract error statuses (409/429/502) | No comprehensive contract test found | Gap; M-6 |

---

## 8. Deferred runtime checks and why

| Check | Why deferred |
|---|---|
| `tests/test_email_worker_lease_pg.py` (12) | Writes live `forum_notifications`; requires controlled test data and cleanup |
| `tests/test_recovery_concurrency_pg.py` (3) | Writes live `forum_users`; depends on rebuilt User Service and gateway |
| `tests/test_avatar_email_change_pg.py` (5) | Writes live user/content DBs; uploads files; needs rebuilt User Service and live Content Service |
| Live Mailpit validation for registration, recovery, email-change, and welcome emails | Sends mail and writes database rows; must follow rebuild and allowlist confirmation |
| Worker crash/restart and stale-lease recovery on the live stack | Requires container restart and database state changes |
| Browser recovery/avatar/email-change E2E | Requires rebuilt services, Mailpit, and browser stack |
| Post-rebuild migration/version verification | Deferred so no migration or DB write occurs in this task |
| Internal key enforcement without the development bypass | Would require changing environment/runtime or a targeted unit test; current runtime is `development` |
| Duplicate recovery-token cleanup/reproduction | Requires DB write; current live data condition (H-2) is recorded only |
| Contract tests for 409/429/502 paths | No existing safe isolated test; current preflight is static only |

---

## 9. Proposed safe execution sequence for the next task

This sequence is a recommendation only; none of it was executed here.

### Phase 0 — Freeze and snapshot

1. Stop making working-tree changes. Record `git status --short`, `git diff`, `git diff --stat`, and `git diff --check`.
2. Save a reviewable patch and copies of untracked files outside the repository (for example under `/tmp/opencode/task_031_2_4_snapshot/`).
3. Capture `docker image inspect` metadata and tag the current user/notification images with a rollback tag before rebuilding.
4. Dump the retained databases read-only-consistently (`pg_dump -Fc` for `forum_users` and `forum_notifications`, plus content if avatar testing will mutate it). Record `users.alembic_version_users`, `notifications.alembic_version_notifications`, the duplicate-token query, and current delivery status counts.
5. Decide, in a separately authorized task, how to handle:
   - the two unconsumed recovery tokens for account `2735494c-…` (cleanup or code-level limit);
   - the user model/migration drift;
   - committing the untracked notification migration and lease test.
   Do not run recovery concurrency tests against the affected account until this is resolved.

### Phase 1 — Source-control and migration preparation

6. Review and commit the validated working-tree changes on a dedicated branch. At minimum, the new migration and `tests/test_email_worker_lease_pg.py` must be tracked before a clean/CI build.
7. Verify the migration heads offline and against the retained DB without applying writes where possible; then, with notification/user services stopped, apply the required migrations:
   - current retained DB should already be `users_0003` / `notifications_0004`;
   - a fresh environment needs `users_0001..0003` and `notifications_0001..0004`.
8. Re-check the version tables and required columns before starting any service.

### Phase 2 — Controlled rebuild

9. Rebuild only the services whose source changed: `user-service` and, if desired for runtime confirmation, `notification-service`.
10. Recreate those containers only; do not recreate unrelated services.
11. Wait for health:
    - User `/health/ready`;
    - Notification `/health/ready`;
    - Gateway dependency checks.
12. Verify the rebuilt runtime explicitly:
    - live User OpenAPI includes `/internal/users/{user_id}`;
    - `ChangeEmailRequest.required` includes `password`;
    - avatar source uses `X-Internal-Service-Key`;
    - avatar handlers and email-change resend are present;
    - notification worker source still contains lease recovery.

### Phase 3 — PostgreSQL integration validation

13. Run, sequentially and with cleanup:
    - `tests/test_email_worker_lease_pg.py`;
    - `tests/test_recovery_concurrency_pg.py`;
    - `tests/test_avatar_email_change_pg.py`.
14. After each suite, confirm no leftover test accounts, deliveries, sessions, tokens, or attachments.
15. Explicitly verify the H-2 account behavior: either it is quarantined/cleaned, or the endpoint is confirmed to return a controlled result rather than 500.

### Phase 4 — Mailpit and browser validation

16. With Mailpit running and allowlist confirmed, test:
    - registration OTP;
    - password-recovery OTP and reset;
    - email-change request and resend;
    - welcome/mandatory security email;
    - optional-activity suppression with `email_enabled=false`.
17. Confirm `email_deliveries` transitions `pending` → `sending` → `delivered`, `sending_since` is set/cleared, and no stale `sending` rows remain.
18. Run the browser recovery journey (`tests/test_browser_e2e.py::test_browser_account_recovery_and_password_reset_journey`) and avatar/email-change journey (`::test_browser_avatar_and_email_change_lifecycle`).

### Phase 5 — Rollback preparation and exit criteria

19. Define rollback triggers before Phase 2: failed migration, unhealthy service, failing PG suite, or browser regression.
20. Rollback path: recreate the previously tagged images, restore the pre-validation DB dump if writes must be undone, and re-verify the version tables.
21. Exit criteria: all four phases pass; no duplicate active-token condition remains; live OpenAPI matches source; Mailpit and browser journeys pass; no unexpected stale `sending` rows; working-tree changes are committed and reproducible.

---

## 10. No-mutation confirmation

This task made **no** changes to application source, tests, migrations, migrations state, configuration, Docker Compose, environment files, or documentation other than creating this single report.

Specifically:

- No `git add`, commit, stash, reset, clean, checkout, or discard was performed.
- No container was rebuilt, recreated, restarted, stopped, or removed.
- No image was loaded, retagged, replaced, or deleted.
- No Alembic migration was applied or rolled back.
- No database `INSERT`, `UPDATE`, `DELETE`, `DDL`, or migration write was performed.
- No test suite, browser test, or Mailpit send was executed.
- The only new file is `docs/audit/task_031_2_4_integration_preflight_report.md`.

Read-only operations used: `git` inspection, file reads, `grep`, `docker ps/inspect/exec` inspection, HTTP `GET` probes, and PostgreSQL `SELECT` queries.
