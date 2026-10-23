# Task 031.2.6 — Controlled Rebuild Readiness and Rollback Plan

**Mode:** Read-only planning and inspection. No rebuild, container recreation, migration, backup, restore, stress test, Mailpit test, browser test, or Git mutation was performed in this task.  
**Date:** 2026-10-23 (Asia/Bangkok)  
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`  
**Baseline commit:** `bf3384c4c3b53b5f826ef119f2ad549997ff1d63`  
**Verdict:** **READY WITH EXPLICIT RISKS**

**Evidence legend**

- **Verified** — checked directly in this task against the worktree, Docker metadata, read-only PostgreSQL queries, or read-only inspection of files inside a running container.
- **Report-derived** — taken from a prior Task 031 audit and not independently reproduced here.
- **Assumption** — a planning inference that must be checked during execution.
- **Deferred** — intentionally not run in this task; belongs to the future execution or stress-test task.

---

## 1. Executive verdict

### 1.1 Verdict: `READY WITH EXPLICIT RISKS`

The worktree contains a coherent source-level implementation of the Task 031.1 and Task 031.2.3a–031.2.5c changes, and the live PostgreSQL databases already match the file-based migration heads. A controlled rebuild is technically feasible **only after the execution gates in Section 11 are closed**.

It is not `READY FOR CONTROLLED REBUILD` because:

1. The required artifacts are not committed. A clean checkout of `HEAD` omits `src/common/internal_auth.py`, `migrations/notifications/versions/20261022_0004_email_delivery_lease.py`, and required test files, and its tracked User/Notification code is not the current worktree implementation. The current worktree, not `HEAD`, is the only reproducible source today.
2. All six running application-service images are stale relative to the current shared internal-auth code. The User image is additionally stale relative to the restored User handlers. A partial rebuild would create a mixed old/new environment, because the current Compose file passes `INTERNAL_SERVICE_SECRET` while the running containers only have the legacy `INTERNAL_SERVICE_KEY`.
3. Only the User image has a preservation tag. The other five current application images are retrievable only through the mutable `latest` tag and their recorded image IDs, which is not a sufficient rollback inventory.
4. The backup tooling is weakened by drift: the actual backup script does not create the checksums claimed by older audit text, and the tracked backup/restore test asserts obsolete migration heads.
5. The staging runbook's rollback command invokes the restore script without a prefix. In the actual script that can target the live database names and run `pg_restore --clean`. That command must not be used.
6. Live `users` data contains one account with two unconsumed password-recovery tokens. The restored source has runtime duplicate normalization, but the database invariant and partial unique index are still future work.

It is not `BLOCKED` because the source can be made reproducible with a commit, the live schema is already at the current heads, Compose fails before container operations when the required secret is missing, the User recovery image and export are verified, and the duplicate-token behavior is already handled by the restored source.

### 1.2 What this plan deliberately does not do

- It does not build, rebuild, pull, load, retag, delete, start, stop, restart, recreate, or remove any image or container.
- It does not apply, upgrade, downgrade, stamp, or otherwise alter any migration.
- It does not modify any database row or schema.
- It does not run a backup, restore, Mailpit delivery test, browser E2E, PostgreSQL stress test, or concurrency test.
- It does not edit `.env`, `.env.staging`, Compose, source, tests, or runtime configuration.
- It does not stage, commit, stash, reset, clean, or discard any work.

All commands in Sections 6–10 marked **FUTURE — NOT RUN** are planned execution steps only.

---

## 2. Initial repository and runtime snapshot

### 2.1 Git state

**Verified** before this report was added:

| Item | Value |
|---|---|
| Branch | `master` |
| HEAD | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| HEAD date | 2026-10-22 22:41:09 +07:00 |
| HEAD subject | `feat(tasks 028-031): account recovery, avatar & email-change, outbound email dispatch` |
| Staged files | None |
| Tracked modified files | 25 |
| Untracked files | 20 (15 audit reports, and 5 required source/migration/test artifacts) |
| Total status entries | 45 |

The exact initial `git status --short` is reproduced in Appendix A.1. The report file created by this task is the 21st untracked file and is not part of that initial snapshot.

### 2.2 Container and image snapshot

**Verified** with read-only `docker ps` and `docker inspect`:

| Compose service | Container | Container ID (short) | Image reference | Full image ID | Container created | Health | Published port(s) | Restarts |
|---|---|---|---|---|---|---|---|---|
| `notification-service` | `forum_notification_service` | `a60d861d99bf` | `forumplatform-notification-service:latest` | `sha256:429d8167327eec2f316034068f4bc0eee681ba263876b388c8042c86e9de3552` | 2026-10-23 00:18:33 +07 | `healthy` | `8005` | 0 |
| `gateway` | `forum_api_gateway` | `7882580cf734` | `forumplatform-gateway:latest` | `sha256:010b8b460375538c25717d3799cef7bb476e4ee0f248529ee312624a6c389611` | 2026-10-22 16:40:37 +07 | `healthy` | `8000` | 0 |
| `user-service` | `forum_user_service` | `c65c42263b67` | `forumplatform-user-service:latest` | `sha256:4a1c1198a0ed1a33ac3c076016b7cf8aac1a0b9bd12840f519aaaa70396f3b90` | 2026-10-22 16:33:43 +07 | `healthy` | `8001` | 0 |
| `content-service` | `forum_content_service` | `7145f1802584` | `forumplatform-content-service:latest` | `sha256:2f94eea2659add036e66dfea9d301bc799c518dfead73e6d007fd437d7200437` | 2026-10-22 16:29:19 +07 | `healthy` | `8002` | 0 |
| `interaction-service` | `forum_interaction_service` | `dfedc3186bf6` | `forumplatform-interaction-service:latest` | `sha256:3fe104995d8834671400c6a0e0ec2edf0a5366a78a4f2d7e823b34ea4f561165` | 2026-10-21 11:40:25 +07 | `healthy` | `8003` | 0 |
| `moderation-service` | `forum_moderation_service` | `a38deaf35879` | `forumplatform-moderation-service:latest` | `sha256:cbd91c33607b405054ef862593d25cd02ec98e28734ff98649f79b334266a7cd` | 2026-10-21 11:40:25 +07 | `healthy` | `8004` | 0 |
| `search-service` | `forum_search_service` | `70e6be031f1f` | `forumplatform-search-service:latest` | `sha256:a19cbd6bd20d52a7720fba22cda4be7afa7497e00aac16f7def7ec2739903219` | 2026-10-21 11:40:25 +07 | `healthy` | `8006` | 0 |
| `postgres` | `forum_postgres` | `5694d5bbf581` | `postgres:16-alpine` | `sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea` | 2026-10-21 09:47:33 +07 | `healthy` | `5432` | 0 |
| `redis` | `forum_redis` | `3a0ec4e5fbdd` | `redis:7-alpine` | `sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499` | 2026-10-21 09:47:33 +07 | `healthy` | `6379` | 0 |
| `mailpit` | `forum_mailpit` | `e2129999b5d1` | `axllent/mailpit:v1.31.6@sha256:23fe39e81bde596a918000b3135c0f85dc8bb4c9ea6be274b051143f3e300387` | `sha256:23fe39e81bde596a918000b3135c0f85dc8bb4c9ea6be274b051143f3e300387` | 2026-10-21 09:47:33 +07 | `healthy` | `8025` (UI) | 0 |

All ten containers were running and healthy with restart count `0`. This healthy state does **not** prove that the running application code matches the worktree.

### 2.3 Live database inventory and migration heads

**Verified** by read-only `psql` transactions against `forum_postgres`:

| Logical database | Schema | Version table | Live head | File head | File tracked in Git? |
|---|---|---|---|---|---|
| `forum_users` | `users` | `users.alembic_version_users` | `users_0003` | `users_0003` | Yes |
| `forum_content` | `content` | `content.alembic_version_content` | `content_0003` | `content_0003` | Yes |
| `forum_interactions` | `interactions` | `interactions.alembic_version_interactions` | `interactions_0002` | `interactions_0002` | Yes |
| `forum_moderation` | `moderation` | `moderation.alembic_version_moderation` | `moderation_0003` | `moderation_0003` | Yes |
| `forum_notifications` | `notifications` | `notifications.alembic_version_notifications` | `notifications_0004` | `notifications_0004` | **No — the 0004 file is untracked** |
| `forum_search` | `search` | `search.alembic_version_search` | `search_0002` | `search_0002` | Yes |

The PostgreSQL instance also contains the non-application `postgres` database. No separate database container exists for each service; all six logical databases are hosted by `forum_postgres`.

### 2.4 Live schema and data findings

**Verified** without selecting personal data:

| Finding | Observed value |
|---|---|
| Notification `sending_since` | Exists as nullable `timestamp with time zone` |
| Notification delivery status counts | `dead_letter=1`, `delivered=4`, `sending=0` |
| Notification `email_deliveries` indexes | Primary key, unique `(event_id, recipient_id)`, status/next-attempt index, recipient index |
| Users password-recovery token rows | 33 total: 3 unconsumed, 30 consumed |
| Accounts with more than one unconsumed recovery token | **1 account**, maximum 2 per account |
| Active email-change duplicate groups | 0 |
| `email_change_requests` check constraints | None; only primary key and account foreign key |
| `one_time_tokens` live constraints | `ck_one_time_tokens_ck_one_time_attempts`, `ck_one_time_tokens_ck_one_time_resends`, primary key, account foreign key |
| `accounts` uniqueness | Unique lower-case email and username normalized values |

**Report-derived:** the prior 031.2.4 report states that both unconsumed tokens for the one account are expired, and the prior 031.2.5a report proposes the controlled normalization and partial unique index. This task verified only the counts and schema, not the token expiry or account identity.

### 2.5 Critical: the running images are not the worktree

**Verified** by read-only file inspection inside the running containers:

| Artifact | HEAD | Running image | Current worktree |
|---|---|---|---|
| `src/services/user/app.py` line count | 985 | 1,848 | **1,952** |
| User internal-call header | Not the current canonical header | `X-Internal-Key` at image lines 752 and 854 | `X-Internal-Service-Key` at current lines 753 and 863 |
| User `GET /internal/users/{user_id}` | Present in the pre-restoration HEAD file | **Absent from the running image** | Present at current line 1925 |
| User duplicate-recovery normalization | Not in the running image | **Absent** | Present at current lines 883–910 and used by recovery request/verify |
| `src/common/internal_auth.py` | Absent from HEAD | **Absent from all six application images** | Present (untracked) |
| `internal_service_auth_bypass` setting | Not in HEAD | **Absent from all six application images** | Present in `src/common/config.py:34-40` |
| Notification lease recovery | Absent from HEAD worker | **Present in the running Notification image** (`recover_stale_leases`, `sending_since`) | Present in `src/services/notification/email_worker.py:262-332` |
| Current User `ChangeEmailRequest.password` | — | Running image accepts optional password (report-derived) | Required at `src/services/user/schemas.py:104-106` |

Implication: no current artifact — HEAD, running image, or worktree — can be assumed to represent the desired final stack. The worktree must be committed, built, and validated as a unit.

---

## 3. Source-control and artifact preservation inventory

### 3.1 What a clean checkout of current HEAD does and does not contain

**Verified:**

- `HEAD:src/common/internal_auth.py` — absent.
- `HEAD:migrations/notifications/versions/20261022_0004_email_delivery_lease.py` — absent.
- `HEAD:tests/conftest.py` — absent.
- `HEAD:tests/test_internal_service_auth.py` — absent.
- `HEAD:tests/test_email_worker_lease_pg.py` — absent.
- `HEAD:src/services/user/app.py` is the older 985-line version, not the 1,952-line worktree version.
- `HEAD:src/services/notification/email_worker.py` does not contain the lease-recovery implementation.

Therefore, **a clean checkout of the current HEAD cannot reproduce the current worktree or its intended runtime**. Even a Docker build from HEAD would omit the shared internal-auth module and the Notification 0004 migration source, while using older User and Notification code.

This is not merely an untracked-file cosmetic issue. The current worktree is the only complete source of the Task 031.1 / 031.2.3–031.2.5c implementation. It must be preserved and committed in a later approved step.

### 3.2 Required artifacts

**Required runtime/source artifacts currently untracked:**

| Path | Why required | Disposition |
|---|---|---|
| `src/common/internal_auth.py` | Shared fail-closed internal credential verifier imported by service auth modules | Must be committed with all service auth changes |
| `migrations/notifications/versions/20261022_0004_email_delivery_lease.py` | Sole Notification head; adds nullable `sending_since` | Must be committed with `migrations/notifications/env.py` and lease tests |
| `tests/conftest.py` | Deterministic test environment for internal-auth tests | Must be committed with 031.2.5c tests |
| `tests/test_internal_service_auth.py` | Focused missing/incorrect/correct-secret and settings tests | Must be committed with 031.2.5c |
| `tests/test_email_worker_lease_pg.py` | Disposable-PostgreSQL lease/recovery tests | Must be committed with 031.1/Notification 0004 |

**Required tracked modifications currently unstaged (25):**

- Shared/runtime authentication:
  - `src/common/config.py`
  - `src/common/outbox_worker.py`
  - `src/services/user/auth.py`
  - `src/services/content/auth.py`
  - `src/services/search/auth.py`
  - `src/services/notification/auth.py`
  - `src/services/notification/config.py`
- User restoration:
  - `src/services/user/app.py`
  - `src/services/user/schemas.py`
- Notification lease:
  - `src/services/notification/email_worker.py`
  - `src/services/notification/models.py`
  - `migrations/notifications/env.py`
- Deployment/config examples:
  - `docker-compose.yml`
  - `.env.example`
  - `.env.staging.example`
- Tests and scripts:
  - `scripts/run_benchmarks.py`
  - `tests/test_e2e_integration.py`
  - `tests/test_email_dispatch_worker.py`
  - `tests/test_notification_service.py`
  - `tests/test_outbox_concurrency_live.py`
  - `tests/test_outbox_hardening.py`
  - `tests/test_outbox_worker.py`
  - `tests/test_search_service.py`
  - `tests/test_staging_operational_readiness.py`
  - `tests/test_user_auth_lifecycle.py`

**Untracked audit reports (15):** Task 031.1 design/change/validation, 031.2.1 inventory, 031.2.2 discrepancy, 031.2.3a–f restoration reports, 031.2.4 preflight, 031.2.5a–c reports. They are evidence, not runtime dependencies. Preserve them; they need not be in the same commit as runtime code.

### 3.3 Proposed later commit sequence

**Preferred: two commits, with the runtime commit atomic and not deployed in intermediate states.**

1. **Commit A — Task 031 integration baseline (runtime, migrations, tests, deployment configuration).**
   Include all required untracked source/migration/test files and the 25 tracked modifications above. This keeps the following inseparable groups together:
   - `src/common/internal_auth.py` + the five service auth modules + `src/common/config.py` + `src/common/outbox_worker.py` + Compose/env examples + internal-auth tests.
   - `migrations/notifications/versions/20261022_0004_email_delivery_lease.py` + `migrations/notifications/env.py` + `tests/test_email_worker_lease_pg.py` + notification worker/model/tests.
   - Restored User handlers + `tests/test_user_auth_lifecycle.py` + User schemas/tests.
   Do **not** include `.env`, backups, or generated caches. The existing `backups/` directory is tracked; no new backup artifacts may be added to Git.
2. **Commit B — Audit evidence.**
   Include the Task 031 audit reports and this report. This keeps the runtime commit reviewable without separating the evidence from the code review.

**Alternative granular sequence (only if the team requires it):** split 031.1 lease, 031.2.3 User restoration, and 031.2.5c internal auth into three commits, then a docs commit. However, intermediate commits must **not** be deployed, because the User app and service auth changes can temporarily disagree on the internal header/environment contract. Only the final commit is a deployable baseline.

**Future verification after commit:** create a temporary clean worktree from the new commit, confirm the five previously untracked required files exist, confirm the image build sees the new helper/migration, and run the approved static/unit checks. This is not a runtime rebuild.

### 3.4 Why the commit is a hard gate

The Dockerfile copies `src/`, `migrations/`, `scripts/`, `docs/`, and `frontend/` into the image (`Dockerfile:15-20`). A build from a clean checkout that lacks `src/common/internal_auth.py` will fail at import time once the modified service auth modules import it. A fresh database also cannot be migrated to Notification head without the untracked 0004 migration. The commit is therefore required for reproducibility, CI, and disaster recovery — not merely repository tidiness.

---

## 4. Image preservation and rebuild inventory

### 4.1 Services that need rebuilding

**Planning conclusion from verified source and runtime facts:** all six application services must be rebuilt together; the gateway does not need a rebuild for these changes.

| Compose service | Current image ID | Relevant source | Stale? | Reason |
|---|---|---|---|---|
| `user-service` | `4a1c1198a0ed…` | `src/services/user/`, `src/common/`, `migrations/users/` | **Yes** | Restored recovery/email-change/avatar handlers and canonical internal header are absent; image is 1,848 lines vs 1,952 current; missing `/internal/users/{user_id}` |
| `content-service` | `2f94eea2659a…` | `src/services/content/`, `src/common/` | **Yes** | No shared `internal_auth.py`, no bypass setting; image built before 031.2.5c |
| `interaction-service` | `3fe104995d88…` | `src/services/interaction/`, `src/common/` | **Yes** | No shared `internal_auth.py`; old outbox worker default key behavior; image built 2026-10-21 |
| `moderation-service` | `cbd91c33607b…` | `src/services/moderation/`, `src/common/` | **Yes** | Same shared auth/outbox drift; image built 2026-10-21 |
| `notification-service` | `429d8167327e…` | `src/services/notification/`, `src/common/`, `migrations/notifications/` | **Yes** | Contains 031.1 lease code but no shared `internal_auth.py`/bypass setting; image built before 031.2.5c |
| `search-service` | `a19cbd6bd20d…` | `src/services/search/`, `src/common/` | **Yes** | No shared `internal_auth.py`/bypass setting; image built 2026-10-21 |
| `gateway` | `010b8b460375…` | `src/gateway/` | **No changed source** | `src/gateway/` is not modified in this worktree; gateway has no internal-service auth environment in Compose |

**Why not a partial rebuild:** `src/common/config.py` and `src/common/outbox_worker.py` changed, and the current Compose file passes `INTERNAL_SERVICE_SECRET` while every running app container currently has only the legacy `INTERNAL_SERVICE_KEY`. Rebuilding only the User and Notification services would leave Content, Interaction, Moderation, and Search on old images that neither read the new secret nor use the canonical header. That is a mixed-contract deployment and must be avoided.

### 4.2 Existing preserved User image

**Verified:**

| Item | Value |
|---|---|
| Tag | `forumplatform-user-service:recovery-4a1c1198a0ed` |
| Resolved image ID | `sha256:4a1c1198a0ed1a33ac3c076016b7cf8aac1a0b9bd12840f519aaaa70396f3b90` |
| `latest` image ID | Same image ID |
| Export path | `/tmp/opencode/user-service-4a1c1198a0ed.tar` |
| Export size | 108,246,016 bytes |
| Export SHA-256 | `cfbd035a36ee22ac08a2c22056c2925c5fc6fc18459e85b57b308e470d59f6f4` |

The recovery tag and export were not created, moved, loaded, or otherwise changed in this task.

### 4.3 Image preservation procedure for the future execution task

**FUTURE — NOT RUN.** Perform this before any build that replaces `latest`.

1. For each of the six services, record:
   - `docker image inspect --format '{{.Id}} {{.Created}} {{json .RepoDigests}}' forumplatform-<service>:latest`
   - the running container ID and its `.Image` value.
   - the current Compose project/service labels.
2. Create an immutable, non-`latest` tag for each current image, for example:
   - `forumplatform-user-service:pre-031-2-6-4a1c1198a0ed`
   - `forumplatform-content-service:pre-031-2-6-2f94eea2659a`
   - `forumplatform-interaction-service:pre-031-2-6-3fe104995d88`
   - `forumplatform-moderation-service:pre-031-2-6-cbd91c33607b`
   - `forumplatform-notification-service:pre-031-2-6-429d8167327e`
   - `forumplatform-search-service:pre-031-2-6-a19cbd6bd20d`
3. Re-inspect each new tag and verify it resolves to the recorded full image ID. Do not treat a tag alone as proof of preservation.
4. For the User service, additionally verify the existing recovery tag and export checksum above; do not retag over it.
5. Optional, if disk and policy permit: `docker image save` each image to a restricted path outside the worktree and record a SHA-256 checksum. This is not required for Task 031.2.6 and was not done.
6. After building new images, record the new full image IDs and verify the recreated containers reference those IDs, not merely `latest`.

If immutable tags for the five non-User services cannot be created, the application rollback path for those services is not adequately prepared. Treat that as a failed gate, not an acceptable risk.

### 4.4 Build and rollout order

1. Build all six application images while the old stack remains running:
   - `user-service`
   - `content-service`
   - `interaction-service`
   - `moderation-service`
   - `notification-service`
   - `search-service`
2. Do not build or recreate `gateway`, `postgres`, `redis`, or `mailpit` unless a separate approved change requires it.
3. Recreate the User service first because Content, Search, and Notification depend on User behavior for sessions and recipient lookup.
4. Recreate Content, Interaction, Moderation, and Search next, then Notification, then Gateway last.
5. Do not start Gateway until all six service health checks and the internal-auth smoke checks pass.

---

## 5. Environment and internal-authentication readiness

### 5.1 Compose configuration

**Verified** in `docker-compose.yml`:

| Variable | Compose behavior | Services |
|---|---|---|
| `INTERNAL_SERVICE_SECRET` | Required interpolation with `:?`; missing value aborts `docker compose config` | `user-service`, `content-service`, `interaction-service`, `moderation-service`, `notification-service`, `search-service` (`docker-compose.yml:86,121,156,189,222,265`) |
| `ENVIRONMENT` | Defaults to `development` | Same six services (`docker-compose.yml:85,120,155,188,221,264`) |
| `INTERNAL_SERVICE_AUTH_BYPASS` | Defaults to `false` | Same six services (`docker-compose.yml:87,122,157,190,223,266`) |
| Legacy `INTERNAL_SERVICE_KEY` | Not referenced by current Compose | Only present on the old running containers |

**Verified fail-fast behavior:** with `INTERNAL_SERVICE_SECRET` unset, `docker compose config --quiet` exits `1` and reports six interpolation errors, one per application service. It fails before any build, stop, or recreate operation. This is the desired Compose behavior.

**Verified in the running stack:** all six application containers have `INTERNAL_SERVICE_KEY`, no `INTERNAL_SERVICE_SECRET`, and no `ENVIRONMENT`. The current containers are therefore running under the old configuration contract. They must not be recreated against the new Compose file until new images are built.

### 5.2 Source-level validation behavior

**Verified** in current source:

- `BaseServiceSettings.internal_service_secret` defaults to empty but has a field validator that rejects empty or whitespace-only values (`src/common/config.py:30-33,81-87`). An in-process settings construction without a secret was rejected with a `ValidationError`.
- Staging and production reject `INTERNAL_SERVICE_AUTH_BYPASS=true` (`src/common/config.py:89-99`). In-process checks accepted `bypass=false` for staging/production and rejected `bypass=true`.
- Staging and production also reject known placeholder secrets, secrets shorter than 32 characters, and database URLs containing the development default (`src/common/config.py:101-137`).
- The shared verifier always checks a supplied header with `hmac.compare_digest`; it accepts a missing header only when `internal_service_auth_bypass` is explicitly true (`src/common/internal_auth.py:18-45`).
- `ENVIRONMENT=development` is merely a default; it does **not** by itself enable bypass. Bypass remains `false` unless explicitly set.
- The canonical header is `X-Internal-Service-Key` (`src/common/internal_auth.py:8`). Current callers and receivers use it:
  - Outbox worker: `src/common/outbox_worker.py:29,176`
  - User avatar calls: `src/services/user/app.py:753,863`
  - Notification recipient lookup: `src/services/notification/events.py:205`
  - Service verifier wrappers: `src/services/user/auth.py:139-141`, `content/auth.py:150-152`, `search/auth.py:152-154`, `notification/auth.py:150-152`

### 5.3 Answers to the required readiness questions

| Question | Answer |
|---|---|
| Which services require the secret? | Six application services; Gateway does not receive it in Compose |
| Required variable or default? | Required via `:?`; no default for the secret |
| Do staging/production reject bypass? | Yes, settings validation rejects it |
| Can the development `ENVIRONMENT` fallback accidentally enable bypass? | No; bypass defaults to false and is explicit. However, leaving `ENVIRONMENT` at its development default weakens other staging/production validation and must not be done for a production-like deployment |
| Will deployment fail before recreating containers if the secret is missing? | Yes, Compose interpolation exits `1` before container operations |
| Is the canonical header consistent in source? | Yes, `X-Internal-Service-Key` across current callers and receivers |
| Is the running stack consistent? | No; old images use the old contract and the User image still sends `X-Internal-Key` |
| Is there a dangerous implicit fallback? | No implicit bypass was found in current source. The remaining risk is operator error: setting `ENVIRONMENT=development` or enabling bypass in a non-production environment, not an automatic fallback |

**Gate:** the execution task must set `ENVIRONMENT` explicitly to the intended environment, set `INTERNAL_SERVICE_AUTH_BYPASS=false` for the rebuild, and load the approved `INTERNAL_SERVICE_SECRET` from a secret store without echoing it. If `ENVIRONMENT` is staging or production, the secret must meet the configured length/default checks.

**Gate:** because the current running containers use the old env contract, the build must complete and the new images must be verified **before** `docker compose up` recreates anything.

---

## 6. Migration and database safety plan

### 6.1 Migration inventory

| Service/database | Live head | File head | New revision relative to HEAD? | Tracked? | Expected schema effect | App/worker dependency | Reversible? |
|---|---|---|---|---|---|---|---|
| Users / `forum_users` | `users_0003` | `users_0003` | Yes, relative to HEAD's earlier state | Yes | Adds email-change `resend_count` and `resend_available_at`; indexes | Email-change and recovery resend behavior | Downgrade drops resend columns and indexes; data-lossy |
| Content / `forum_content` | `content_0003` | `content_0003` | No new head | Yes | Consumed-events/outbox baseline | Content event ingestion | Existing revision |
| Interactions / `forum_interactions` | `interactions_0002` | `interactions_0002` | No new head | Yes | Outbox metadata baseline | Interaction outbox | Existing revision |
| Moderation / `forum_moderation` | `moderation_0003` | `moderation_0003` | No new head | Yes | Outbox metadata baseline | Moderation outbox | Existing revision |
| Notifications / `forum_notifications` | `notifications_0004` | `notifications_0004` | **Yes — 0004 is new and untracked** | **No** | Adds nullable `sending_since` to `notifications.email_deliveries` | Worker lease recovery and `sending_since` updates | Downgrade drops the column and lease timestamps; data-lossy |
| Search / `forum_search` | `search_0002` | `search_0002` | No new head | Yes | Consumed-events/fields baseline | Search event ingestion | Existing revision |

**Verified:** the live Notification database already has `sending_since` and is at `notifications_0004`. The migration is additive and nullable. The untracked status is a source-control defect, not an unapplied live migration.

**Verified:** Compose has no migration service or startup command. Migrations are not automatically run by `docker compose up`. The staging deployment runbook's migration step is a manual host command, not Compose behavior.

### 6.2 Notification 0004 in detail

`migrations/notifications/versions/20261022_0004_email_delivery_lease.py:19-28`:

- Upgrade: `op.add_column("email_deliveries", sa.Column("sending_since", sa.DateTime(timezone=True), nullable=True), schema="notifications")`
- Downgrade: `op.drop_column(...)`

`notifications_0004` is the sole Notification head. `migrations/notifications/env.py:5-14` imports the Notification models and targets the `notifications` schema. The 0004 file is untracked; a clean checkout that lacks it cannot reproduce the live head.

The worker's `recover_stale_leases` implementation selects and updates `sending_since` (`src/services/notification/email_worker.py:262-332,380,416-419`). If the column is missing, the worker's lease queries fail. The model declares the column at `src/services/notification/models.py:110`.

### 6.3 User 0003 and model/live drift

`users_0003` is tracked and live-applied. It adds `resend_count` and `resend_available_at` and two indexes (`migrations/users/versions/20261022_0003_email_change_resend_columns.py:19-46`). Its downgrade drops those columns/indexes (`:49-61`).

**Verified drift that is not a rebuild blocker:**

1. `OneTimeToken` declares only `ck_one_time_attempts` (`src/services/user/models.py:106-108`). The live database has `ck_one_time_tokens_ck_one_time_attempts` and `ck_one_time_tokens_ck_one_time_resends`. The live constraints are stronger, but the naming/metadata is not exact.
2. `EmailChangeRequest` declares `ck_email_change_attempts` and `ck_email_change_resend_count` (`src/services/user/models.py:175-178`), but the live table has no check constraints. `users_0003` mentions attempt constraints in its docstring, but its upgrade creates only columns and indexes.
3. Only `migrations/notifications/env.py:7` imports a service model. The other five migration environments import only `Base` (`migrations/users/env.py:6`, `content/env.py:6`, `interactions/env.py:6`, `moderation/env.py:6`, `search/env.py:6`). This limits future autogenerate/metadata checks; it does not change the live heads.

Do not generate speculative migrations or apply schema “fixes” during the rebuild. These drift items are future maintenance work.

### 6.4 Recovery-token duplicate risk

**Verified:** 1 account has two unconsumed `password_recovery` tokens; 33 total recovery tokens, 3 unconsumed, 30 consumed. The restored source's `_lock_recovery_challenge` selects all unconsumed recovery tokens in descending creation order under `FOR UPDATE`, treats the newest as authoritative, and consumes older rows (`src/services/user/app.py:883-910`). The recovery request path also invalidates prior unconsumed tokens before inserting a new one (`:968-1000`).

**Report-derived:** the two tokens are expired and the account is identified in the prior 031.2.5a report. This plan does not repeat the account identifier and did not inspect token secrets or personal data.

**Decision:** runtime duplicate normalization is sufficient for the rebuild. The live rows should still be normalized under the future 031.2.5a maintenance procedure, and a partial unique index should only be added after normalization. Do not apply a unique index before the data cleanup. This is an explicit risk, not a blocker to rebuilding the source because the source handles duplicates without a 500.

### 6.5 Backup and restore procedure

#### Current tooling and its limitations

**Verified:**

- `scripts/backup/backup_databases.sh` selects exactly `forum_users`, `forum_content`, `forum_interactions`, `forum_moderation`, `forum_notifications`, and `forum_search` (`:21-28`). It runs `pg_dump -Fc` inside the `postgres` Compose service (`:37-54`) and writes a timestamped directory plus `backup_manifest.json` (`:56-67`).
- The actual script checks only that each dump is larger than 100 bytes and that the script exits zero (`:43-53`). It does **not** compute SHA-256 checksums, despite older audit text claiming checksum manifests.
- `scripts/backup/restore_databases.sh` restores all six dumps into target names with an optional prefix (`:23-30,39-65`). If the prefix is empty, the target names are the live database names.
- The restore script drops and recreates prefixed target databases (`:51-55`) and runs `pg_restore --clean --if-exists` (`:58-59`). It suppresses `pg_restore` stderr and only checks exit status.
- `tests/test_backup_restore.py` is tracked but its `EXPECTED_HEADS` still asserts `users_0002` and `notifications_0002` (`:33-39`). It cannot pass against the current live heads without an update, and it performs live backup/restore operations (`:53-82`).
- The staging runbook's rollback step calls `restore_databases.sh` without a prefix (`docs/operations/staging_deployment_runbook.md:83-86`). That would target the live database names. Do not use that command.
- Existing backup artifacts under `backups/` are tracked in Git. New backups must not go into the worktree or be staged.

#### Future backup procedure

**FUTURE — NOT RUN.**

1. **Select the target:** the six application logical databases on `forum_postgres`. Do not include the non-application `postgres` database in the rebuild backup set. Redis and Mailpit are not durable business stores and are not part of this backup.
2. **Quiesce writes:** stop Gateway first, then the six application services. Leave `postgres`, `redis`, and `mailpit` running. This stops public writes and outbox workers and gives a cross-service write boundary. The database server remains available for dumping.
3. **Use a secure destination outside the worktree:** for example, a restricted directory under `/secure/...` or another approved backup location with `umask 077`. Do not use the tracked `backups/` directory. Do not print the secret used by Compose.
4. **Run the existing backup script with the approved secret loaded in the environment**, because the script invokes `docker compose exec` and Compose interpolation requires `INTERNAL_SERVICE_SECRET`. Capture the script's timestamped directory path.
5. **Verify success independently of the script's exit code:**
   - Six `.dump` files exist and are non-empty.
   - `backup_manifest.json` has `"status": "completed"`.
   - Generate a `SHA256SUMS` file with `sha256sum` and store it with the backup.
   - Run `pg_restore --list` on each dump and capture the archive table of contents.
   - Record the live heads and representative row counts before the dump, and compare them to a fresh read-only query after the backup.
6. **Do not rely on `tests/test_backup_restore.py` as the acceptance gate** until its expected heads are updated and its checksum claims match the script. Use the manual verification above, or update the test in a separately approved change.

#### Restore rehearsal

**FUTURE — NOT RUN.**

Preferred: restore into a separate disposable PostgreSQL 16 container/cluster on a separate network, port, and volume. If that is not possible, use prefixed databases on the existing server with a unique prefix such as `rehearsal_20261023_`.

Mandatory guard:

- Refuse to run if the target prefix is empty, does not start with `rehearsal_`, or could produce any live database name.
- Query `pg_database` and assert that none of the target names equals `forum_users`, `forum_content`, `forum_interactions`, `forum_moderation`, `forum_notifications`, or `forum_search`.
- For a full recovery restore, prefer `pg_restore --single-transaction` so a failed restore rolls back rather than leaving partial objects. The current script does not use it.
- Inspect restore logs; the script's stderr suppression is not sufficient evidence.

Verify after restore without exposing personal data:

- `SELECT version_num` from each restored schema's Alembic version table; compare with the source heads.
- Verify required columns/indexes/constraints, especially `notifications.email_deliveries.sending_since`, `users.email_change_requests.resend_count/resend_available_at`, and the recovery-token indexes/constraints.
- Compare representative row counts in allowlisted tables: `users.accounts`, `users.one_time_tokens`, `users.email_change_requests`, `notifications.email_deliveries`, `content.threads`, `content.comments`, `interactions.votes`, `moderation.reports`, and `search.documents`.
- Do not select email addresses, token hashes, password hashes, session values, or message bodies.
- Drop/remove the disposable databases or container only after the evidence is captured.

#### Wrong-target prevention

- Never run `restore_databases.sh` with an empty prefix.
- Never run the staging runbook's rollback restore command.
- Require an explicit `rehearsal_` prefix and a preflight list of live database names.
- Verify `current_database()` before every destructive SQL operation.
- Keep live backups and rehearsal databases on separate retention paths.
- If the target cannot be proven to be disposable, do not restore.

### 6.6 Migration execution plan

**Current environment:** all six live heads already equal the file heads. The correct current-environment migration action is **verify only**, not `upgrade all`. Use read-only `psql` queries against the version tables.

**Do not use `scripts/migrate.py current` as a read-only verification command.** `migrations/async_env.py:44-48` executes `CREATE SCHEMA IF NOT EXISTS` inside the migration environment before running Alembic, so even a “current” invocation can perform DDL. Use direct `psql` `SELECT`s for verification.

**Fresh or isolated environment:** after target verification and a verified backup, apply the intended migrations. A one-off command in the built `user-service` image is grounded in the repository layout because the Dockerfile copies `migrations/` and `scripts/` and the Compose service has `DATABASE_*` URLs:

```text
# FUTURE — NOT RUN
docker compose run --rm --no-deps user-service python scripts/migrate.py upgrade all
```

For a narrowly scoped Notification-only application, the intended revision is `notifications_0004`. Do not use `upgrade all` against a live database whose heads are already current; it is unnecessary and increases risk.

**Required order:**

1. Verify the target database, schema, and current head with read-only SQL.
2. Take and verify the backup.
3. Apply only the intended revision(s), on a verified target.
4. Verify the resulting columns, indexes, constraints, and heads with read-only SQL.
5. Stop the application/worker rollout if any schema check fails.
6. Do not automatically downgrade. Both `notifications_0004` and `users_0003` downgrades discard columns/data; downgrade requires an explicit data-loss decision.

---

## 7. Ordered controlled rebuild and service-start plan

All steps below are **FUTURE — NOT RUN** in this task. The order minimizes downtime and prevents the old images from being recreated under the new environment contract.

### Phase 0 — Freeze and record

- Announce a change freeze for the Compose project.
- Record the starting Git HEAD, all container/image IDs, the six live heads, the live row-count baseline, and the intended backup path.
- Do not delete containers, volumes, images, or backups during the rollout.

### Phase 1 — Commit the reproducible baseline

- Stage only the required runtime, migration, test, and documentation artifacts from Section 3.3.
- Commit the atomic integration baseline.
- Confirm the new commit contains `src/common/internal_auth.py`, `notifications_0004`, and all required tests.
- Confirm a clean worktree before building. Do not build from the dirty worktree if the intent is a reproducible artifact.

### Phase 2 — Preflight configuration and image preservation

- Export or otherwise load the approved `INTERNAL_SERVICE_SECRET` without echoing it.
- Set `ENVIRONMENT` explicitly and keep `INTERNAL_SERVICE_AUTH_BYPASS=false` for the rebuild.
- Run `docker compose config --quiet`; it must succeed.
- Verify no secret value is printed by any command.
- Record and tag all six current application images as described in Section 4.3.
- Verify the User recovery tag and export checksum.
- Confirm `forum_postgres`, `forum_redis`, and `forum_mailpit` are healthy and unchanged.

### Phase 3 — Build while the old stack still runs

```text
# FUTURE — NOT RUN
docker compose build user-service content-service interaction-service moderation-service notification-service search-service
```

- Do not rebuild Gateway, PostgreSQL, Redis, or Mailpit.
- Record the six new full image IDs and their tags.
- If any build fails, stop. The old containers remain running and untouched.
- Do not run `docker compose up` until the image IDs are verified.

### Phase 4 — Quiesce, back up, and verify

- Stop Gateway first, then the six application services. Do not stop the database.
- Confirm no application container is writing.
- Run the backup procedure in Section 6.5 into a restricted path outside the worktree.
- Compute and record checksums, archive listings, the manifest, heads, and row-count baselines.
- If the backup is not verified, restart the old stack and abort the rebuild.

### Phase 5 — Migration verification or application

- On the current live databases: verify only; do not run an upgrade.
- On a fresh/rehearsal environment: apply the intended migrations only after target verification and backup.
- Verify heads, columns, indexes, and constraints with read-only SQL.
- If verification fails, do not start the new application images. Follow the migration rollback branch in Section 8.

### Phase 6 — Start the new application services

```text
# FUTURE — NOT RUN
docker compose up -d --no-deps --no-build user-service
# Wait for forum_user_service to report healthy and for startup logs to be clean.
docker compose up -d --no-deps --no-build content-service interaction-service moderation-service search-service
# Wait for all four to report healthy.
docker compose up -d --no-deps --no-build notification-service
# Wait for healthy; verify the worker started with the schema present.
docker compose up -d --no-deps --no-build gateway
```

- `--no-deps` prevents unnecessary recreation of PostgreSQL, Redis, and Mailpit.
- `--no-build` ensures the already verified images are used.
- Do not use `--force-recreate` on the infrastructure services.
- Do not start Gateway until the six services pass health and internal-auth smoke checks.

### Phase 7 — Health and smoke validation

- `docker compose ps`; all ten containers must be running/healthy.
- `docker inspect --format '{{.State.Health.Status}}'` for each application container.
- Service liveness: `GET /health/live` on ports 8001–8006.
- Gateway readiness: `GET http://localhost:8000/health/ready`.
- Gateway metrics: `GET http://localhost:8000/metrics`.
- Database: `pg_isready`; read-only head queries; schema verification.
- Mailpit: health/UI reachability; do not send test mail until the stack is healthy.
- Check logs for import errors, missing `internal_auth`, missing `sending_since`, and auth failures.

