# Task 031.2.7 — Pre-Rebuild Blocker Remediation Plan

**Mode:** Read-only repository, Docker, and database inspection. No rebuild, retag, container start/stop/restart/removal, migration, backup, restore, stress test, or runtime change was performed.  
**Date:** 2026-10-23 (Asia/Bangkok)  
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`  
**Baseline commit:** `bf3384c4c3b53b5f826ef119f2ad549997ff1d63`  
**Reviewed document:** `docs/audit/task_031_2_6_rebuild_readiness_and_rollback_plan.md`  
**Verdict:** **BLOCKED — NOT READY FOR CONTROLLED REBUILD**

**Evidence legend**

- **Verified** — rechecked directly in this task against the current worktree, Docker metadata, read-only PostgreSQL queries, or files inside running containers.
- **Report-derived** — taken from a prior Task 031 report and not independently reproduced here.
- **Assumption** — a planning inference that must be checked during remediation.
- **Deferred** — intentionally not performed in this task; belongs to a later implementation or execution task.

---

## 1. Executive verdict

### 1.1 Verdict: `BLOCKED` for controlled rebuild

The Task 031.2.6 plan is a sound reconstruction plan, but **none of its hard execution gates has been implemented**. This task rechecked the current state and confirmed that the blockers still exist. The remediation plan below is complete, but it is not an execution authorization.

The rebuild is blocked by seven P0 workstreams:

1. The required source, migration, and test artifacts are still uncommitted; a clean checkout of current `HEAD` is not reproducible.
2. The database restore script can target live database names when invoked without a prefix, and the staging runbook instructs exactly that unsafe invocation.
3. The backup path has no checksum verification, the tracked backup/restore test asserts obsolete heads, and no verified isolated restore rehearsal has been performed.
4. The migration/runbook path uses commands that are not read-only or that apply `upgrade all` to live databases.
5. The development/test hooks are blocked only when `ENVIRONMENT=production`; staging still exposes `/test/otp` and `/test/reset-rate-limiters`.
6. Only the User image has a preservation tag. The other five current application images are recoverable only through mutable `latest` tags and recorded IDs.
7. The current running containers and images do not implement the new internal-service authentication contract, and the current stack is internally inconsistent.

Two P1 workstreams remain open:

8. One live account has two unconsumed recovery tokens; source-level normalization exists, but the data invariant and future partial unique index are not in place.
9. Test-data, credential, and backup-artifact hygiene must be fixed before smoke/backup workflows can be accepted.

Deferred maintenance items are separate: model/live constraint drift, historical tracked backups, the older Gateway image, and the permanent Mailpit allowlist policy.

### 1.2 What this task did not do

- It did not modify the existing Task 031.2.6 report.
- It did not implement any remediation, commit, migration, backup, restore, image tag, or Compose change.
- It did not rebuild, retag, load, export, delete, start, stop, restart, or recreate any image or container.
- It did not execute migrations, backups, restores, stress tests, Mailpit tests, or browser tests.
- It did not modify database data or schema.
- It did not print secrets, credentials, token values, email addresses, or personal data.

### 1.3 Relationship to Task 031.2.6

Task 031.2.6 concluded `READY WITH EXPLICIT RISKS` and defined gates G1–G10 (`docs/audit/task_031_2_6_rebuild_readiness_and_rollback_plan.md:7,816-825`). This task does not treat those gates as satisfied. It rechecks them, maps them to remediation work, and adds a first-class blocker for the staging-only test-hook exposure found in the current User source.

---

## 2. Current-state evidence

### 2.1 Git state

**Verified** at the start of this task:

| Item | Current value |
|---|---|
| Branch | `master` |
| HEAD | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| Staged files | None |
| Tracked modified files | 25 |
| Untracked files | 21 |
| Total status entries | 46 |

The 21 untracked files comprise:

- 16 audit reports, including the Task 031.2.6 report;
- 5 required source/migration/test artifacts:
  - `src/common/internal_auth.py`
  - `migrations/notifications/versions/20261022_0004_email_delivery_lease.py`
  - `tests/conftest.py`
  - `tests/test_internal_service_auth.py`
  - `tests/test_email_worker_lease_pg.py`

This report is the 22nd untracked file. Nothing is staged.

### 2.2 Clean-checkout reproducibility

**Verified:**

| Artifact | HEAD | Current worktree |
|---|---|---|
| `src/common/internal_auth.py` | Absent | Present (untracked) |
| Notification 0004 migration | Absent | Present (untracked) |
| `tests/conftest.py` | Absent | Present (untracked) |
| `tests/test_internal_service_auth.py` | Absent | Present (untracked) |
| `tests/test_email_worker_lease_pg.py` | Absent | Present (untracked) |
| `src/services/user/app.py` | 985 lines | 1,952 lines |
| Notification lease implementation | Absent from HEAD worker | Present in worktree |
| Notification image worker | 441 lines | Current worktree worker has lease recovery |

A clean checkout of current `HEAD` therefore does **not** reproduce the current worktree. It omits the shared internal-auth module and Notification 0004 migration, uses the older User implementation, and does not include the lease test source. This is a P0 reproducibility blocker, not a documentation-only issue.

### 2.3 Running containers and images

**Verified** with read-only `docker ps`, `docker inspect`, and `docker images`:

| Compose service | Container | Image reference | Full image ID | Created | Health | Published port |
|---|---|---|---|---|---|---|
| `notification-service` | `forum_notification_service` | `forumplatform-notification-service:latest` | `sha256:429d8167327eec2f316034068f4bc0eee681ba263876b388c8042c86e9de3552` | 2026-10-23 00:18:33 +07 | healthy | 8005 |
| `gateway` | `forum_api_gateway` | `forumplatform-gateway:latest` | `sha256:010b8b460375538c25717d3799cef7bb476e4ee0f248529ee312624a6c389611` | 2026-10-22 16:40:37 +07 | healthy | 8000 |
| `user-service` | `forum_user_service` | `forumplatform-user-service:latest` | `sha256:4a1c1198a0ed1a33ac3c076016b7cf8aac1a0b9bd12840f519aaaa70396f3b90` | 2026-10-22 16:33:43 +07 | healthy | 8001 |
| `content-service` | `forum_content_service` | `forumplatform-content-service:latest` | `sha256:2f94eea2659add036e66dfea9d301bc799c518dfead73e6d007fd437d7200437` | 2026-10-22 16:29:19 +07 | healthy | 8002 |
| `interaction-service` | `forum_interaction_service` | `forumplatform-interaction-service:latest` | `sha256:3fe104995d8834671400c6a0e0ec2edf0a5366a78a4f2d7e823b34ea4f561165` | 2026-10-21 11:40:25 +07 | healthy | 8003 |
| `moderation-service` | `forum_moderation_service` | `forumplatform-moderation-service:latest` | `sha256:cbd91c33607b405054ef862593d25cd02ec98e28734ff98649f79b334266a7cd` | 2026-10-21 11:40:25 +07 | healthy | 8004 |
| `search-service` | `forum_search_service` | `forumplatform-search-service:latest` | `sha256:a19cbd6bd20d52a7720fba22cda4be7afa7497e00aac16f7def7ec2739903219` | 2026-10-21 11:40:25 +07 | healthy | 8006 |
| `postgres` | `forum_postgres` | `postgres:16-alpine` | `sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea` | 2026-10-21 09:47:33 +07 | healthy | 5432 |
| `redis` | `forum_redis` | `redis:7-alpine` | `sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499` | 2026-10-21 09:47:33 +07 | healthy | 6379 |
| `mailpit` | `forum_mailpit` | `axllent/mailpit:v1.31.6@sha256:23fe39e81bde596a918000b3135c0f85dc8bb4c9ea6be274b051143f3e300387` | `sha256:23fe39e81bde596a918000b3135c0f85dc8bb4c9ea6be274b051143f3e300387` | 2026-10-21 09:47:33 +07 | healthy | 8025 |

All containers were healthy with restart count `0`.

**Image preservation:**

- The only non-`latest` application tag is `forumplatform-user-service:recovery-4a1c1198a0ed`, resolving to the current User image ID.
- The pre-existing User export at `/tmp/opencode/user-service-4a1c1198a0ed.tar` is 108,246,016 bytes with SHA-256 `cfbd035a36ee22ac08a2c22056c2925c5fc6fc18459e85b57b308e470d59f6f4`.
- Content, Interaction, Moderation, Notification, and Search have only `latest`; their rollback availability depends on the image IDs above remaining in the local image store.
- Gateway also has only `latest`, but its source is unchanged in this worktree.

### 2.4 Current image/runtime mismatch

**Verified** by read-only inspection inside the six running application containers:

- None of the six images contains `/app/src/common/internal_auth.py`.
- None of the six images contains the `internal_service_auth_bypass` setting in `/app/src/common/config.py`.
- All six containers have the legacy `INTERNAL_SERVICE_KEY` environment key.
- None of the six containers has `INTERNAL_SERVICE_SECRET` or `ENVIRONMENT`.
- The running User image:
  - is 1,848 lines for `/app/src/services/user/app.py`, versus 1,952 in the worktree;
  - sends `X-Internal-Key` at image lines 752 and 854;
  - does not contain `get_internal_user_contact`.
- The running Notification image:
  - contains `sending_since` and lease-recovery code;
  - sends `X-Internal-Service-Key` from `/app/src/services/notification/events.py:205`.
- The current stack is therefore internally inconsistent: Notification may send the canonical header while the running User service still uses the legacy header and lacks the internal-recipient endpoint.

### 2.5 Migration heads

**Verified** by read-only PostgreSQL queries:

| Database | Live head | File head | All revision files tracked? |
|---|---|---|---|
| `forum_users` | `users_0003` | `users_0003` | Yes |
| `forum_content` | `content_0003` | `content_0003` | Yes |
| `forum_interactions` | `interactions_0002` | `interactions_0002` | Yes |
| `forum_moderation` | `moderation_0003` | `moderation_0003` | Yes |
| `forum_notifications` | `notifications_0004` | `notifications_0004` | **No — 0004 is untracked** |
| `forum_search` | `search_0002` | `search_0002` | Yes |

`notifications_0004` is the sole Notification head and is live-applied. The source file is untracked. This means the live database is ahead of `HEAD` in reproducibility terms: the live schema can be used, but the file needed to recreate it is not in Git.

### 2.6 Live schema and recovery-token state

**Verified** with aggregate/read-only queries only:

| Check | Current result |
|---|---|
| Password-recovery tokens | 33 total; 3 unconsumed; 30 consumed |
| Accounts with more than one unconsumed recovery token | **1 account**, maximum 2 |
| Active email-change duplicate groups | 0 |
| `email_change_requests` check constraints | None |
| `one_time_tokens` check constraints | `ck_one_time_tokens_ck_one_time_attempts`, `ck_one_time_tokens_ck_one_time_resends` |
| Notification `sending_since` | Exists, nullable `timestamp with time zone` |
| Notification delivery status counts | `dead_letter=1`, `delivered=4`, no `sending` rows |

The restored User source already handles duplicate unconsumed recovery tokens by locking the rows, treating the newest as authoritative, and consuming older rows (`src/services/user/app.py:883-909`). The recovery request path also invalidates prior unconsumed tokens before inserting a new one (`:968-1000`). No partial unique index exists, and the live duplicate data remains.

### 2.7 Internal-service configuration evidence

**Verified:**

- Compose requires `INTERNAL_SERVICE_SECRET` for all six application services via `${INTERNAL_SERVICE_SECRET:?INTERNAL_SERVICE_SECRET must be set}` (`docker-compose.yml:86,121,156,189,222,265`).
- `ENVIRONMENT` defaults to `development` (`docker-compose.yml:85,120,155,188,221,264`).
- `INTERNAL_SERVICE_AUTH_BYPASS` defaults to `false` (`docker-compose.yml:87,122,157,190,223,266`).
- With `INTERNAL_SERVICE_SECRET` unset, `docker compose config --quiet` exits `1` with six interpolation errors, before any container operation.
- The current source rejects an empty internal secret (`src/common/config.py:81-87`).
- The current source rejects bypass in staging and production (`src/common/config.py:89-99`).
- The canonical header is `X-Internal-Service-Key` (`src/common/internal_auth.py:8`).
- The verifier fails closed when a supplied credential is wrong and when no credential is supplied without explicit bypass (`src/common/internal_auth.py:18-45`).
- `ENVIRONMENT=development` by itself does **not** enable bypass; bypass defaults to false.

The current running containers do not implement these source-level checks because they predate the 031.2.5c images.

### 2.8 Staging test-hook exposure

**Verified** in the current User source:

- `GET /test/otp` returns `404` only when `user_settings.environment == "production"` (`src/services/user/app.py:175-185`).
- `POST /test/reset-rate-limiters` returns `404` only when `user_settings.environment == "production"` (`src/services/user/app.py:188-202`).
- There is no staging guard and no explicit `ENABLE_TEST_HOOKS` setting.

Therefore, an explicit staging deployment would still expose both routes. The endpoints are hidden from OpenAPI, but hidden routes are still routable. This is a security control gap, not merely documentation drift.

### 2.9 Backup, restore, and migration tooling evidence

**Verified:**

- `scripts/backup/backup_databases.sh:21-28` selects the six application databases.
- `scripts/backup/backup_databases.sh:42` runs `pg_dump -Fc` through `docker compose exec`.
- `scripts/backup/backup_databases.sh:43-49` checks only that each dump is larger than 100 bytes.
- The script writes `backup_manifest.json` (`:60-67`) but does not generate SHA-256 checksums.
- `scripts/backup/restore_databases.sh:15` treats the target prefix as optional.
- `scripts/backup/restore_databases.sh:40` constructs `${TARGET_PREFIX}${base_db}`.
- If the prefix is empty, the restore target is the live database name.
- `scripts/backup/restore_databases.sh:52-55` drops/recreates only prefixed targets.
- `scripts/backup/restore_databases.sh:58-59` runs `pg_restore --clean --if-exists` and suppresses the first attempt's stderr, falling back to a second restore.
- `tests/test_backup_restore.py:33-39` expects `users_0002` and `notifications_0002`, which do not match the live `users_0003` and `notifications_0004` heads.
- `tests/test_backup_restore.py:53-82` is a live backup plus prefixed-restore drill, not a read-only test.
- The test embeds a development database DSN/credential in source. The value is intentionally not reproduced here.
- The staging runbook invokes `scripts/migrate.py upgrade all` and `scripts/migrate.py current all` (`docs/operations/staging_deployment_runbook.md:46-52`) and lists obsolete heads `users_0002`/`notifications_0002`.
- The same runbook restores without a prefix (`:83-86`), which is the unsafe invocation described above.
- `scripts/migrate.py:54-58` implements `current` through Alembic's `command.current`.
- `migrations/async_env.py:45-47` executes `CreateSchema(..., if_not_exists=True)` inside the migration environment. As a result, even a “current” invocation is not strictly read-only.
- Existing backup artifacts under `backups/` are tracked in Git. No new backup artifacts may be added to the worktree.

---

## 3. Prioritized blocker register

Priorities:

- **P0** — must be resolved before any rebuild or deployment.
- **P1** — must be resolved before the corresponding service or data workflow is accepted.
- **Deferred** — safe to postpone with an explicit follow-up owner/task.

| ID | Priority | Blocker | Evidence | Failure mode | Remediation |
|---|---|---|---|---|---|
| B1 | P0 | Required artifacts are uncommitted; clean checkout is not reproducible | Git status; five files absent from HEAD; HEAD User app 985 vs worktree 1,952; HEAD worker lacks lease | Clean build omits `internal_auth.py`/0004, imports fail or wrong code runs; fresh DB cannot reach Notification head; rollback source unavailable | R1 |
| B2 | P0 | Unsafe restore instructions/script | `restore_databases.sh:15,40,52-59`; runbook `:83-86` | Empty prefix targets live DBs; `--clean` drops/recreates objects in live schemas; data loss | R2 |
| B3 | P0 | Backup integrity, checksums, stale test heads, no verified rehearsal | `backup_databases.sh:43-67`; `test_backup_restore.py:33-39,53-82` | Corrupt/incomplete dump accepted; test cannot pass; restore never proven; hardcoded dev credential | R3 |
| B4 | P0 | Migration/runbook path is unsafe or not read-only | `migrate.py:54-58`; `async_env.py:45-47`; runbook `:46-52`; live heads already current | `current all` performs schema DDL; `upgrade all` unnecessary on live; wrong default target; stale heads | R4 |
| B5 | P0 for staging/production; P1 for local acceptance | Dev/test hooks reachable in staging | `user/app.py:175-202`; Compose `ENVIRONMENT` default `development` | OTP retrieval and rate-limit reset in staging; account takeover/DoS; test-only controls exposed | R5 |
| B6 | P0 | Only User has a preservation tag | `docker images`; User recovery tag; five services only `latest` | New build overwrites `latest`; no reliable application rollback; image GC could remove old versions | R6 |
| B7 | P0 | Internal auth/env contract is not deployed; current stack is mixed | Compose `:86...`; all six containers only `INTERNAL_SERVICE_KEY`; all six images lack helper/bypass; User old header/no endpoint; Notification canonical header | Partial rebuild causes 401/outage; old images ignore new secret; secret/bypass misconfiguration | R7 |
| B8 | P1 | Live duplicate active recovery tokens | 1 account with 2 unconsumed; 3 total unconsumed; no partial unique index | Ambiguous recovery behavior; future unique-index migration fails; duplicated/consumed state | R8 |
| B9 | P1 | Test-data, credential, and backup-artifact hygiene | Mailpit allowlist `docker-compose.yml:46`; test DSN in `test_backup_restore.py`; tracked `backups/` | Tests touch live data/accounts; credentials in source; backups accidentally committed | R9 |
| B10 | Deferred | Model/live constraint drift | `OneTimeToken` model/live constraint names; `EmailChangeRequest` model checks absent live | Future autogenerate noise or enforcement gaps; not a current rebuild blocker | R10 |
| B11 | Deferred | Older Gateway image; historical backups retention | Gateway source unchanged; image older than app changes; tracked backups | Gateway does not need rebuilding now; historical backups need separate security/retention review | R10 |

---

## 4. Ordered remediation tasks

The order below is a proposed implementation sequence. Task 031.2.7 does not implement any of these tasks.

### R1 — Preserve and commit the reproducible baseline

**Priority:** P0  
**Resolves:** B1  
**Owner:** repository maintainer / release engineer

**Required changes**

1. Commit the five required untracked source/migration/test artifacts and the 25 tracked modifications as an atomic runtime baseline. Keep the following groups together:
   - `src/common/internal_auth.py` + the service auth/config/outbox changes + Compose/env examples + internal-auth tests.
   - `migrations/notifications/versions/20261022_0004_email_delivery_lease.py` + `migrations/notifications/env.py` + lease tests + Notification worker/model/tests.
   - Restored User handlers + User schemas/tests.
2. Commit audit evidence separately if desired. Audit reports are not runtime dependencies, but Task 031.2.6 and this plan must be preserved.
3. Do not commit `.env`, secret-bearing files, or new backup artifacts.
4. Do not include generated caches.

**Measurable acceptance criteria**

- `git status --porcelain` is empty after the commit.
- A clean checkout of the new commit contains all five required files.
- `git hash-object` of each required file in the clean checkout matches the committed object.
- `src/services/user/app.py` in the commit is the 1,952-line version (or the reviewed final equivalent), not the 985-line HEAD version.
- `HEAD` contains `notifications_0004` and `src/common/internal_auth.py`.

**Verification command/test**

```text
git status --porcelain
git ls-tree -r HEAD --name-only | grep -E 'internal_auth.py|20261022_0004_email_delivery_lease.py|tests/conftest.py|test_internal_service_auth.py|test_email_worker_lease_pg.py'
git show HEAD:src/services/user/app.py | wc -l
git cat-file -e HEAD:src/common/internal_auth.py
```

Alternatively, create a temporary clean worktree/archive and assert those paths exist. Do not rebuild in this task.

**Dependencies and rollback**

- No runtime dependency.
- Rollback is a Git revert of the commit; no database or image change.
- The commit must not be deployed until R2–R7 are complete.

---

### R2 — Make restore safe by construction

**Priority:** P0  
**Resolves:** B2  
**Owner:** database owner / release engineer

**Required changes**

1. Change `scripts/backup/restore_databases.sh` so an empty or unsafe prefix is rejected before any `psql`, `DROP DATABASE`, `CREATE DATABASE`, or `pg_restore` invocation.
2. Require an explicit `rehearsal_`-style prefix, or a separate explicit `--allow-live` mode protected by a two-person confirmation. The default path must never target live names.
3. Add a live-name denylist check for the six application database names.
4. Stop suppressing `pg_restore` stderr; retain logs for acceptance.
5. Prefer `pg_restore --single-transaction` for full restore rehearsals so a failed restore does not leave partial objects. Document any incompatibility.
6. Update `docs/operations/staging_deployment_runbook.md` to remove the unprefixed rollback command and describe application rollback separately from database restore.
7. Add an automated guard test that invokes restore with an empty prefix and asserts a non-zero exit before any mutation.

**Measurable acceptance criteria**

- Empty-prefix invocation fails before connecting to PostgreSQL or calling Docker.
- Every target name is checked to begin with the approved rehearsal prefix and is not one of the six live database names.
- The runbook contains no command that restores into live names without an explicit guard.
- A test proves the guard and the `--single-transaction` behavior (future execution with a disposable target).

**Verification command/test**

```text
bash -n scripts/backup/restore_databases.sh
# Future focused test:
# invoke with an empty prefix and a temporary backup dir; expect non-zero exit and no docker/psql calls
```

**Dependencies and rollback**

- Depends on R3 backup artifacts for a real rehearsal.
- Script/runbook changes are reversible by Git revert; no runtime or database state changes.

---

### R3 — Add backup integrity, refresh the test, and prove a restore rehearsal

**Priority:** P0  
**Resolves:** B3  
**Owner:** database owner / QA

**Required changes**

1. Update the backup workflow to generate a `SHA256SUMS` file for the six dumps.
2. Verify each dump with `pg_restore --list` and record the archive listing.
3. Update `tests/test_backup_restore.py`:
   - expected heads to `users_0003` and `notifications_0004`, or dynamic values derived from a reviewed source of truth;
   - remove the hardcoded development DSN/credential; read the test URL from an explicit test environment;
   - ensure the test cannot target a live database and cleans up prefixed targets.
4. Update `docs/operations/backup_restore_runbook.md` to match the actual script and current heads.
5. Perform a one-time restore rehearsal into a separate disposable PostgreSQL instance or `rehearsal_` databases on the existing server.
6. Verify restored Alembic heads, required columns/indexes/constraints, and representative row counts without selecting personal data.

**Measurable acceptance criteria**

- A backup produces six non-empty dumps, a completed manifest, and `SHA256SUMS`.
- `sha256sum -c SHA256SUMS` passes.
- `pg_restore --list` succeeds for all six dumps.
- The rehearsal restores all six databases to `users_0003`, `content_0003`, `interactions_0002`, `moderation_0003`, `notifications_0004`, and `search_0002` respectively.
- `notifications.email_deliveries.sending_since` and the User resend columns exist after restore.
- No live database is a restore target.
- The updated test passes or is replaced by an equivalent guarded test.

**Verification command/test**

```text
# FUTURE — NOT RUN
sha256sum -c SHA256SUMS
pg_restore --list <each dump>
# Read-only SQL against the rehearsal targets:
SELECT version_num FROM users.alembic_version_users;
SELECT version_num FROM notifications.alembic_version_notifications;
SELECT column_name, is_nullable FROM information_schema.columns
  WHERE table_schema='notifications' AND table_name='email_deliveries'
    AND column_name='sending_since';
