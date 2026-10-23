# Task 031.2.11 — Rebuild Checkpoint and Artifact Integrity Audit Report

**Date:** 2026-10-23
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`
**Mode:** Read-only Git, worktree, artifact, Docker-metadata, configuration, and backup/restore inspection
**Checkpoint boundary:** `bf3384c4c3b53b5f826ef119f2ad549997ff1d63`
**Verdict:** **BLOCKED — REQUIRED EVIDENCE OR ARTIFACTS MISSING**

---

## 1. Executive Summary

The current worktree is not a reproducible checkpoint. `HEAD` is still exactly the previously recorded baseline `bf3384c4c3b53b5f826ef119f2ad549997ff1d63`; there are zero commits after it, no local tags, and no staged changes. All Task 031.1 and Task 031.2 work is still uncommitted: 34 tracked modifications and 27 untracked entries.

Several required artifacts are absent from `HEAD`, including the shared internal-auth module and the Notification `notifications_0004` migration. A clean checkout of the current commit therefore cannot build or test the current source tree.

The image-preservation gate also fails. The seven running application images have known local image IDs, but only the User Service has a dedicated recovery tag and a verified export. The other five application services and the Gateway have only mutable `latest` references. No image has an OCI source-revision label.

The live authentication configuration does not match the current source contract. All six application containers have the legacy `INTERNAL_SERVICE_KEY`; none has `INTERNAL_SERVICE_SECRET`, and none has an explicit `ENVIRONMENT` or bypass setting. The current source and Compose file require `INTERNAL_SERVICE_SECRET` and use `X-Internal-Service-Key`. A partial or unplanned rebuild would create a mixed-version stack and break internal service calls.

The backup/restore gate also fails. The hardened scripts and their offline tests are present, but there is no fresh checksummed backup in the current format, the two existing tracked backup sets have no `SHA256SUMS`, and no isolated restore rehearsal or application-level recovery test has been completed.

This inventory is sufficient to name the remediation workstreams. It is not sufficient to declare the checkpoint ready. The final verdict is **BLOCKED**, and it does not authorize a rebuild or the start of Task 031.2.12.

---

## 2. Git and Worktree Inventory

### 2.1 Checkpoint references

| Item | Observed value | Interpretation |
|---|---|---|
| Working directory | `/home/chocolat/Documents/Project/ForumPlatform` | Repository root |
| Branch | `master` | Current branch |
| `HEAD` | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` | Current commit |
| Recorded baseline | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` | Identical to `HEAD` |
| Commits after baseline | `0` | No checkpoint commit exists for the work |
| Local tags | None | No tag preserves the current source state |
| Stashes | None | No stashed worktree checkpoint |
| Worktrees | One, at the repository root | No alternate preserved worktree |
| Staged changes | None; `git diff --cached` is empty | Nothing is prepared for commit |
| Local branch refs | `master` only | No alternate local branch checkpoint |
| Local remote-tracking ref | `origin/master` points to `bf3384c` | Remote state was not fetched or verified |

The remote-tracking ref is reported only as local metadata. No fetch, pull, checkout, reset, stash, clean, stage, commit, push, or branch switch was performed.

### 2.2 Worktree counts

| Category | Count |
|---|---:|
| Tracked modifications | 34 |
| Untracked entries | 27 |
| Total worktree entries | 61 |
| Deleted tracked paths | 0 |
| Staged paths | 0 |
| Unmerged/conflicted paths | 0 |

The untracked total is relative to standard Git exclusion rules. It does not include ignored or tracked backup files.

### 2.3 Tracked modifications

The 34 modified tracked files are:

```text
.env.example
.env.staging.example
docker-compose.yml
docs/operations/backup_restore_runbook.md
docs/operations/incident_recovery_runbook.md
docs/operations/staging_deployment_runbook.md
migrations/notifications/env.py
scripts/backup/backup_databases.sh
scripts/backup/restore_databases.sh
scripts/run_benchmarks.py
src/common/config.py
src/common/outbox_worker.py
src/services/content/auth.py
src/services/notification/auth.py
src/services/notification/config.py
src/services/notification/email_worker.py
src/services/notification/models.py
src/services/search/auth.py
src/services/user/app.py
src/services/user/auth.py
src/services/user/config.py
src/services/user/schemas.py
tests/test_backup_restore.py
tests/test_e2e_integration.py
tests/test_email_dispatch_worker.py
tests/test_gateway.py
tests/test_notification_service.py
tests/test_outbox_concurrency_live.py
tests/test_outbox_hardening.py
tests/test_outbox_worker.py
tests/test_search_service.py
tests/test_security_hardening.py
tests/test_staging_operational_readiness.py
tests/test_user_auth_lifecycle.py
```

No tracked path was deleted. No file mode or rename change was reported by `git diff --summary`.

### 2.4 Untracked entries

The 27 pre-existing untracked entries are:

```text
docs/api/forum_api_endpoint_reference.md
docs/api/forum_api_workflow_guide.md
docs/audit/task_031_1_change_log.md
docs/audit/task_031_1_design.md
docs/audit/task_031_1_validation_report.md
docs/audit/task_031_2_10_internal_auth_deployment_readiness_report.md
docs/audit/task_031_2_1_inventory_report.md
docs/audit/task_031_2_2_discrepancy_report.md
docs/audit/task_031_2_3a_image_preservation_report.md
docs/audit/task_031_2_3b_restoration_map.md
docs/audit/task_031_2_3c_recovery_restoration_report.md
docs/audit/task_031_2_3d_email_change_restoration_report.md
docs/audit/task_031_2_3e_avatar_restoration_report.md
docs/audit/task_031_2_3f_email_change_schema_report.md
docs/audit/task_031_2_4_integration_preflight_report.md
docs/audit/task_031_2_5a_recovery_token_integrity_report.md
docs/audit/task_031_2_5b_migration_chain_and_checkout_report.md
docs/audit/task_031_2_5c_internal_service_auth_report.md
docs/audit/task_031_2_6_rebuild_readiness_and_rollback_plan.md
docs/audit/task_031_2_7_pre_rebuild_blocker_remediation_plan.md
docs/audit/task_031_2_8_staging_security_guard_report.md
docs/audit/task_031_2_9_backup_restore_safety_report.md
migrations/notifications/versions/20261022_0004_email_delivery_lease.py
src/common/internal_auth.py
tests/conftest.py
tests/test_email_worker_lease_pg.py
tests/test_internal_service_auth.py
```

The required new report for this task adds one further untracked report:

```text
docs/audit/task_031_2_11_rebuild_checkpoint_and_artifact_integrity_report.md
```

### 2.5 Task 031.2.9 file set

Task 031.2.9 intentionally changed or created:

| Status | Path |
|---|---|
| Modified | `scripts/backup/backup_databases.sh` |
| Modified | `scripts/backup/restore_databases.sh` |
| Modified | `tests/test_backup_restore.py` |
| Modified | `docs/operations/backup_restore_runbook.md` |
| Modified | `docs/operations/staging_deployment_runbook.md` |
| Created | `docs/audit/task_031_2_9_backup_restore_safety_report.md` |

All six paths are present in the worktree. The report and both scripts are still uncommitted.

### 2.6 Task 031.2.10 file set

Task 031.2.10 intentionally changed or created:

| Status | Path |
|---|---|
| Modified | `src/common/config.py` |
| Modified | `tests/test_internal_service_auth.py` |
| Modified | `.env.example` |
| Modified | `docs/operations/incident_recovery_runbook.md` |
| Modified | `docs/operations/staging_deployment_runbook.md` |
| Created | `docs/audit/task_031_2_10_internal_auth_deployment_readiness_report.md` |

The report lists `src/common/internal_auth.py`, service-specific auth modules, `docker-compose.yml`, Gateway routing, and `.env.staging.example` as unchanged by Task 031.2.10. Those files remain modified or untracked from earlier work, so their presence in the worktree is not evidence that Task 031.2.10 changed them.

### 2.7 Overlapping changes and provenance limits

The following overlaps are deliberate or pre-existing:

| Path | Overlap | Result |
|---|---|---|
| `docs/operations/staging_deployment_runbook.md` | Modified by both Task 031.2.9 and Task 031.2.10 | Both the restore-safety guidance and the internal-auth transition link are present |
| `src/common/config.py` | Modified during Task 031.2.5c and again by Task 031.2.10 | Current diff contains both the canonical secret work and the explicit-environment bypass guard |
| `tests/test_internal_service_auth.py` | Introduced as untracked work associated with Task 031.2.5c and modified by Task 031.2.10 | Current file is the newer coverage; it is still untracked |
| `docker-compose.yml` | Changed by earlier Task 031.2.5c work, not by Task 031.2.10 | Current diff removes the legacy key and requires the canonical secret |
| `.env.staging.example` | Changed by earlier work; Task 031.2.10 did not alter it | It contains the canonical key and bypass setting but remains uncommitted |

No merged-conflict markers or deleted paths were found. However, because the Task 031.2.1–031.2.10 work has never been checkpointed in Git, the repository cannot prove that every uncommitted hunk came only from the intended task. The overlap table is an evidence-based inventory, not a cryptographic provenance proof. This is part of the P0 reproducibility blocker.

### 2.8 Required artifacts absent from `HEAD`

| Path | Purpose | Impact if omitted from a clean checkout | Current source |
|---|---|---|---|
| `src/common/internal_auth.py` | Shared canonical header verifier and bypass policy | User, Content, Interaction, Moderation, Notification, Search, and outbox imports fail or use inconsistent behavior; source-level auth contract cannot be built | Untracked |
| `migrations/notifications/versions/20261022_0004_email_delivery_lease.py` | Adds `notifications.email_deliveries.sending_since` | Fresh databases stop at `notifications_0003`; the lease-aware worker and model reference a column that does not exist | Untracked |
| `tests/conftest.py` | Deterministic isolated-test environment | Test collection or settings startup may fail, or tests may use unsafe default configuration | Untracked |
| `tests/test_email_worker_lease_pg.py` | PostgreSQL lease-recovery coverage | No committed proof of stale-lease recovery, `SKIP LOCKED`, or `sending_since` behavior | Untracked |
| `tests/test_internal_service_auth.py` | Canonical header, bypass, and environment coverage | No committed proof of the current auth contract | Untracked |
| `docs/audit/task_031_2_9_backup_restore_safety_report.md` | Backup/restore evidence | The Task 031.2.9 remediation evidence is absent from a clean checkout | Untracked |
| `docs/audit/task_031_2_10_internal_auth_deployment_readiness_report.md` | Auth-transition evidence | The Task 031.2.10 evidence and rollback plan are absent from a clean checkout | Untracked |
| Other untracked Task 031 audit reports and API documents | Historical evidence and documentation | Audit trail and some documentation are absent, but they are not runtime dependencies | Untracked |

No untracked backup script was found. The backup and restore scripts are tracked but modified.

`git cat-file -e HEAD:src/common/internal_auth.py` and `git cat-file -e HEAD:migrations/notifications/versions/20261022_0004_email_delivery_lease.py` both fail, confirming that these files do not exist in the current commit.

---

## 3. Artifact and Migration Inventory

### 3.1 Notification migration chain

The file-based Notification chain is linear and terminates at the untracked lease migration:

| Revision | File | Down revision | Tracked in `HEAD` |
|---|---|---|---|
| `notifications_0001` | `migrations/notifications/versions/20261020_0001_initial_notifications.py` | None | Yes |
| `notifications_0002` | `migrations/notifications/versions/20261021_0002_consumed_events.py` | `notifications_0001` | Yes |
| `notifications_0003` | `migrations/notifications/versions/20261022_0003_email_deliveries.py` | `notifications_0002` | Yes |
| `notifications_0004` | `migrations/notifications/versions/20261022_0004_email_delivery_lease.py` | `notifications_0003` | No |

`notifications_0004` adds a nullable timezone-aware `sending_since` column to `notifications.email_deliveries`. The Notification model declares the same column, and `email_worker.py` uses it for lease recovery, stale-row selection, and lease clearing. `migrations/notifications/env.py` now imports the Notification models so Alembic metadata discovery is not empty.

Impact if the migration is omitted: a fresh database cannot reach the current Notification source contract, and the current worker code will query or update a column that does not exist.

The live database migration state was not queried in this task. Prior reports state that live Notification data is at `notifications_0004`; that claim is report-derived and must be rechecked with a read-only query in an approved later task before any rebuild or migration.

### 3.2 PostgreSQL-only tests and environment safeguards

| Test | Safeguard | Current status |
|---|---|---|
| `tests/test_email_worker_lease_pg.py` | Requires `TEST_NOTIFICATIONS_DATABASE_URL`; skips when unset; rejects the development `forum_notifications` database name at collection time; states that the target must be disposable and migrated to `notifications_0004` | Safely skips in this environment because the variable is unset. Not executed. |
| `tests/test_outbox_concurrency_live.py` | Uses a hard-coded local PostgreSQL URL and reads `INTERNAL_SERVICE_SECRET` at import time; no disposable-target environment guard or skip marker was found | A test run can target the development database. Not executed. |
| `tests/test_outbox_hardening.py` | Uses hard-coded local PostgreSQL URLs and reads `INTERNAL_SERVICE_SECRET` at import time; no disposable-target environment guard or skip marker was found | A test run can target development databases. Not executed. |
| `tests/test_backup_restore.py` | Uses temporary synthetic artifacts and a fake `docker` executable; no live database or daemon | Offline by design. The prior Task 031.2.9 report records 21 passing cases; not rerun here. |
| `tests/conftest.py` | Sets `ENVIRONMENT=test`, a deterministic test secret, and bypass disabled | Required for deterministic import/collection; untracked. |

The lease test's guard is good, but the two tracked live outbox tests remain unsafe for casual execution. That is a P1 test-isolation issue, not a reason to run them during this audit.

### 3.3 Internal-auth artifacts and configuration

| Artifact | Role | State |
|---|---|---|
| `src/common/internal_auth.py` | Canonical verifier for `X-Internal-Service-Key`; accepts a missing header only when the explicit bypass is enabled | Present, untracked |
| `src/services/user/auth.py` | User receiver dependency delegating to the shared verifier | Modified, tracked |
| `src/services/content/auth.py` | Content receiver dependency | Modified, tracked |
| `src/services/notification/auth.py` | Notification receiver dependency | Modified, tracked |
| `src/services/search/auth.py` | Search receiver dependency | Modified, tracked |
| `src/common/outbox_worker.py` | Sends the canonical header on outbox delivery | Modified, tracked |
| `src/services/notification/events.py` | Calls User Service with the canonical header | Tracked; inspected by search |
| `src/services/user/app.py` | Calls Content Service with the canonical header | Modified, tracked |
| `src/common/config.py` | Requires non-empty secret; fail-closed bypass validation | Modified, tracked |
| `docker-compose.yml` | Supplies `INTERNAL_SERVICE_SECRET`, `ENVIRONMENT`, and bypass to six application services | Modified, tracked |
| `.env.example` | Development template with canonical keys and bypass false | Modified, tracked |
| `.env.staging.example` | Staging template with canonical keys and bypass false | Modified, tracked |
| `tests/test_internal_service_auth.py` | Focused auth contract and Compose/static checks | Untracked |

No active source file, Compose service, or active runbook found in this audit uses the legacy environment variable `INTERNAL_SERVICE_KEY` or the legacy header `X-Internal-Key`. The legacy references are in the running container environment and historical audit evidence, not in the current source contract.

### 3.4 Artifact-integrity conclusion

The current worktree contains a coherent source-level implementation, but it is not a committed checkpoint. The untracked runtime module and migration, plus the uncommitted modifications to service code and Compose, mean that the "current source" exists only in this local worktree. A rebuild from `HEAD` would not reproduce it.

---

## 4. Image Preservation Inventory

### 4.1 Current running application images

All seven application containers are running and healthy according to read-only Docker metadata. Image inspection used `docker inspect`, `docker image ls`, and selected image metadata commands only. No image, container, tag, or daemon state was changed.

| Container | Service | Current image reference | Full image ID | Docker tags | Dedicated preservation archive |
|---|---|---|---|---|---|
| `forum_api_gateway` | Gateway | `forumplatform-gateway:latest` | `sha256:010b8b460375538c25717d3799cef7bb476e4ee0f248529ee312624a6c389611` | `latest` only | None found |
| `forum_user_service` | User | `forumplatform-user-service:latest` | `sha256:4a1c1198a0ed1a33ac3c076016b7cf8aac1a0b9bd12840f519aaaa70396f3b90` | `latest`, `recovery-4a1c1198a0ed` | `/tmp/opencode/user-service-4a1c1198a0ed.tar` |
| `forum_content_service` | Content | `forumplatform-content-service:latest` | `sha256:2f94eea2659add036e66dfea9d301bc799c518dfead73e6d007fd437d7200437` | `latest` only | None found |
| `forum_interaction_service` | Interaction | `forumplatform-interaction-service:latest` | `sha256:3fe104995d8834671400c6a0e0ec2edf0a5366a78a4f2d7e823b34ea4f561165` | `latest` only | None found |
| `forum_moderation_service` | Moderation | `forumplatform-moderation-service:latest` | `sha256:cbd91c33607b405054ef862593d25cd02ec98e28734ff98649f79b334266a7cd` | `latest` only | None found |
| `forum_notification_service` | Notification | `forumplatform-notification-service:latest` | `sha256:429d8167327eec2f316034068f4bc0eee681ba263876b388c8042c86e9de3552` | `latest` only | None found |
| `forum_search_service` | Search | `forumplatform-search-service:latest` | `sha256:a19cbd6bd20d52a7720fba22cda4be7afa7497e00aac16f7def7ec2739903219` | `latest` only | None found |

The six application services required by the task are User, Content, Interaction, Moderation, Notification, and Search. The Gateway is included because it is the seventh running application container and must also survive a rebuild.

### 4.2 Preservation evidence

| Service | Image ID known | Dedicated tag | Archive verified | Source revision known | Rollback identification |
|---|---|---|---|---|---|
| Gateway | Yes | No | No | No | Full local ID only; no preserved tag/archive |
| User | Yes | Yes: `forumplatform-user-service:recovery-4a1c1198a0ed` | Yes; existing tar and checksum verified read-only | No OCI revision label; unknown | Recovery tag and archive identify the image, but no restore test proves deployability |
| Content | Yes | No | No | No | Full local ID only; mutable `latest` |
| Interaction | Yes | No | No | No | Full local ID only; mutable `latest` |
| Moderation | Yes | No | No | No | Full local ID only; mutable `latest` |
| Notification | Yes | No | No | No | Full local ID only; mutable `latest` |
| Search | Yes | No | No | No | Full local ID only; mutable `latest` |

All `RepoDigests` are local repository digests of the form `forumplatform-<service>@sha256:<same-image-id>`. No registry-pushed digest or signature evidence was found. An image ID in the local Docker store is not a durable external preservation record.

### 4.3 User archive check

The User archive exists at `/tmp/opencode/user-service-4a1c1198a0ed.tar`, is 108,246,016 bytes, and has SHA-256:

```text
cfbd035a36ee22ac08a2c22056c2925c5fc6fc18459e85b57b308e470d59f6f4
```

That checksum matches the value recorded in the Task 031.2.3a report. The archive's existence and checksum were verified read-only. It was not extracted, loaded, retagged, or restored. Its location under `/tmp` is not durable evidence against host cleanup or reboot; it should be moved to an approved preservation location before a rebuild.

### 4.4 Image preservation conclusion

The current image IDs are known, and the User recovery tag points to the same full ID as the running User image. That is useful rollback evidence. It is not sufficient to declare all six application images preserved:

- Five of the six application services have no dedicated non-`latest` tag and no verified archive.
- The Gateway has no dedicated tag or archive either.
- No image has an OCI source-revision label. The only image label is `com.docker.compose.image.builder=classic`; build timestamps do not prove which source revision produced each image.
- Tags alone are not sufficient. A preserved tag is only meaningful if it resolves to the recorded immutable ID, and an archive is only meaningful if it can be verified and loaded.
- No rollback restore or load test has been performed.

The image status is therefore **partially preserved**, not complete. The preservation gate is failed.

---

## 5. Configuration Transition Audit

### 5.1 Source, template, and runtime contract

| Surface | `INTERNAL_SERVICE_SECRET` | `X-Internal-Service-Key` | `INTERNAL_SERVICE_AUTH_BYPASS` | `ENVIRONMENT` |
|---|---|---|---|---|
| `src/common/internal_auth.py` | Expected value is read from settings | Canonical request header | Controls missing-header bypass | Not read directly by the verifier |
| `src/common/config.py` | Required non-empty; staging/production enforce length and non-default | Not applicable | Fail-closed; requires explicit `development` or `test` when enabled | Controls bypass and secret-strength policy |
| `docker-compose.yml` | Required for all six application services via interpolation | Not set as Compose variable | Defaults to `false` | Defaults to `development` if omitted |
| `.env.example` | Present as a documented placeholder | Documented canonical contract | `false` | `development` |
| `.env.staging.example` | Present as a documented placeholder that staging validation rejects | Documented canonical contract | `false` | `staging` |
| Actual `.env` | No key found | No key found | No key found | No key found |
| Actual `.env.staging` | File absent | File absent | File absent | File absent |
| Running Gateway | Unset | Not applicable to a caller-only gateway | Unset | Unset |
| Running User | Unset | Unset in inspected environment | Unset | Unset |
| Running Content | Unset | Unset in inspected environment | Unset | Unset |
| Running Interaction | Unset | Unset in inspected environment | Unset | Unset |
| Running Moderation | Unset | Unset in inspected environment | Unset | Unset |
| Running Notification | Unset | Unset in inspected environment | Unset | Unset |
| Running Search | Unset | Unset in inspected environment | Unset | Unset |

The running application containers have the legacy `INTERNAL_SERVICE_KEY` variable present. Its value was not printed. The canonical `INTERNAL_SERVICE_SECRET` is absent or empty in all six running application containers. The Gateway has neither key, which is consistent with it not being a protected internal receiver.

The actual `.env` file is 6 bytes and contains no recognized `KEY=value` assignment for the canonical secret, bypass, or environment. No secret value was printed or reproduced in this report.

### 5.2 Legacy references and transition risk

The active source, Compose file, templates, and operational runbooks now use the canonical contract. The legacy contract survives in the currently deployed container configuration. That creates these concrete transition risks:

1. **Partial rebuild:** If only some services are rebuilt, old containers will continue to expect or send the legacy key while new containers expect `INTERNAL_SERVICE_SECRET` and `X-Internal-Service-Key`. Internal calls can return 401 and outbox delivery can fail.
2. **Missing secret:** Compose uses `${INTERNAL_SERVICE_SECRET:?...}`, so a rebuild using the current `.env` cannot interpolate the configuration unless the protected environment supplies the secret.
3. **Implicit development environment:** Compose defaults `ENVIRONMENT` to `development`. A staging deployment that forgets to set it will receive development defaults and will not enforce the staging/production secret-strength checks.
4. **Bypass risk:** The source is now fail-closed unless `ENVIRONMENT` is explicitly `development` or `test`, but the bypass must remain `false` for staging/production. It must not be used to bridge a mixed-version transition.
5. **No live auth test:** No live endpoint request was made, so the running containers' actual acceptance or rejection behavior was not verified. Only environment-key presence and source/documentation contracts were inspected.

### 5.3 Configuration conclusion

The source and Compose contract is coherent for a future cutover. The deployed runtime is not on that contract. The required environment values are missing from the actual `.env`, and the running containers still use the legacy variable. The configuration-transition gate is failed until a protected canonical secret, explicit environment, and an all-six-service cutover plan are available.

---

## 6. Backup and Restore Readiness

### 6.1 Readiness classification

| Layer | Status | Evidence |
|---|---|---|
| Script-level validation | Implemented and statically verified | Both shell scripts pass `bash -n`; Task 031.2.9 records 21 offline test cases using synthetic artifacts and a fake Docker executable |
| Archive integrity validation | Supported by the new script format, but not demonstrated on a current live backup | The backup script writes `SHA256SUMS`, verifies it, and runs `pg_restore --list`; no current-format backup was found under the repository `backups/` path to verify |
| Database restore rehearsal | Not completed | Task 031.2.9 explicitly records that no disposable PostgreSQL instance or destructive restore branch was executed |
| Application-level recovery validation | Not completed | No post-restore application smoke test, data-count validation, or service-level recovery test was performed |

### 6.2 Existing backup artifacts

Two tracked backup directories are present:

```text
backups/20261021_083119Z/
backups/20261021_084751Z/
```

Each contains six `.dump` files and one `backup_manifest.json`, for 14 tracked backup files total. Neither directory contains `SHA256SUMS`. Neither manifest contains the new `checksum_algorithm` field; both contain only the older keys `backup_format`, `databases`, `status`, and `timestamp`, with `status=completed`.

The hardened restore script rejects these old artifacts until an approved checksum and manifest process is applied. Therefore the current tracked backups are not verified recovery artifacts under the current procedure.

### 6.3 Restore safety controls observed

The restored script design is materially safer than the pre-remediation version:

- `--backup-dir`, `--target-prefix`, and `--environment` are mandatory.
- The target prefix must match a disposable `restore_..._` style pattern.
- Empty, ambiguous, and live database names are rejected before any Docker/PostgreSQL action.
- A destructive restore requires an exact, non-empty `--confirm-target` matching the target prefix.
- `SHA256SUMS`, the manifest, the dump set, and an `INCOMPLETE` marker are validated before Docker or `psql` is invoked.
- A failed backup remains in a `.partial` directory with an `INCOMPLETE` marker.

These are script-level controls. They do not prove that a real `pg_dump` archive from this deployment can be restored, that roles and ownership are correct, that `--single-transaction` works with the archive, or that the restored application works.

### 6.4 Checks not performed

No backup was created, no archive was checksummed or listed with `pg_restore`, no database was connected to, no restore was executed, and no backup file was modified. The existing archives were not inspected for content or test-restored.

---

## 7. Gate Decision and Blocker Register

### 7.1 Verdict

**BLOCKED — REQUIRED EVIDENCE OR ARTIFACTS MISSING**

This is a single verdict. It is not a READY verdict, and it does not authorize a rebuild, a migration, a container recreation, a restore, or the start of Task 031.2.12.

### 7.2 P0 blockers

| ID | P0 blocker | Evidence | Smallest safe next action |
|---|---|---|---|
| P0-1 | The current source is not reproducible from `HEAD` | `HEAD` equals baseline; zero commits after baseline; 34 modified and 27 untracked entries; required shared auth module and `notifications_0004` are absent from `HEAD` | Run a separate approved checkpoint task to review and commit the required source, migration, tests, Compose, and evidence, or produce an immutable patch bundle; verify a clean checkout contains all required files. Do not rebuild first. |
| P0-2 | Image preservation is incomplete | Only User has a dedicated recovery tag and archive; five other application services and the Gateway have only mutable `latest`; no OCI source-revision labels; no archive for five services or Gateway; User archive is under `/tmp` | In a separate approved preservation task, tag each current image ID with a non-`latest` recovery tag, export and checksum each archive to durable storage, record the source revision or document it as unknown, and verify each tag resolves to the recorded ID. Do not build or retag `latest`. |
| P0-3 | Live internal-auth configuration is split from the source contract | Six running application containers have legacy `INTERNAL_SERVICE_KEY`, no canonical secret, and no explicit environment; `.env` has no canonical keys; Compose requires the secret; source accepts only `X-Internal-Service-Key` | Prepare a protected deployment environment with a non-default canonical secret of at least 32 characters and an explicit `ENVIRONMENT`; plan an atomic all-six-service cutover with the same secret and bypass false; validate Compose interpolation with a dummy secret. Do not send live internal requests until the cutover is approved. |
| P0-4 | No current-format verified backup is present in the repository backup path | Two tracked backup sets lack `SHA256SUMS` and new manifest fields; no current-format backup was found under `backups/`; no live archive has been integrity-checked | In an approved separate operation, create a fresh backup through the hardened script, verify `SHA256SUMS` and `pg_restore --list`, and record the six database heads and object counts. Do not commit the backup. |
| P0-5 | No isolated restore rehearsal or application recovery validation exists | Task 031.2.9 explicitly states no disposable restore was performed; no database was connected to in this audit; no post-restore app test exists | In an approved isolated rehearsal, restore the verified backup into disposable `restore_..._` databases, verify schema heads and data, run application-level checks, and leave live databases untouched. A failed rehearsal stops the rebuild. |

### 7.3 P1 blockers and risks

| ID | P1 blocker or risk | Evidence | Smallest safe next action |
|---|---|---|---|
| P1-1 | PostgreSQL tests have inconsistent isolation guards | `test_email_worker_lease_pg.py` safely skips without `TEST_NOTIFICATIONS_DATABASE_URL` and rejects the development name; `test_outbox_concurrency_live.py` and `test_outbox_hardening.py` hard-code local development database URLs and have no skip/disposable-target guard | Add an approved test-isolation guard before running these tests; do not run the unguarded live tests during audit or rebuild validation. |
| P1-2 | Live migration state is not reverified in this task | Task 031.2.5b/031.2.7 reports say the live Notification database is at `notifications_0004`, but this audit was barred from database connections; the source file is untracked | In an approved read-only database check, query the six schema version tables explicitly by name and record the results. Do not use a mutating `migrate.py current` command. |
| P1-3 | The User archive is in a temporary location | Archive exists at `/tmp/opencode/user-service-4a1c1198a0ed.tar` and its checksum matches prior evidence, but `/tmp` can be cleaned | Move the verified archive to an approved durable location and record its checksum in the image-preservation manifest. |
| P1-4 | No repository CI or automated checkpoint gate enforces these conditions | No GitHub Actions or equivalent workflow was found; all gates are manual | Make the approved checkpoint, image-preservation, backup-integrity, restore-rehearsal, and clean-checkout checks explicit gates in the separate execution plan. |

### 7.4 What would allow a READY verdict later

A later audit could return **READY FOR A SEPARATE CONTROLLED REBUILD PLAN** only if all of the following evidence exists:

1. A committed or otherwise immutable checkpoint contains every required source, migration, test, Compose, and evidence artifact, and a clean checkout passes its focused checks.
2. Every currently deployed application image has a non-`latest` recovery tag resolving to the recorded immutable ID, plus verified durable archives or an explicitly approved equivalent.
3. The canonical secret and explicit environment are provisioned in a protected configuration, with bypass false, and a static all-six-service cutover/rollback plan is ready.
4. A current-format backup with six non-empty dumps, `SHA256SUMS`, and manifest is verified with `sha256sum -c` and `pg_restore --list`.
5. An isolated restore rehearsal and application-level recovery validation have passed without touching live databases.
6. The live migration heads are verified read-only and match the committed migration graph.

Even that later READY verdict would authorize only the drafting of a separate execution plan. It would not authorize a rebuild.

---

## 8. Validation Performed and Limitations

### 8.1 Validation performed

| Check | Result |
|---|---|
| `git rev-parse HEAD` and branch check | `HEAD` is `bf3384c4…`; branch is `master` |
| Baseline verification | Baseline exists as a commit, is an ancestor of `HEAD`, and has zero commits after it |
| `git status --porcelain=v1 -uall` | 34 tracked modifications and 27 pre-existing untracked entries, 61 total before this report |
| `git diff`, `git diff --stat`, `git diff --cached` | Tracked diff enumerated; no staged changes; no deletions |
| `git show-ref`, tags, stashes, worktrees | Only `master` locally; no tags; no stashes; one worktree |
| Required-artifact presence in `HEAD` | `internal_auth.py` and Notification `0004` are absent from `HEAD` |
| Docker version and container metadata | Docker client/server 29.9.2; seven application containers healthy |
| Image metadata | Seven image IDs, tags, local digests, creation timestamps, and selected labels inspected; no OCI source-revision label |
| Runtime environment-key inspection | Legacy/canonical key presence inspected without printing secret values |
| Notification migration graph | File-based chain `0001 → 0002 → 0003 → 0004` confirmed |
| Backup script syntax | `bash -n` passed for both backup and restore scripts |
| Backup manifests | Two old manifests parsed; no `SHA256SUMS` and no new checksum field |
| Existing `git diff --check` | Passed for tracked worktree changes; command exited 0 with no diagnostics |
| New report whitespace check | `git diff --no-index --check -- /dev/null <report>` produced no diagnostics; its exit status 1 is expected because `/dev/null` and the report differ. An independent trailing-whitespace scan found zero affected lines. |
| Markdown structure | 492 lines; 39 headings; 12 code-fence markers, balanced; 16 tables, all rows with consistent column widths |
| Final worktree status | 34 tracked modifications and 28 untracked entries; the only addition relative to the pre-audit 27 untracked entries is this report |

### 8.2 Checks not performed

- No clean checkout of a commit containing the current work was possible because no such commit exists.
- No database was connected to, queried, migrated, or modified.
- No live endpoint or internal-auth request was made.
- No backup was created or restored, and no `pg_restore` archive was loaded.
- No test suite was executed; prior test results are report-derived, and only `bash -n` and static inspection were run here.
- No image was built, tagged, loaded, exported, retagged, deleted, or restarted.
- No full container environment values, secret values, credentials, or database connection strings were printed.
- No remote fetch was performed, so remote branch state beyond local `origin/master` metadata is unverified.
- Markdown rendering by a browser was not performed; structure was checked with local text parsing.

### 8.3 Explicit non-mutation statement

This task created exactly one new repository file:

```text
docs/audit/task_031_2_11_rebuild_checkpoint_and_artifact_integrity_report.md
```

No source, test, migration, script, configuration, runbook, or prior audit report was modified. No Git staging, commit, push, reset, clean, stash, checkout, or branch switch was performed. No container or image was built, tagged, loaded, deleted, stopped, restarted, or recreated. No database connection, migration, backup, restore, or cleanup was performed. No live endpoint request was made. No secret value was disclosed. Task 031.2.12 was not started.

---

**Final verdict:** **BLOCKED — REQUIRED EVIDENCE OR ARTIFACTS MISSING**