### Phase 8 — Controlled smoke tests

Run the non-mutating checks first, then the disposable-data tests in Section 9. Do not run the full PostgreSQL stress suite, Mailpit failure tests, or browser E2E in the same window without a separate approval.

### Phase 9 — Accept or roll back

- If all health checks and required smoke tests pass, declare the rebuild successful and record the new image IDs, heads, and evidence.
- If any service fails, stop before opening further traffic and follow Section 8.
- Do not accept “container is healthy” as sufficient if internal auth or cross-service calls fail.

---

## 8. Smoke-test and integration matrix

All tests below are **FUTURE — NOT RUN**. “Mutates” indicates whether the test writes application state. Tests marked “disposable” must use a dedicated disposable database or an approved test account, never the live duplicate-token account.

### 8.1 Internal authentication

| ID | Component | Preconditions | Action | Expected result | Evidence to capture | Mutates? | Cleanup |
|---|---|---|---|---|---|---|---|
| IA-1 | Compose secret guard | No `INTERNAL_SERVICE_SECRET` in environment | `docker compose config --quiet` | Exit `1`; six missing-variable errors; no container action | Exit code and stderr | No | None |
| IA-2 | User internal receiver | Rebuilt User service; bypass false | `GET /internal/users/<disposable-id>` with no internal header | `401`, “Internal service key missing.” | Status/body | No | None |
| IA-3 | User internal receiver | Rebuilt User service; bypass false | Same request with an incorrect secret and with the legacy header | `401`, invalid key; legacy header not accepted | Status/body | No | None |
| IA-4 | User internal receiver | Disposable account exists; correct secret loaded without argv leakage | `GET /internal/users/<disposable-id>` with canonical header | `200`; response contains the disposable account's contact fields (id, username, display name, email, role, ban flag) | Status and field names only; do not log the email/body; no secret in logs | No (read-only GET) | Delete disposable account after all user tests |
| IA-5 | Settings validation | In-process settings or approved config test | Construct staging/production settings with bypass enabled | `ValidationError`; bypass rejected | Exception type/location, no secret values | No | None |
| IA-6 | Public auth | Disposable account | `GET /auth/csrf`; login; `GET /auth/session`; logout | CSRF `200`; login `200`; session `200`; logout `204`; subsequent session `401` | Cookie presence (not value), statuses | Yes | Logout/delete account; reset rate limiters |
| IA-7 | Development/test hooks | `ENVIRONMENT=development` | Verify `/test/otp` and `/test/reset-rate-limiters` behavior; do not use in staging or production | `404` in production; intended only for development/test; **staging is not currently blocked by the route guard** | Status/body | Depends | None |