```

Representative counts: `users.accounts`, `users.one_time_tokens`, `users.email_change_requests`, `notifications.email_deliveries`, `content.threads`, `content.comments`, `interactions.votes`, `moderation.reports`, `search.documents`. Do not select row contents.

**Dependencies and rollback**

- Depends on R1 (test/source commit) and R2 (safe target guard).
- Scripts/tests/docs only; no live database mutation except the isolated rehearsal.
- Revert scripts/tests by Git if needed; the rehearsal target is disposable.

---

### R4 — Make migration execution explicit and non-destructive on live

**Priority:** P0  
**Resolves:** B4  
**Owner:** database owner / release engineer

**Required changes**

1. Change `scripts/migrate.py current` so it does not execute `CREATE SCHEMA` or any DDL, or explicitly document it as a mutating command and stop using it for verification.
2. Ensure migration commands resolve explicit target URLs and print/verify the target identity (`current_database()`, server, port) before applying anything. Do not rely on hardcoded defaults.
3. Update the staging runbook:
   - current live environment: verify only;
   - fresh environment: backup, target verification, then apply the intended revisions;
   - remove obsolete heads `users_0002`/`notifications_0002`;
   - do not use `upgrade all` on a live database already at the current heads;
   - do not use `current all` as a read-only check.
4. Add a test that asserts `current` does not create schemas, or a test that the runbook uses read-only SQL for verification.
5. Keep `notifications_0004` as the sole Notification head; if a new index migration is added later, update the graph and gates.

**Measurable acceptance criteria**

- A read-only verification path exists and is used by the runbook.
- `current` against a fresh disposable database leaves the schema set unchanged, or is reclassified as mutating and removed from verification procedures.
- The runbook contains current heads and explicit target checks.
- No procedure applies `upgrade all` to a live database whose heads are current.
- Fresh/rehearsal migration succeeds with the expected six heads.

**Verification command/test**

```text
# FUTURE — NOT RUN
# Read-only:
SELECT current_database(), current_user;
SELECT version_num FROM users.alembic_version_users;
# After a disposable migration:
SELECT version_num FROM notifications.alembic_version_notifications;
```

Static check:

```text
grep -R "migrate.py current" docs scripts
```

**Dependencies and rollback**

- Depends on R3 verified backup for any migration execution.
- Migration/runbook changes are source-only; revert by Git.
- Do not downgrade automatically; both known downgrades are data-lossy.

---

### R5 — Restrict development/test hooks outside development/test

**Priority:** P0 for staging/production; P1 for local rebuild acceptance  
**Resolves:** B5  
**Owner:** User-service maintainer / security reviewer

**Required changes**

1. Change the guard for `/test/otp` and `/test/reset-rate-limiters` from `environment == "production"` to an explicit allowlist:
   - allow only `development` and `test`, or
   - require a separate `ENABLE_TEST_HOOKS=true` setting that defaults false and is rejected in staging/production.
2. Keep the routes out of OpenAPI.
3. Add focused tests for:
   - development/test: route behavior remains available;
   - staging: `404`;
   - production: `404`.
4. Ensure staging/production Compose/security settings do not inherit a development fallback.

**Measurable acceptance criteria**

- With `ENVIRONMENT=staging`, both routes return `404`.
- With `ENVIRONMENT=production`, both routes return `404`.
- With `ENVIRONMENT=development` or `test`, the intended test behavior remains available.
- The routes are not in the OpenAPI schema.
- A test fails if the guard regresses to a production-only check.

**Verification command/test**

```text
# FUTURE — NOT RUN
# Focused tests with staging/production/development settings:
# - GET /test/otp -> 404 for staging/production
# - POST /test/reset-rate-limiters -> 404 for staging/production
```

**Dependencies and rollback**

- Depends on R1 and R7 environment configuration.
- Source-only change; revert by Git. No database impact.
- If the rebuild target is strictly local development, this can be accepted as P1 only if `ENVIRONMENT=development` and the hooks are not reachable outside localhost/isolated development networks. It must not be promoted to staging.

---

### R6 — Preserve all six current application images

**Priority:** P0  
**Resolves:** B6  
**Owner:** release engineer

**Required changes**

1. Before any build, record each service's current full image ID, tag, container ID, creation time, and digest (if available).
2. Create immutable non-`latest` tags for all six current application images, for example:
   - `forumplatform-user-service:pre-031-2-7-4a1c1198a0ed`
   - `forumplatform-content-service:pre-031-2-7-2f94eea2659a`
   - `forumplatform-interaction-service:pre-031-2-7-3fe104995d88`
   - `forumplatform-moderation-service:pre-031-2-7-cbd91c33607b`
   - `forumplatform-notification-service:pre-031-2-7-429d8167327e`
   - `forumplatform-search-service:pre-031-2-7-a19cbd6bd20d`
3. Verify each tag resolves to the recorded full image ID.
4. Preserve the existing User recovery tag and export checksum; do not overwrite them.
5. Optionally export each image to a restricted path outside the worktree with a SHA-256 checksum.
6. Retain old images and tags until the rebuilt stack has passed all acceptance gates.

**Measurable acceptance criteria**

- Six immutable tags exist, none named `latest`.
- Each tag resolves to its recorded current image ID.
- The User recovery tag still resolves to `4a1c1198a0ed…`; the export checksum is unchanged.
- A written image-preservation manifest records all IDs and tags.

**Verification command/test**

```text
# FUTURE — NOT RUN
docker image inspect --format '{{.Id}}' forumplatform-<service>:pre-031-2-7-<id>
docker image inspect --format '{{.Id}}' forumplatform-user-service:recovery-4a1c1198a0ed
```

**Dependencies and rollback**

- No runtime dependency; must happen before any build that replaces `latest`.
- Tag creation is additive. Do not delete tags until the new stack is accepted.
- If a tag cannot be created, the rollback gate fails and the rebuild stops.

---

### R7 — Resolve internal authentication and environment configuration, then rebuild all six services

**Priority:** P0  
**Resolves:** B7  
**Owner:** release engineer / security owner

**Required changes**

1. Complete R1 so the new shared auth code is committed.
2. Before any recreate, load `INTERNAL_SERVICE_SECRET` from an approved secret store; do not put it in shell history or process arguments.
3. Set `ENVIRONMENT` explicitly for the target environment; keep `INTERNAL_SERVICE_AUTH_BYPASS=false` for the rebuild.
4. Rebuild all six application services together; do not rebuild Gateway, PostgreSQL, Redis, or Mailpit unless separately required.
5. Verify the new images contain `src/common/internal_auth.py`, use `X-Internal-Service-Key`, and no longer depend on `INTERNAL_SERVICE_KEY`.
6. Start User first, then the other services, and Gateway last.
7. Run auth smoke tests:
   - missing header → `401`;
   - incorrect header and legacy header → `401`;
   - correct canonical header → expected `200` for a disposable account;
   - staging/production bypass configuration → rejected;
   - no development bypass in the rebuilt stack.

**Measurable acceptance criteria**

- `docker compose config --quiet` succeeds with the approved secret and fails without it.
- All six rebuilt containers have `INTERNAL_SERVICE_SECRET` and `ENVIRONMENT` and do not have `INTERNAL_SERVICE_KEY`.
- All six images contain the shared auth module.
- The canonical header is used by callers and receivers.
- Missing/wrong credentials fail closed; correct credentials succeed.
- No partial rebuild is accepted.

**Verification command/test**

```text
# FUTURE — NOT RUN
docker compose config --quiet
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' <container>  # inspect keys, not printed values
docker exec <container> test -f /app/src/common/internal_auth.py
# HTTP auth smoke with secret supplied through an approved mechanism
```

**Dependencies and rollback**

- Depends on R1, R5, R6, and R3.
- Preserved images provide application rollback; schema rollback is separate.
- If any service fails, stop before Gateway and follow the 031.2.6 rollback tree.
- Rotate the secret if it is exposed in logs or command history.

---

### R8 — Normalize duplicate recovery tokens and defer the unique index

**Priority:** P1  
**Resolves:** B8  
**Owner:** database owner / User-service maintainer

**Required changes**

1. Take and verify a backup before any live data normalization.
2. Identify duplicate active `password_recovery` groups without extracting token values or personal data.
3. Choose the newest row by `created_at`/`id` as authoritative; consume older rows with an audit timestamp.
4. Verify that no account has more than one unconsumed recovery token.
5. Add a focused test that seeds 2–3 duplicate legacy tokens in a disposable database and verifies the source normalization path.
6. Add a partial unique index only in a separate, later migration after zero duplicate groups are confirmed. Do not combine normalization and the index in one unverified step.
7. Update the 031.2.5a recovery-integrity report with the exact normalization result and the status of the index work.

**Measurable acceptance criteria**

- Before index work: duplicate active groups = 0; max active tokens per account ≤ 1.
- Source normalization test passes on disposable data.
- A later partial unique index migration applies successfully and rejects a second active recovery token.
- The live account is not touched during smoke tests.

**Verification command/test**

```text
# Read-only before/after:
WITH g AS (
  SELECT account_id, count(*) AS c
  FROM users.one_time_tokens
  WHERE purpose='password_recovery' AND consumed_at IS NULL
  GROUP BY account_id
  HAVING count(*) > 1
)
SELECT count(*), COALESCE(max(c),0) FROM g;
```

**Dependencies and rollback**

- Depends on R1, R3, and R7 for the verified source/runtime context.
- Data normalization is mutation; take a backup and record affected row counts/timestamps. Reversal requires a restore or an explicit compensating update; do not auto-downgrade.
- The unique index is Deferred and must not be applied before normalization.

---

### R9 — Enforce test-data, credential, and backup-artifact hygiene

**Priority:** P1  
**Resolves:** B9  
**Owner:** QA / security reviewer

**Required changes**

1. Use dedicated disposable accounts, unique test data, and an approved cleanup boundary for smoke tests.
2. Do not use the live duplicate-token account.
3. Address the Mailpit allowlist constraint:
   - use the two fixed permitted recipients, or
   - make an approved temporary allowlist change for the test window and revert it.
4. Remove hardcoded development credentials from tests; read test DSNs from an explicit test environment.
5. Do not add new backup artifacts to the worktree; use a restricted external destination.
6. Do not delete or rewrite existing tracked backups in this remediation; treat them as sensitive and schedule a separate security/retention review.
7. Document cleanup of accounts, tokens, sessions, deliveries, attachments, messages, and disposable databases.

**Measurable acceptance criteria**

- No smoke test mutates the live duplicate account.
- All test accounts/data are tagged and cleaned up.
- No test source contains a hardcoded credential.
- No new backup artifact is staged or committed.
- Mailpit accepts only approved test recipients.

**Verification command/test**

```text
# FUTURE — NOT RUN
# Static: ensure test DSNs are environment-driven and no literal credential remains.
# Runtime: Mailpit/API check for approved recipients; post-test cleanup counts.
```

**Dependencies and rollback**

- Depends on R3 and R7.
- No production data mutation. Cleanup failures are treated as a test failure.

---

### R10 — Deferred maintenance

**Priority:** Deferred  
**Resolves:** B10/B11  
**Owner:** database/schema maintainer, security reviewer, gateway maintainer

Deferred items:

1. **Model/live constraint drift.** `OneTimeToken` and `EmailChangeRequest` metadata do not exactly match live constraint names/absence. Do not generate speculative migrations during the rebuild. Schedule a focused schema-metadata alignment task.
2. **Historical tracked backups.** Existing `backups/` artifacts are tracked. Do not delete or rewrite them in this task. Review sensitivity, retention, and repository history in a separate approved security task.
3. **Gateway image age.** Gateway source is unchanged; no rebuild is required for the 031 changes. Rebuild only through a separate gateway change.
4. **Permanent Mailpit allowlist policy.** Temporary test-window exceptions are acceptable; permanent expansion should be a separate security/operations decision.
5. **Full stress and browser E2E.** The 031.2.6 stress design remains deferred to a dedicated disposable-database task.

**Acceptance criteria**

- Each deferred item has an owner and a follow-up task.
- None is silently declared resolved.
- No deferred item is a prerequisite hidden behind a P0 gate.

---

## 5. Execution gates

A failed P0 gate stops the rebuild. A failed P1 gate stops the corresponding workflow or promotion; it must not be silently accepted. Every gate must have recorded evidence.

| Gate | Scope | Pass criteria | Fail action |
|---|---|---|---|
| G1 — Artifact preservation | P0 | Clean checkout of the final commit contains all required files; `git status` clean; code hashes match; HEAD User app is the reviewed version; Notification 0004 is in HEAD | Stop; do not build |
| G2 — Image preservation | P0 | Six immutable non-`latest` tags resolve to the recorded current image IDs; User recovery tag/export checksum unchanged | Stop; do not build |
| G3 — Environment and internal auth | P0 | Compose config passes with secret; all six new containers have `INTERNAL_SERVICE_SECRET`/`ENVIRONMENT`, no legacy key; shared helper present; canonical header; bypass false; 401/200 smoke passes | Stop; do not start Gateway |
| G4 — Backup integrity | P0 | Six non-empty dumps, manifest, `SHA256SUMS`, `sha256sum -c`, `pg_restore --list`, heads/counts recorded | Stop; restart old stack |
| G5 — Restore rehearsal | P0 | Guarded `rehearsal_` restore succeeds; six heads and required columns/indexes/constraints match; no live target touched | Stop; do not migrate/rebuild |
| G6 — Migration graph/schema compatibility | P0 | One head per service; live heads match; fresh/rehearsal migration succeeds; schema checks pass; read-only verification used; no `upgrade all` on current live | Stop; do not start apps |
| G7 — Recovery-token data integrity | P1 for recovery workflow | Source normalization test passes on disposable data; live duplicate group documented and untouched; zero duplicate groups before any unique index | Stop recovery acceptance/index work; do not silently proceed |
| G8 — Staging security controls | P0 for staging/production | `/test/otp` and `/test/reset-rate-limiters` return `404` in staging/production; bypass rejected; explicit `ENVIRONMENT`; no dev hooks exposed | Stop staging/production promotion |
| G9 — Application smoke/post-rebuild | P0 | Health, internal auth, user flows, notification delivery, cross-service calls, and cleanup boundaries pass; no live duplicate account touched | Stop; roll back |
| G10 — Rollback availability | P0 | Preserved images, verified backup, and guarded restore path available; image rollback and DB rollback treated separately | Stop; do not declare success |

**Gate rule:** no P0 failure may be converted into an accepted risk without an explicit change to the verdict and a new approval. A plan alone does not close a gate.

---

## 6. Deferred risks

| Risk | Why deferred | Required follow-up |
|---|---|---|
| `OneTimeToken` constraint naming/model mismatch | Does not affect current runtime behavior; live constraints are stronger | Separate schema-metadata alignment task |
| `EmailChangeRequest` model checks absent live | Source enforces values; no pending migration | Separate migration review before adding constraints |
| Historical tracked backups | May contain sensitive data; removing/rewriting history is destructive | Security/retention review; do not delete now |
| Gateway image older than current app images | Gateway source unchanged and no internal-auth env | Rebuild only with a separate gateway change |
| Permanent Mailpit allowlist expansion | Security/operations policy decision | Separate decision; use temporary approved window for tests |
| Full stress and browser E2E | Requires disposable DB/isolated environment and dedicated approval | Dedicated execution task; do not run here |
| Recovery unique index | Must follow normalization; adding it now risks migration failure and data ambiguity | R8 follow-up after zero duplicate groups |

---

## 7. Proposed task sequence

No task below is executed by 031.2.7.

| Sequence | Proposed task | Priority | Closes | Deliverable |
|---|---|---|---|---|
| 1 | 031.2.8 — Preserve and commit the runtime/test baseline | P0 | G1 | Atomic runtime commit, clean-checkout proof, report commit |
| 2 | 031.2.9 — Backup/restore hardening and rehearsal | P0 | G4, G5 | Checksums, safe restore guard, refreshed test, verified rehearsal |
| 3 | 031.2.10 — Migration/runbook safety and staging hook guard | P0 | G6, G8 | Read-only current path, explicit target checks, runbook correction, hook guard tests |
| 4 | 031.2.11 — Recovery-token normalization | P1 | G7 | Zero duplicate groups, source test on disposable data, audit record |
| 5 | 031.2.12 — Image preservation and auth/env preflight | P0 | G2, G3 | Six immutable tags, secret/environment confirmation, full-six readiness review |
| 6 | 031.2.13 — Controlled rebuild execution | P0 | G9, G10 | Rebuilt stack, smoke evidence, rollback readiness |
| 7 | 031.2.14 — Recovery unique index and schema drift | Deferred | — | Partial unique index after normalization; metadata alignment |

The rebuild execution task must not start until all P0 gates pass. G7 is a recovery-workflow gate and must stop any unique-index or recovery-acceptance work, even if the local rebuild itself is otherwise ready.

---

## 8. Checks performed in this task

**Performed read-only:**

- Git branch, HEAD, staged/modified/untracked state, and HEAD file presence.
- Read-only inspection of current source, scripts, Compose, tests, and runbooks.
- `docker ps`, `docker inspect`, and `docker images` for container/image metadata, health, and tags.
- Read-only `docker exec` file checks for shared auth/module presence, header strings, and line counts.
- Read-only PostgreSQL queries in `BEGIN READ ONLY` transactions for heads, aggregate token counts, duplicate counts, constraints, and Notification schema/status counts.
- `docker compose config --quiet` with `INTERNAL_SERVICE_SECRET` unset to verify fail-fast behavior.
- Migration graph parsing and tracked/untracked mapping.
- Review of `docs/audit/task_031_2_6_rebuild_readiness_and_rollback_plan.md` and its gates/risks.

**Not performed:**

- No rebuild, build, retag, export, load, delete, start, stop, restart, or recreate.
- No migration apply/upgrade/downgrade/stamp.
- No backup or restore.
- No stress, Mailpit delivery, browser, or application smoke test.
- No source/config/runtime/database modification.
- No Git staging, commit, stash, reset, clean, or discard.
- No secret values, token values, email addresses, or personal data printed.

---

## 9. Safety confirmation

- The existing Task 031.2.6 report was not edited.
- No application source, configuration, migration, Compose file, `.env`, runtime state, or database data/schema was changed.
- No image or container was built, retagged, loaded, exported, deleted, started, stopped, restarted, or recreated.
- No backup, restore, stress test, Mailpit test, or browser test was run.
- No work was staged, committed, reset, cleaned, or stashed.
- All pre-existing modified and untracked artifacts were preserved.
- Exactly one new deliverable file was added: this report.

**Delivery path:** `docs/audit/task_031_2_7_pre_rebuild_blocker_remediation_plan.md`

**Final verdict:** **BLOCKED — remediation required before controlled rebuild.**