Do not put the secret in a command line that appears in shell history or process lists. Use an approved secret-injection mechanism.

### 8.2 User Service

| ID | Component | Preconditions | Action | Expected result | Evidence to capture | Mutates? | Cleanup |
|---|---|---|---|---|---|---|---|
| U-1 | Registration + OTP | Rebuilt User + Mailpit; approved test address; development/test environment | `POST /auth/register`; retrieve latest mail from Mailpit; `POST /auth/register/verify` | Register `202`; verify `201`; account created | Status/body, Mailpit message ID, account count; never print OTP in report | Yes | Delete disposable pending/account rows; delete Mailpit message |
| U-2 | Login/session/logout | Account from U-1 | `POST /auth/login`; `GET /auth/session`; `POST /auth/logout`; repeat session check | `200`; session `200`; logout `204`; subsequent `401` | Statuses, cookie name only | Yes | Logout; delete account |
| U-3 | Recovery request/verify/reset | Disposable account; Mailpit | `POST /auth/recovery/request`; fetch mail; verify OTP; reset password; replay reset | Request `202`; verify `200` with reset token; reset `204`; replay rejected `400`; old sessions invalidated | Statuses/error codes, token row counts, not token values | Yes | Delete disposable account/tokens; Mailpit cleanup |
| U-4 | Email change request/resend/verify | Disposable account; Mailpit; target address approved or disposable | Request change; resend; verify; test taken-email branch | Request `202`; resend `200`; verify `200`; taken email neutral `202` with no active request | Status/body, active request count, Mailpit message IDs | Yes | Delete disposable account/requests; Mailpit cleanup |
| U-5 | Avatar upload/claim/replace/delete | Disposable account; test image bytes; Content service available | Upload to `/uploads/images`; `PUT /profile/avatar`; fetch public image; upload/replace; `DELETE /profile/avatar`; fetch profile | Upload `201`; set `200` with `/uploads/images/<id>`; public fetch `200`; old attachment deleted; delete `204`; profile avatar null | Statuses, attachment statuses, profile avatar value, storage cleanup | Yes | Delete test attachments/object files and account |
| U-6 | Unique/lifecycle constraints | Disposable accounts | Attempt duplicate email/username registration; concurrent email-change verify; 5 failed OTP attempts | Neutral responses where required; one concurrent verify `200` and one `400`; attempts exhausted returns expected error | Status/error codes; counts; no PII | Yes | Delete disposable rows |

### 8.3 Notification Service

| ID | Component | Preconditions | Action | Expected result | Evidence to capture | Mutates? | Cleanup |
|---|---|---|---|---|---|---|---|
| N-1 | Worker startup/schema | Rebuilt Notification; `sending_since` present | Start service; check `/health/ready`; inspect startup logs | Worker starts; no missing-column/import errors | Health, logs, schema column query | No | None |
| N-2 | SMTP/Mailpit delivery | Mailpit healthy; approved recipient; test delivery row or registration event | Trigger one email; inspect Mailpit and DB | Mailpit receives exactly one message; delivery row becomes `delivered`; `sending_since` cleared | Mailpit message ID, DB status/attempts, logs | Yes | Delete test delivery/Mailpit message |
| N-3 | Temporary SMTP failure/retry | **Disposable DB/test harness**, not live | Inject a temporary SMTP failure; wait for retry schedule | Attempts increment; `next_attempt_at` advances; eventual success or bounded terminal state | Attempts, next-attempt, error summary, no secret values | Yes (disposable) | Drop disposable DB/container |
| N-4 | Termination while `sending` / stale lease | **Disposable DB/test harness**; short test lease timeout | Seed/set `sending_since`; terminate worker task; restart/recover | Stale row recovered; no stuck `sending`; attempts bounded | `sending_since`, attempts, status, recovery duration | Yes (disposable) | Drop disposable DB/container |
| N-5 | Competing workers | **Disposable DB/test harness**; two workers | Submit one pending delivery to both workers | Exactly one claim; no double delivery; attempts exactly 1 in normal path | Worker IDs, attempts, SMTP call count | Yes (disposable) | Drop disposable DB/container |
| N-6 | Terminal failure | **Disposable DB/test harness**; max attempts | Fail repeatedly until limit; then attempt recovery | `dead_letter` terminal; recovery does not reclaim; attempts do not exceed max | Status, attempts, recovery count | Yes (disposable) | Drop disposable DB/container |

Do not stop or kill the live Notification container to simulate failure. Use a disposable worker/process and database for N-3 through N-6. A five-minute live lease timeout is not an appropriate smoke test.

### 8.4 Cross-service and persistence

| ID | Component | Preconditions | Action | Expected result | Evidence to capture | Mutates? | Cleanup |
|---|---|---|---|---|---|---|---|
| X-1 | User → Content avatar claim/release | Rebuilt User + Content; disposable account/image | Avatar flow from U-5; call claim/release with correct and incorrect internal credentials | Correct claim/release succeeds; missing/incorrect credentials `401`; no development bypass | Statuses, attachment statuses, Content logs | Yes | Delete test attachment/object/account |
| X-2 | Notification → User internal API | Rebuilt Notification + User; disposable recipient | Trigger a notification that resolves recipient contact | Notification receives `200`; email queued/delivered; no `404`/`401` from internal lookup | Notification logs, delivery status, internal endpoint status | Yes | Delete test delivery/account; Mailpit cleanup |
| X-3 | Recovery duplicate behavior | **Disposable account/DB** with 2–3 unconsumed legacy recovery tokens | Request/verify recovery; inspect active rows | No `500`; newest row authoritative; older rows consumed; at most one active token at end | Counts and row state, not token values | Yes (disposable) | Delete disposable rows/account |
| X-4 | Email uniqueness/lifecycle | Disposable accounts | Concurrent email-change verify; duplicate target; attempt lockout | One `200`, one `400`; duplicate target neutral `202` with no active request; lockout enforced | Statuses/error codes, active counts | Yes | Delete disposable rows |
| X-5 | Persistence after restart | Rebuilt stack | Restart one application service (not infrastructure); re-check session/avatar/delivery state | State persists in PostgreSQL; service reconnects; no duplicate delivery | Health, row state, logs | Yes | Remove disposable state |

**Test-data boundary:** use new usernames and disposable accounts. Do not use or mutate the existing live account with duplicate recovery tokens. Because Mailpit's Compose allowlist currently accepts only the configured fixed test recipients (`docker-compose.yml:46,230`), unique plus-addresses require an approved temporary allowlist change in the execution window; otherwise use the fixed permitted addresses and clean up all test data. Do not leave test accounts, delivery rows, attachments, or Mailpit messages behind.

---

## 9. Rollback decision tree

### 9.1 Universal first actions

1. Stop further rollout; do not start Gateway or additional services.
2. Preserve logs and diagnostics:
   - `docker compose logs --no-color` output to a restricted evidence path.
   - container/image IDs, health history, restart counts, and the failing response.
   - current schema heads, columns, and row counts.
3. Do not delete containers, volumes, images, or backups.
4. Record the exact image ID and migration head at the failure point.
5. Assign a decision owner before taking any destructive action.

### 9.2 Branch A — Application-only failure, schema unchanged

**Observable triggers:** new image fails to start, health never becomes healthy, repeated 5xx, internal-auth 401s, missing endpoints, cross-service calls fail, but migration heads and schema are unchanged.

| Item | Procedure |
|---|---|
| Stop condition | Stop further service recreation; leave failed container logs intact |
| Rollback action | Recreate only the affected service(s) from the recorded pre-rebuild immutable image tag or image ID, using an approved Compose override or documented retag procedure; do not rebuild |
| Health verification | Container healthy; `/health/live`; internal-auth `401` without header and expected result with correct header; one cross-service call |
| Evidence required | Failing image ID, preserved image ID, logs, health, smoke result, post-rollback image ID |
| Decision owner | Release operator with reviewer |
| Escalation | If rollback image also fails, treat as Branch C or D |

**Important:** a preserved User image is not a substitute for the pre-restoration User behavior if the old image is itself the stale image. Rollback restores the prior environment, not necessarily the desired feature set. Record exactly which image was restored.

### 9.3 Branch B — Migration failure before commit

**Observable triggers:** Alembic returns an error; transaction is expected to roll back; head remains at the prior revision; expected column/index is absent.

| Item | Procedure |
|---|---|
| Stop condition | Do not start the new application images or retry blindly |
| Inspection | Read the migration version table, `information_schema.columns`, indexes, constraints, PostgreSQL logs, and the Alembic error; confirm whether the transaction rolled back |
| Retry | Only after correcting the cause and re-verifying the backup and target. Do not issue an automatic rerun |
| Evidence required | Failing command, transaction outcome, head before/after, schema diff, database log excerpt |
| Decision owner | Database owner with release operator |
| Escalation | If partial DDL is present despite the error, treat as Branch C; do not assume rollback |

`notifications_0004` adds a nullable column; on PostgreSQL it is transactional, so a clean failure should leave the head and schema unchanged. Verify rather than assume.

### 9.4 Branch C — Failure after a successful schema migration

**Observable triggers:** new migration head is present, schema change is committed, but the new application/worker fails health or smoke checks.

| Item | Procedure |
|---|---|
| First question | Is the previous application version compatible with the new schema? |
| Known compatibility | `notifications_0004` is additive and nullable; an older Notification worker can ignore the column, but it loses lease recovery. `users_0003` is additive; the older User code may tolerate the new columns, but it does not have the restored handlers. |
| Preferred action | Prefer a forward fix or a compatible old-image rollback. Do **not** automatically downgrade migrations |
| Downgrade | Only with explicit data-loss approval; both 0004 and 0003 downgrades drop columns/data |
| Database restore | Only to an explicitly verified backup/target; do not overwrite live databases in place without the rollback approval |
| Evidence required | Head, schema, old/new image compatibility check, error logs, data-loss assessment |
| Decision owner | Release owner and database owner; incident commander if data is at risk |

**Image rollback alone cannot undo a database schema change.** Conversely, restoring a database does not fix an application defect. State both facts in the incident record.

### 9.5 Branch D — Data corruption or failed restore

**Observable triggers:** wrong-target restore, integrity mismatch, duplicate delivery corruption, row-count/head mismatch, truncation, or a failed isolated restore.

| Item | Procedure |
|---|---|
| Immediate stop | Stop writes to the affected environment; do not “repair” live data in place |
| Preserve | Preserve the failed state, logs, backup checksums, and target database names |
| Restore target | Restore only to an explicitly verified disposable target first; validate heads, columns, constraints, and representative counts |
| Cutover | Reopen traffic only after the restored target passes integrity checks and the application compatibility check |
| Evidence required | Backup checksum, restore log, `pg_restore` listing, target identity, heads, counts, smoke results |
| Decision owner | Incident commander and database owner |
| Prohibited | Restoring over live names, using the runbook command without a prefix, or claiming image rollback repairs data |

### 9.6 Decision summary

| Failure class | Application rollback | Database rollback | Automatic downgrade? |
|---|---|---|---|
| Application-only, schema unchanged | Yes, from preserved image | No | No |
| Migration failed before commit | Only if needed; old images | No; verify rollback | No |
| Migration committed, app failed | Compatible old image or forward fix | Only by explicit decision/restore | **No** |
| Data corruption / failed restore | Stop and preserve | Restore to verified isolated target | **No** |

---

## 10. Future 15–30 minute PostgreSQL stress-test plan

**FUTURE — NOT RUN.** This plan must not be executed in Task 031.2.6.

### 10.1 Isolation requirements

- Use a dedicated disposable PostgreSQL 16 instance/container with its own network, port, volume, and credentials. Do not use `forum_postgres`, `forum_notifications`, or any live development database.
- Create two disposable databases, for example `stress_users_<timestamp>` and `stress_notifications_<timestamp>`, or one dedicated instance with clearly non-live names. Never use a name beginning with `forum_`.
- Apply the intended migrations to the disposable databases before the run.
- Set test URLs through `TEST_USERS_DATABASE_URL` and `TEST_NOTIFICATIONS_DATABASE_URL` (or an equivalent harness). The existing lease test already refuses the live Notification database by name (`tests/test_email_worker_lease_pg.py:34-49`); the stress harness must be stricter and reject every live database name and verify the server identity/port.
- If a dedicated disposable database cannot be guaranteed, **do not run the stress test**.

### 10.2 Bounded run model

| Phase | Duration | Load / behavior |
|---|---|---|
| Warm-up | 2 minutes | Low-rate requests and one worker; verify connections and migrations |
| Recovery-token concurrency | 6 minutes | 8 client tasks, 20 requests/second cap, fixed total request count; synthetic accounts with 10–20% seeded duplicate legacy tokens |
| Email lease/worker concurrency | 6 minutes | 4 workers, batch size 20, 50–200 seeded deliveries, short test lease timeout (5–10s), bounded retries |
| Crash/recovery | 4 minutes | Terminate a disposable worker task while a row is `sending`; restart and observe lease recovery |
| Drain and verify | 2 minutes | Stop load, wait for workers, verify invariants and final states |

Total: about 20 minutes, within the 15–30 minute requirement. Use fixed request counts and timeouts, semaphores, and `asyncio.gather` with a bounded task set. No unbounded loops or task creation.

### 10.3 Load limits

- Maximum 8 concurrent recovery clients and 4 email workers.
- Maximum 20 database connections for the test harness; PostgreSQL `max_connections` stays below 80% utilization.
- Maximum 20 recovery requests/second; fixed total of about 2,000–5,000 requests.
- Maximum 200 seeded deliveries; each with bounded `max_attempts`.
- Test lease timeout 5–10 seconds, not the production default 300 seconds.
- SMTP is mocked or points to a disposable sink; do not use the live Mailpit allowlist for stress traffic.

### 10.4 Scenarios

1. Concurrent recovery requests for the same account: no 500; at most one authoritative active token after normalization.
2. Legacy duplicate recovery rows: newest row authoritative; older unconsumed rows consumed; no duplicate active group remains.
3. Competing email workers: one pending row; exactly one claim; no duplicate SMTP delivery in the normal path.
4. Worker termination while `sending`: stale lease recovered after timeout; attempts bounded; no stuck `sending`.
5. Temporary SMTP failure: retry/backoff, attempts increment, eventual terminal or delivered state.
6. Terminal failure: `dead_letter` reached at max attempts; recovery does not reclaim terminal rows.
7. Lock contention: monitor deadlocks and lock waits under concurrent token and delivery work.

### 10.5 Metrics and invariants

Capture at least:

- Throughput (requests/second, deliveries/minute).
- Latency p50/p95/p99 for recovery requests and worker batches.
- Error rates (4xx/5xx, SMTP failures, database errors).
- Deadlocks and lock-wait durations.
- Active database connections and pool saturation.
- Duplicate claims/duplicate deliveries.
- Lease recovery count and time-to-recovery.
- Final delivery state counts and recovery-token active counts.
- Periodic samples every 10 seconds, plus a final summary.

Invariants after the run:

- No account has more than one unconsumed recovery token.
- No duplicate `(event_id, recipient_id)` delivery row.
- Delivery statuses are in the allowed set; `attempts <= max_attempts`.
- No `sending` row remains after the recovery window.
- `dead_letter` is terminal and is not reclaimed.
- Live database heads and row counts are unchanged.
- No live database received a connection from the stress harness.

### 10.6 Abort conditions

Abort immediately if:

- Active connections exceed 80% of the configured limit, or grow without bound.
- Error rate exceeds 5% for 60 seconds.
- Deadlocks exceed a pre-agreed threshold (for example, more than 5 per minute).
- p99 latency exceeds 5 seconds for 2 minutes.
- Any connection targets a live database, or any cross-database access is observed.
- Any secret, personal data, or message body appears in logs/artifacts.
- Disk, memory, or container limits are approached.
- The disposable target cannot be proven isolated.

### 10.7 Cleanup and post-test integrity

- Stop load and workers; wait for in-flight tasks.
- Export the metrics summary and error samples before teardown.
- Drop the disposable databases and remove the disposable container/volume.
- Re-check the live database heads and row counts to prove no cross-target mutation.
- Delete test accounts, tokens, deliveries, messages, and temporary logs according to the approved retention policy.
- Do not use the live duplicate-token account as test data.

---

## 11. Exact blockers, prerequisites, and deferred checks

### 11.1 Hard execution gates

The next execution task must not begin the rebuild until all of these are satisfied. If any cannot be satisfied, change the verdict to `BLOCKED` and stop.

| Gate | Requirement | Why it is a gate | Verification |
|---|---|---|---|
| G1 | Commit the required worktree artifacts (Section 3.3) and build from that commit | Clean HEAD omits required source/migration/test files and older code | `git status` clean; clean worktree contains the five required untracked-then-tracked files |
| G2 | Preserve all six current application images with immutable tags/recorded IDs | Only User has a recovery tag; rollback inventory is otherwise incomplete | Tag resolves to recorded full image ID for every service |
| G3 | Set `INTERNAL_SERVICE_SECRET` and explicit `ENVIRONMENT`; keep bypass false | Compose fails without secret; old images do not read the new contract | `docker compose config --quiet` succeeds; no secret printed |
| G4 | Produce a verified six-database backup with external checksums | Current script lacks checksums; backup is the restore point | Six dumps, manifest, `SHA256SUMS`, `pg_restore --list`, heads/counts recorded |
| G5 | Use a guarded isolated restore path | The unguarded restore script can target live database names | Prefix `rehearsal_*`; live-name preflight; disposable target only |
| G6 | Rebuild all six application services, not a subset | Shared config/outbox/auth and the env rename make partial rebuilds unsafe | New image IDs verified for all six before recreate |
| G7 | Treat current live migration state as verify-only | All live heads already equal file heads; unnecessary upgrades add risk | Read-only head/column verification; no `upgrade all` on live |
| G8 | Do not use `scripts/migrate.py current` as read-only | Its environment executes `CREATE SCHEMA IF NOT EXISTS` | Use direct `psql` SELECTs for verification |
| G9 | Use disposable data/addresses and an approved cleanup boundary | Smoke tests otherwise mutate live accounts or leave test data | Test accounts/rows/messages cleaned; live duplicate account untouched |
| G10 | Do not use the staging runbook's unprefixed restore command | It can overwrite live databases with `--clean` | Only guarded, prefixed restore is allowed |

### 11.2 Open risks accepted by this plan

| Risk | Current state | Treatment |
|---|---|---|
| Live recovery-token duplicates | 1 account, 2 unconsumed tokens; source normalizes at runtime | Rebuild is safe; normalize before adding a partial unique index; do not touch the account in smoke tests |
| `OneTimeToken`/`EmailChangeRequest` model-vs-live drift | Live constraints differ; no migration planned | Track for future maintenance; do not auto-generate migrations during rebuild |
| Notification 0004 untracked but live-applied | Live head is ahead of HEAD; file is needed for reproducibility | Commit 0004 with migration env and lease tests; current live needs verify only |
| Backup script lacks checksums | Actual script checks size/exit only | Generate external checksums and archive listings in execution; update script later |
| Backup/restore test expected heads are stale | Test asserts `users_0002`/`notifications_0002` | Do not use the test as a gate until updated; use manual verification |
| Staging runbook rollback is unsafe | Calls restore without prefix | Do not follow; use guarded isolated restore and explicit approval |
| Existing `backups/` are tracked | Repository contains backup artifacts | Do not add new backups to Git; review existing artifacts in a separate security task |
| Mailpit allowlist limits unique addresses | Only fixed test recipients are allowed | Use approved fixed addresses or temporary approved allowlist expansion; clean up |
| `ENVIRONMENT` defaults to development | Missing value weakens staging/production validation | Set explicitly for the target environment; bypass remains false |
| Development/test hooks are reachable in staging | `/test/otp` and `/test/reset-rate-limiters` return `404` only when `environment == "production"` (`src/services/user/app.py:175-203`); an explicit staging environment still exposes them | Do not deploy to staging until a separate narrowly scoped fix restricts these hooks to development/test; local development rebuild is unaffected |
| Gateway image not rebuilt | Gateway source unchanged, but image is older | Accept for this change; rebuild only if a separate gateway change is introduced |

### 11.3 Deferred runtime checks

These are intentionally not performed in Task 031.2.6:

- Rebuild, build, tag, export, load, or delete any image.
- Start, stop, restart, recreate, or remove any container.
- Apply, upgrade, downgrade, stamp, or mutate any migration.
- Run a backup or restore.
- Run PostgreSQL stress/concurrency tests.
- Run Mailpit delivery/failure tests.
- Run browser E2E tests.
- Run the existing live backup/restore test.
- Validate the rebuilt stack's health, internal auth, SMTP delivery, or cross-service calls.
- Update stale test heads, backup checksums, or runbooks.

---

## 12. Safety confirmation

**Confirmed for Task 031.2.6:**

- No source, test, migration, Compose, `.env`, `.env.staging`, or runtime configuration file was modified by this task.
- No image was built, rebuilt, pulled, loaded, retagged, exported, imported, or deleted.
- No container was started, stopped, restarted, recreated, or removed.
- No migration was applied, upgraded, downgraded, or stamped.
- No database row or schema was modified. All database inspection used `BEGIN READ ONLY` transactions or read-only `information_schema`/`pg_catalog` queries.
- No backup, restore, Mailpit delivery test, browser E2E, PostgreSQL stress test, or concurrency test was run.
- No work was staged, committed, stashed, reset, cleaned, or discarded.
- All existing tracked modifications and untracked audit/source artifacts were preserved.
- No secret, password, token, session value, or internal credential was printed in this report or in the evidence commands. Test-only fixture values were not reproduced.
- Exactly one new tracked/deliverable file was added by this task: this report. The read-only in-process settings validation may have refreshed ignored Python bytecode caches; no source file or tracked file was added or modified.

The only Docker operations performed were read-only metadata inspection (`docker ps`, `docker inspect`, `docker images`) and read-only greps of files inside running containers. The only PostgreSQL operations were read-only queries in `BEGIN READ ONLY` transactions. The in-process settings validation did not connect to a database.

---

## Appendix A — Evidence index

### A.1 Initial `git status --short`

This was captured before this report was added.

```text
 M .env.example
 M .env.staging.example
 M docker-compose.yml
 M migrations/notifications/env.py
 M scripts/run_benchmarks.py
 M src/common/config.py
 M src/common/outbox_worker.py
 M src/services/content/auth.py
 M src/services/notification/auth.py
 M src/services/notification/config.py
 M src/services/notification/email_worker.py
 M src/services/notification/models.py
 M src/services/search/auth.py
 M src/services/user/app.py
 M src/services/user/auth.py
 M src/services/user/schemas.py
 M tests/test_e2e_integration.py
 M tests/test_email_dispatch_worker.py
 M tests/test_notification_service.py
 M tests/test_outbox_concurrency_live.py
 M tests/test_outbox_hardening.py
 M tests/test_outbox_worker.py
 M tests/test_search_service.py
 M tests/test_staging_operational_readiness.py
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
?? docs/audit/task_031_2_4_integration_preflight_report.md
?? docs/audit/task_031_2_5a_recovery_token_integrity_report.md
?? docs/audit/task_031_2_5b_migration_chain_and_checkout_report.md
?? docs/audit/task_031_2_5c_internal_service_auth_report.md
?? migrations/notifications/versions/20261022_0004_email_delivery_lease.py
?? src/common/internal_auth.py
?? tests/conftest.py
?? tests/test_email_worker_lease_pg.py
?? tests/test_internal_service_auth.py
```

### A.2 Primary source and configuration references

| Topic | File/lines |
|---|---|
| Compose service topology, dependencies, healthchecks, ports | `docker-compose.yml:1-325` |
| Required secret interpolation | `docker-compose.yml:86,121,156,189,222,265` |
| Environment default and bypass default | `docker-compose.yml:85-87,120-122,155-157,188-190,221-223,264-266` |
| Mailpit allowlist | `docker-compose.yml:46,230` |
| Shared internal header and verification | `src/common/internal_auth.py:8,18-45` |
| Settings secret/bypass validation | `src/common/config.py:18,30-40,81-99,101-137` |
| Outbox canonical header | `src/common/outbox_worker.py:29,79-93,176` |
| User recovery duplicate helper | `src/services/user/app.py:883-910,968-1000` |
| User canonical internal calls | `src/services/user/app.py:753,863` |
| User internal contact endpoint | `src/services/user/app.py:1920-1945` |
| User email-change schema | `src/services/user/schemas.py:104-106` |
| User token/model drift | `src/services/user/models.py:106-108,175-178` |
| Notification lease recovery | `src/services/notification/email_worker.py:262-332,380,416-419` |
| Notification `sending_since` model | `src/services/notification/models.py:110` |
| Notification 0004 migration | `migrations/notifications/versions/20261022_0004_email_delivery_lease.py:19-28` |
| Notification migration model import | `migrations/notifications/env.py:5-14` |
| Alembic schema creation side effect | `migrations/async_env.py:44-48` |
| Migration CLI | `scripts/migrate.py:21-64` |
| Dockerfile build context | `Dockerfile:15-20` |
| Backup script | `scripts/backup/backup_databases.sh:21-28,37-67` |
| Restore script | `scripts/backup/restore_databases.sh:23-30,39-65` |
| Stale backup test heads | `tests/test_backup_restore.py:33-39` |
| Unsafe runbook restore | `docs/operations/staging_deployment_runbook.md:76-90` |
| Lease test live-DB guard | `tests/test_email_worker_lease_pg.py:34-49` |

### A.3 Prior reports used as supporting evidence

- `docs/audit/task_031_2_4_integration_preflight_report.md:13-23` — prior `READY WITH EXPLICIT RISKS` verdict and main risks.
- `docs/audit/task_031_2_4_integration_preflight_report.md:110-129` — stale User image evidence.
- `docs/audit/task_031_2_5a_recovery_token_integrity_report.md:256-...` — deferred recovery-token normalization and index work.
- `docs/audit/task_031_2_5b_migration_chain_and_checkout_report.md` — migration-chain and checkout findings.
- `docs/audit/task_031_2_5c_internal_service_auth_report.md` — canonical secret/header design and prior validation.
- `docs/audit/task_031_2_3a_image_preservation_report.md:25-50` — User recovery tag/export evidence.
- `docs/audit/task_031_1_design.md:26-120` — lease design and PostgreSQL test design.
- `docs/audit/task_031_2_3c_recovery_restoration_report.md`, `031_2_3d`, `031_2_3e`, `031_2_3f` — restored User handler and schema maps.

### A.4 Read-only verification commands used in this task

- `git branch --show-current`, `git rev-parse HEAD`, `git status --short`, `git diff --cached --name-status`, `git ls-files --others --exclude-standard`, `git ls-files backups`, `git cat-file -e HEAD:<path>`, `git show HEAD:<path>`.
- `docker ps --no-trunc`, `docker inspect` with selected fields only, `docker images --no-trunc --digests`, `docker image inspect` for the preserved User images.
- `docker exec <container> sh -lc 'grep ...'` for read-only source inspection inside containers.
- `docker compose config --format json` piped through a parser that printed service topology and environment **keys only**, never values; `env -u INTERNAL_SERVICE_SECRET docker compose config --quiet` to verify fail-fast behavior.
- Read-only `psql` queries with `BEGIN READ ONLY` against the six application databases for version tables, columns, indexes, constraints, and aggregate counts.
- In-process `BaseServiceSettings` validation checks with no database connection, asserting rejection of empty secrets and staging/production bypass; no secret values were printed.
- AST parsing of migration files plus `git ls-files` to build the revision graph and tracked/untracked map.
- Reading the backup/restore scripts, Dockerfile, Compose file, migration environments, and selected source/test files.

No command that mutates runtime, images, containers, migrations, database state, or Git state was executed. The planned commands in this report are explicitly labeled **FUTURE — NOT RUN**.
