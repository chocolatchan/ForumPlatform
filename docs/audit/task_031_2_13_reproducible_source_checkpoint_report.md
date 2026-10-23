# Task 031.2.13 — Reproducible Source Checkpoint Report

**Date:** 2026-10-23  
**Task:** Reproducible source checkpoint preparation  
**Mode:** Read-only Git and source inspection; no staging, commit, fetch, ref change, database connection, migration, container action, or worktree mutation  
**Recorded baseline commit:** `bf3384c4c3b53b5f826ef119f2ad549997ff1d63`  
**Recorded baseline subject:** `feat(tasks 028-031): account recovery, avatar & email-change, outbound email dispatch`  
**Branch:** `master`  
**Final gate decision:** **READY FOR EXPLICIT CHECKPOINT AUTHORIZATION**

This report is an inventory and checkpoint-preparation record. The READY decision does not authorize staging or committing now, and it does not authorize starting Task 031.2.14.

---

## 1. Executive Summary

The current worktree is based exactly on the recorded baseline commit. A fresh read-only inventory found:

- 34 tracked modifications.
- 30 pre-existing untracked files.
- 0 staged changes.
- 0 tracked deletions.
- 0 unmerged paths.
- 0 renames.
- 0 file-mode changes.
- 0 stashes.
- 1 worktree.

All 34 modified tracked paths exist in `HEAD`. All 30 pre-existing untracked paths are absent from `HEAD`. There is no path overlap between the modified and untracked sets. The two sets contain 64 unique changed entries.

The checkpoint requires all 64 pre-existing entries plus the Task 031.2.13 report created by this task. The most important baseline gaps are the shared internal-auth module, the Notification `notifications_0004` lease migration, the test harness files, and the audit/API documentation. The source, tests, migration, Compose, backup tooling, runbooks, and audit evidence are interdependent. A single coherent checkpoint is recommended rather than category-ordered intermediate commits.

This report does not conclude that the runtime environment is ready, that PostgreSQL integration has passed, or that a checkpoint has been created. Those remain separately gated operations.

---

## 2. Read-Only Git Inventory

### 2.1 Repository state

| Item | Observed value |
|---|---|
| `HEAD` | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| `origin/master` | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| Branch | `master` |
| Upstream | `origin/master` |
| Ahead/behind at inspection time | `+0 -0` |
| Tracked modifications | 34 |
| Untracked files before this report | 30 |
| Staged entries | 0 |
| Deleted tracked paths | 0 |
| Unmerged paths | 0 |
| Renames | 0 |
| File-mode changes | 0 |
| Stashes | 0 |
| Worktrees | 1 |
| `git diff --check` | Exit code `0` |
| Modified/untracked path overlap | 0 |
| Duplicate paths within either set | 0 |

No fetch was performed. No ref was changed. The baseline comparison is therefore against the locally recorded `HEAD` and `origin/master`, both of which resolve to `bf3384c4c3b53b5f826ef119f2ad549997ff1d63`.

### 2.2 Exact tracked modifications

Every path below is tracked in `HEAD` and has unstaged modifications. None is staged.

| # | Exact path | In baseline | Role and dependency | Risk if omitted |
|---:|---|---|---|---|
| 1 | `.env.example` | Yes | Documents `EMAIL_WORKER_LEASE_TIMEOUT_SECONDS`, `INTERNAL_SERVICE_SECRET`, and `INTERNAL_SERVICE_AUTH_BYPASS` | Local/operator configuration contract is incomplete |
| 2 | `.env.staging.example` | Yes | Staging example for the canonical secret and bypass-disabled policy | Staging configuration guidance is incomplete |
| 3 | `docker-compose.yml` | Yes | Six-service canonical internal secret, explicit environment, bypass flag, Notification SMTP/lease settings, and Mailpit dependency | Compose cannot provide the runtime contract expected by source |
| 4 | `docs/operations/backup_restore_runbook.md` | Yes | Documents hardened backup/restore flags, checksums, disposable targets, and migration heads | Backup safety test fails and operators receive stale restore guidance |
| 5 | `docs/operations/incident_recovery_runbook.md` | Yes | Documents single-cutover internal-secret rotation and forbids bypass as a shortcut | Incident guidance contradicts the fail-closed implementation |
| 6 | `docs/operations/staging_deployment_runbook.md` | Yes | Documents read-only migration checks, current heads, and safe restore escalation | Backup safety test fails and staging guidance can encourage mutating migration commands |
| 7 | `migrations/notifications/env.py` | Yes | Imports Notification models so Alembic metadata includes the lease column | Migration metadata is incomplete and autogenerate/drift checks are misleading |
| 8 | `scripts/backup/backup_databases.sh` | Yes | Atomic `.partial` backups, archive listing, `SHA256SUMS`, and completed manifest | Backup safety test fails; restore artifact contract is absent |
| 9 | `scripts/backup/restore_databases.sh` | Yes | Explicit disposable target flags, checksum verification, and destructive confirmation | Backup safety test fails; unsafe restore paths remain possible |
| 10 | `scripts/run_benchmarks.py` | Yes | Uses the configured internal secret instead of a hard-coded default | Benchmark runs use a stale credential and fail closed |
| 11 | `src/common/config.py` | Yes | Requires a non-empty secret and enforces fail-closed bypass/environment rules | Authentication can regress to an empty or default credential policy |
| 12 | `src/common/outbox_worker.py` | Yes | Imports the canonical header constant and rejects an empty internal key | Outbox callers disagree with receivers or retain a legacy credential default |
| 13 | `src/services/content/auth.py` | Yes | Delegates receiver authentication to the shared verifier | Content Service can diverge from the canonical internal-auth policy |
| 14 | `src/services/notification/auth.py` | Yes | Delegates receiver authentication to the shared verifier | Notification Service can diverge from the canonical internal-auth policy |
| 15 | `src/services/notification/config.py` | Yes | Adds `email_worker_lease_timeout_seconds` | Lease recovery code has no supported timeout setting |
| 16 | `src/services/notification/email_worker.py` | Yes | Sets/clears `sending_since` and recovers expired or null leases | `notifications_0004` has no working producer; stuck `sending` rows are never recovered |
| 17 | `src/services/notification/models.py` | Yes | Declares `EmailDelivery.sending_since` | ORM and migration schema disagree; lease recovery cannot run |
| 18 | `src/services/search/auth.py` | Yes | Delegates receiver authentication to the shared verifier | Search Service can diverge from the canonical internal-auth policy |
| 19 | `src/services/user/app.py` | Yes | Restores avatar, recovery, email-change, public profile/admin handlers, guarded test hooks, and cross-service callers | User-facing lifecycle functionality is absent or incomplete |
| 20 | `src/services/user/auth.py` | Yes | Delegates receiver authentication to the shared verifier | User Service can diverge from the canonical internal-auth policy |
| 21 | `src/services/user/config.py` | Yes | Adds fail-closed `test_endpoints_enabled` | Test hooks may be exposed by default or the application may fail to import |
| 22 | `src/services/user/schemas.py` | Yes | Makes `ChangeEmailRequest.password` required | Email-change re-authentication contract and tests are inconsistent |
| 23 | `tests/test_backup_restore.py` | Yes | Tests backup/restore scripts, runbooks, and offline migration heads | Hardened backup/restore behavior has no automated verification |
| 24 | `tests/test_e2e_integration.py` | Yes | Uses the configured internal-auth header in E2E service calls | E2E requests use a stale credential and fail authentication |
| 25 | `tests/test_email_dispatch_worker.py` | Yes | Aligns SQLite schema setup with the updated Notification model and worker | Email worker test setup breaks or omits lease behavior |
| 26 | `tests/test_gateway.py` | Yes | Verifies test-only routes are not exposed through the public gateway | Route-exposure regression is untested |
| 27 | `tests/test_notification_service.py` | Yes | Supplies the configured internal-auth header | Notification ingestion tests fail with 401 |
| 28 | `tests/test_outbox_concurrency_live.py` | Yes | Uses explicit disposable test DB configuration and the shared header | Live/shared database risk returns or collection fails |
| 29 | `tests/test_outbox_hardening.py` | Yes | Adds isolation policy tests, cleanup scoping tests, and configured header use | Unsafe database targeting and header drift are untested |
| 30 | `tests/test_outbox_worker.py` | Yes | Supplies the now-required internal key | Outbox worker construction fails |
| 31 | `tests/test_search_service.py` | Yes | Supplies the configured internal-auth header | Search ingestion tests fail with 401 |
| 32 | `tests/test_security_hardening.py` | Yes | Covers test-endpoint gating by explicit environment | Test-hook exposure regression is untested |
| 33 | `tests/test_staging_operational_readiness.py` | Yes | Supplies the required internal key to worker construction | Staging worker tests fail at construction |
| 34 | `tests/test_user_auth_lifecycle.py` | Yes | Adds recovery-token cardinality and email-change password tests | Restored lifecycle behavior has no focused regression coverage |

### 2.3 Exact pre-existing untracked files

All 30 paths below are absent from `HEAD`. None was created by this task.

| # | Exact path | In baseline | Classification | Dependency or omission risk |
|---:|---|---|---|---|
| 1 | `docs/api/forum_api_endpoint_reference.md` | No | Source-grounded API reference | No runtime dependency; omission leaves the endpoint reference absent from the checkpoint |
| 2 | `docs/api/forum_api_workflow_guide.md` | No | Source-grounded workflow guide | No runtime dependency; omission leaves the workflow guide absent from the checkpoint |
| 3 | `docs/audit/task_031_1_change_log.md` | No | Task 031.1 evidence | Historical evidence trail incomplete; no runtime import |
| 4 | `docs/audit/task_031_1_design.md` | No | Task 031.1 design evidence | Historical design evidence missing; no runtime import |
| 5 | `docs/audit/task_031_1_validation_report.md` | No | Task 031.1 validation evidence | Historical validation evidence missing; no runtime import |
| 6 | `docs/audit/task_031_2_1_inventory_report.md` | No | Task 031.2.1 evidence | Git-preservation evidence missing; no runtime import |
| 7 | `docs/audit/task_031_2_2_discrepancy_report.md` | No | Task 031.2.2 evidence | Source/runtime discrepancy evidence missing; no runtime import |
| 8 | `docs/audit/task_031_2_3a_image_preservation_report.md` | No | Task 031.2.3a evidence | Image-preservation evidence missing; no runtime import |
| 9 | `docs/audit/task_031_2_3b_restoration_map.md` | No | Task 031.2.3b evidence | Restoration-map evidence missing; no runtime import |
| 10 | `docs/audit/task_031_2_3c_recovery_restoration_report.md` | No | Task 031.2.3c evidence | Recovery-restoration evidence missing; no runtime import |
| 11 | `docs/audit/task_031_2_3d_email_change_restoration_report.md` | No | Task 031.2.3d evidence | Email-change-restoration evidence missing; no runtime import |
| 12 | `docs/audit/task_031_2_3e_avatar_restoration_report.md` | No | Task 031.2.3e evidence | Avatar-restoration evidence missing; no runtime import |
| 13 | `docs/audit/task_031_2_3f_email_change_schema_report.md` | No | Task 031.2.3f evidence | Schema-alignment evidence missing; no runtime import |
| 14 | `docs/audit/task_031_2_4_integration_preflight_report.md` | No | Task 031.2.4 evidence | Integration-preflight evidence missing; no runtime import |
| 15 | `docs/audit/task_031_2_5a_recovery_token_integrity_report.md` | No | Task 031.2.5a evidence | Recovery-token evidence missing; no runtime import |
| 16 | `docs/audit/task_031_2_5b_migration_chain_and_checkout_report.md` | No | Task 031.2.5b evidence | Migration-chain evidence missing; no runtime import |
| 17 | `docs/audit/task_031_2_5c_internal_service_auth_report.md` | No | Task 031.2.5c evidence | Internal-auth evidence missing; no runtime import |
| 18 | `docs/audit/task_031_2_6_rebuild_readiness_and_rollback_plan.md` | No | Task 031.2.6 evidence | Rebuild/rollback evidence missing; no runtime import |
| 19 | `docs/audit/task_031_2_7_pre_rebuild_blocker_remediation_plan.md` | No | Task 031.2.7 evidence | Blocker-remediation evidence missing; no runtime import |
| 20 | `docs/audit/task_031_2_8_staging_security_guard_report.md` | No | Task 031.2.8 evidence | Staging-guard evidence missing; no runtime import |
| 21 | `docs/audit/task_031_2_9_backup_restore_safety_report.md` | No | Task 031.2.9 evidence | Backup/restore evidence missing; no runtime import |
| 22 | `docs/audit/task_031_2_10_internal_auth_deployment_readiness_report.md` | No | Task 031.2.10 evidence | Deployment-readiness and rollback evidence missing; no runtime import |
| 23 | `docs/audit/task_031_2_11_rebuild_checkpoint_and_artifact_integrity_report.md` | No | Task 031.2.11 evidence | Prior checkpoint blocker evidence missing; no runtime import |
| 24 | `docs/audit/task_031_2_12_outbox_test_isolation_report.md` | No | Task 031.2.12 evidence | Outbox isolation evidence missing; no runtime import |
| 25 | `migrations/notifications/versions/20261022_0004_email_delivery_lease.py` | No | New Notification migration | Clean checkout remains at `notifications_0003`; lease recovery has no column |
| 26 | `src/common/internal_auth.py` | No | Shared internal-auth verifier and canonical header | All modified receivers and the outbox worker fail to import; clean checkout is not buildable |
| 27 | `tests/conftest.py` | No | Deterministic test environment and test-only secret | Multiple test modules fail at import/collection; `ENVIRONMENT` and internal-auth tests are nondeterministic |
| 28 | `tests/outbox_pg_isolation.py` | No | Explicit disposable test-database policy helper | Both outbox test modules fail to import and the no-fallback policy disappears |
| 29 | `tests/test_email_worker_lease_pg.py` | No | PostgreSQL lease-recovery integration tests | Lease recovery has no real-PostgreSQL verification |
| 30 | `tests/test_internal_service_auth.py` | No | Focused internal-auth and Compose alignment tests | Canonical internal-auth behavior and receiver wiring are untested |

### 2.4 Task deliverable added after the initial inventory

| Path | In baseline | State after this task | Dependency or omission risk |
|---|---|---|---|
| `docs/audit/task_031_2_13_reproducible_source_checkpoint_report.md` | No | New untracked audit report | This report is the Task 031.2.13 evidence artifact; omission would leave the checkpoint plan unrecorded |

After this report is created, the worktree contains 31 untracked files and still 34 tracked modifications. The 30-file pre-existing inventory above is preserved unchanged.

### 2.5 Tracked-clean supporting artifacts required by the changed work

These files are already in `HEAD`, are not modified, and must not be staged. They are dependencies of the changed source/tests and must be present in any clean checkout.

| Exact path | State | Why it is required |
|---|---|---|
| `src/services/user/models.py` | Tracked clean | Declares `avatar_object_key`, `OneTimeToken`, `EmailChangeRequest`, and resend-attempt fields used by restored handlers |
| `migrations/users/versions/20261022_0003_email_change_resend_columns.py` | Tracked clean | Provides `users_0003` resend columns/indexes expected by the current User model and runbook heads |
| `migrations/notifications/versions/20261022_0003_email_deliveries.py` | Tracked clean | Direct parent of `notifications_0004` |
| `src/services/user/security.py` | Tracked clean | Password, session, OTP, and reset-token primitives used by restored handlers and tests |
| `src/services/notification/events.py` | Tracked clean | Sends internal events using the configured secret |
| `src/services/notification/templates.py` | Tracked clean | Email rendering used by notification dispatch and lease tests |
| `src/services/content/app.py` | Tracked clean | Provides the internal avatar claim/release endpoints called by the restored User handlers |
| `src/common/models.py` | Tracked clean | Shared SQLAlchemy metadata used by Notification migration discovery |
| `migrations/async_env.py` | Tracked clean | Shared migration runner; note that it executes schema creation even during read-only Alembic inspection |
| `pyproject.toml` | Tracked clean | Dependency, Ruff, Pyright, and pytest configuration |

---

## 3. Dependency and Ordering Constraints

### 3.1 Internal-service authentication

`src/common/internal_auth.py` is a new shared module. It defines the canonical `X-Internal-Service-Key` header constant and the fail-closed verifier. The following source modules import it:

| Importer | Dependency |
|---|---|
| `src/services/content/auth.py` | `verify_internal_service_request` |
| `src/services/notification/auth.py` | `verify_internal_service_request` |
| `src/services/search/auth.py` | `verify_internal_service_request` |
| `src/services/user/auth.py` | `verify_internal_service_request` |
| `src/common/outbox_worker.py` | Canonical header constant |

The verifier depends on settings fields provided by `src/common/config.py`:

- `internal_service_secret`
- `internal_service_auth_bypass`

`src/common/config.py` makes the secret non-empty and rejects bypass unless the environment is explicitly `development` or `test`. `docker-compose.yml`, `.env.example`, and `.env.staging.example` provide the corresponding configuration contract. The six application services in Compose require the canonical secret; the legacy `INTERNAL_SERVICE_KEY` wiring is removed.

The Unity of this chain matters:

- The verifier cannot exist alone without the receiver and outbox imports.
- The receivers cannot import the verifier unless the new module is included.
- Compose cannot start the changed services without a non-empty canonical secret.
- `tests/conftest.py` must supply deterministic `ENVIRONMENT`, `INTERNAL_SERVICE_SECRET`, and bypass values at import time for multiple test modules.
- `tests/test_internal_service_auth.py` verifies the verifier, all receiver delegations, the no-default outbox key, the canonical header, and the six-service Compose contract.

### 3.2 Notification migration and email-worker lease recovery

The Notification migration graph is linear:

```text
notifications_0001
  -> notifications_0002
  -> notifications_0003 (email_deliveries)
  -> notifications_0004 (email_delivery_lease; new, absent from baseline)
```

`notifications_0004` adds `notifications.email_deliveries.sending_since`. The implementation dependencies are:

| Artifact | Dependency |
|---|---|
| `src/services/notification/models.py` | Maps `sending_since` |
| `src/services/notification/email_worker.py` | Sets/clears `sending_since`, performs lease recovery, and implements startup/periodic recovery |
| `src/services/notification/config.py` | Supplies `email_worker_lease_timeout_seconds` |
| `migrations/notifications/env.py` | Imports Notification models so Alembic metadata includes the field |
| `.env.example` and `docker-compose.yml` | Expose `EMAIL_WORKER_LEASE_TIMEOUT_SECONDS` |
| `tests/test_email_worker_lease_pg.py` | Requires a disposable Notification database migrated through `notifications_0004` |
| `tests/test_email_dispatch_worker.py` | Exercises worker behavior against the updated model and worker |

Leaving out any of the model, worker, migration, or setting creates a broken intermediate state. The migration alone is insufficient for runtime; the runtime worker alone cannot persist lease timestamps; and the tests alone cannot run on a clean checkout.

### 3.3 User recovery, email-change, and avatar restoration

`src/services/user/app.py` restores or completes:

- Avatar set/delete handlers and profile avatar-field visibility.
- Recovery request/verify/reset handlers.
- Email-change request/resend/verify handlers.
- Public profile and admin user-management handlers.
- Guarded test-only OTP and rate-limit reset hooks.
- Internal user-contact endpoint used by other services.

The restored handlers depend on:

| Dependency | Relationship |
|---|---|
| `src/services/user/models.py` | Already tracked clean; provides `avatar_object_key`, `OneTimeToken`, and `EmailChangeRequest` fields |
| `migrations/users/versions/20261022_0003_email_change_resend_columns.py` | Already tracked clean; provides `users_0003` resend columns and indexes |
| `src/services/user/schemas.py` | Requires `ChangeEmailRequest.password` |
| `src/services/user/config.py` | Provides fail-closed `test_endpoints_enabled` |
| `src/services/user/auth.py` | Delegates internal authentication to the shared verifier |
| `src/services/content/app.py` | Already tracked clean; provides internal avatar claim/release endpoints |
| `src/services/user/security.py` | Already tracked clean; provides password/OTP/session primitives |
| `src/services/user/database.py` | Database session manager and transaction boundaries |
| `tests/test_user_auth_lifecycle.py` | Focused recovery-token and email-change regression tests |
| `tests/test_security_hardening.py` and `tests/test_gateway.py` | Test-hook gating and public-route exclusion tests |

The User migration head is `users_0003`, and it must not be confused with the new Notification `notifications_0004`. The two service graphs are independent.

### 3.4 Backup/restore tooling, tests, and runbooks

The backup/restore work is a single coupled unit:

| Artifact | Coupled behavior |
|---|---|
| `scripts/backup/backup_databases.sh` | Writes `.partial` artifacts, verifies archive listings, generates `SHA256SUMS`, writes a completed manifest, and renames only after success |
| `scripts/backup/restore_databases.sh` | Requires `--backup-dir`, a `restore_` target prefix, an explicit environment, and exact destructive confirmation; verifies checksums before Docker/psql |
| `tests/test_backup_restore.py` | Assertions cover script behavior, failure paths, runbook commands, and the expected migration heads |
| `docs/operations/backup_restore_runbook.md` | Must describe the same flags, checksum format, and disposable target policy |
| `docs/operations/staging_deployment_runbook.md` | Must describe the current heads and must not recommend mutating migration commands as read-only checks |
| `docs/operations/incident_recovery_runbook.md` | Must describe the same internal-secret cutover semantics |

The backup test hard-codes expected heads `users_0003` and `notifications_0004`. It also rejects stale runbook examples such as the old positional restore invocation and the old `scripts/migrate.py current all` guidance. Splitting the scripts from the runbooks or from the test would create a checkout where the test suite and documentation disagree.

### 3.5 Outbox PostgreSQL test isolation

`tests/outbox_pg_isolation.py` defines the shared isolation policy:

- Explicit `TEST_*_DATABASE_URL` variables only.
- No fallback to application settings, `.env`, or live database names.
- Disposable names must match `test_*` or `stress_*`.
- Live names including `forum_*` and the six application databases are rejected.
- Malformed, empty, non-PostgreSQL, or unsafe values raise before a connection is opened.
- Cleanup is limited to caller-supplied `DELETE ... WHERE ...` statements; no table-wide cleanup helper.

Both `tests/test_outbox_concurrency_live.py` and `tests/test_outbox_hardening.py` import this helper. They also require `INTERNAL_SERVICE_SECRET` at import time, which depends on `tests/conftest.py`. The policy test in `tests/test_outbox_hardening.py` is offline and must remain runnable without any database. The live tests must skip when `TEST_CONTENT_DATABASE_URL`, `TEST_NOTIFICATIONS_DATABASE_URL`, or `TEST_SEARCH_DATABASE_URL` is absent.

### 3.6 Migration graph at the checkpoint

| Service | Revision chain | Repository head | Checkpoint relationship |
|---|---|---|---|
| Users | `users_0001` -> `users_0002` -> `users_0003` | `users_0003` | Baseline-tracked clean dependency |
| Content | `content_0001` -> `content_0002` -> `content_0003` | `content_0003` | Baseline-tracked clean |
| Interactions | `interactions_0001` -> `interactions_0002` | `interactions_0002` | Baseline-tracked clean |
| Moderation | `moderation_0001` -> `moderation_0002` -> `moderation_0003` | `moderation_0003` | Baseline-tracked clean |
| Notifications | `notifications_0001` -> `notifications_0002` -> `notifications_0003` -> `notifications_0004` | `notifications_0004` | `0004` is untracked and required; `0003` is baseline clean |
| Search | `search_0001` -> `search_0002` | `search_0002` | Baseline-tracked clean |

Each service has exactly one head. The heads must match the values asserted by `tests/test_backup_restore.py` and documented in the operational runbooks.

### 3.7 Documentation and evidence dependencies

- `docs/api/forum_api_endpoint_reference.md` and `docs/api/forum_api_workflow_guide.md` describe the source behavior and restored workflows. They have no runtime imports, but they are part of the intended documentation state.
- The 22 untracked audit reports preserve the evidence chain from Task 031.1 through Task 031.2.12. They are not imported by code or tests.
- This Task 031.2.13 report depends on the preceding audit evidence and records the checkpoint gate.
- Omitting audit evidence would not break the application, but it would make the checkpoint's provenance and rollback rationale incomplete.

---

## 4. Checkpoint Strategy

### 4.1 Recommendation: one coherent checkpoint

Use one coherent checkpoint containing all required source, configuration, migration, test, tooling, runbook, and evidence artifacts. Do not create category-ordered intermediate commits.

Reasons:

1. The internal-auth module is an import dependency for four receivers and the outbox worker.
2. The Notification migration, model, worker, configuration, Compose setting, and PostgreSQL tests are a single functional unit.
3. The User application restoration depends on tracked-clean model and migration files plus modified schemas, config, auth, and tests.
4. The backup scripts, backup test, and three runbooks cross-check each other's exact flags, heads, and safety behavior.
5. The outbox test policy and both outbox test modules depend on the new helper and the deterministic root test configuration.
6. The evidence reports describe the same worktree state. A partial intermediate commit would not match the evidence and could fail its own tests.

A single checkpoint also makes the clean-checkout verification meaningful: the verification target is the exact tree that contains all required dependencies.

### 4.2 Staged review batches inside the one checkpoint

These batches are for review and exact staging. They are not independent commits.

#### Batch A — Essential source, configuration, and migrations (19 paths)

- `.env.example`
- `.env.staging.example`
- `docker-compose.yml`
- `migrations/notifications/env.py`
- `migrations/notifications/versions/20261022_0004_email_delivery_lease.py`
- `scripts/run_benchmarks.py`
- `src/common/config.py`
- `src/common/internal_auth.py`
- `src/common/outbox_worker.py`
- `src/services/content/auth.py`
- `src/services/notification/auth.py`
- `src/services/notification/config.py`
- `src/services/notification/email_worker.py`
- `src/services/notification/models.py`
- `src/services/search/auth.py`
- `src/services/user/app.py`
- `src/services/user/auth.py`
- `src/services/user/config.py`
- `src/services/user/schemas.py`

#### Batch B — Corresponding tests and test harness (16 paths)

- `tests/conftest.py`
- `tests/outbox_pg_isolation.py`
- `tests/test_backup_restore.py`
- `tests/test_e2e_integration.py`
- `tests/test_email_dispatch_worker.py`
- `tests/test_email_worker_lease_pg.py`
- `tests/test_gateway.py`
- `tests/test_internal_service_auth.py`
- `tests/test_notification_service.py`
- `tests/test_outbox_concurrency_live.py`
- `tests/test_outbox_hardening.py`
- `tests/test_outbox_worker.py`
- `tests/test_search_service.py`
- `tests/test_security_hardening.py`
- `tests/test_staging_operational_readiness.py`
- `tests/test_user_auth_lifecycle.py`

#### Batch C — Backup/restore tooling and operational runbooks (5 paths)

- `scripts/backup/backup_databases.sh`
- `scripts/backup/restore_databases.sh`
- `docs/operations/backup_restore_runbook.md`
- `docs/operations/incident_recovery_runbook.md`
- `docs/operations/staging_deployment_runbook.md`

`tests/test_backup_restore.py` is placed in Batch B because it is a test-tree artifact, but its assertions are coupled to Batch C. Batch B and Batch C must land in the same checkpoint.

#### Batch D — API documentation, audit evidence, and this report (25 paths)

- `docs/api/forum_api_endpoint_reference.md`
- `docs/api/forum_api_workflow_guide.md`
- The 22 untracked `docs/audit/task_031_*.md` files enumerated in Section 2.3.
- `docs/audit/task_031_2_13_reproducible_source_checkpoint_report.md`

Batch D contains 2 API documents, 22 pre-existing audit reports, and this report.

The combined batches cover exactly:

- 34 tracked modifications.
- 30 pre-existing untracked files.
- 1 new Task 031.2.13 report.

### 4.3 Exact staging rules for the future authorized operation

Do not use any blanket staging command. Do not stage `backups/`, `.env`, `uploads/`, `.pytest_cache/`, `.ruff_cache/`, `__pycache__/`, `venv/`, `.jsonl` benchmark traces, or any other ignored artifact.

Use an explicit path list and verify it before committing:

```bash
git add -- <explicit checkpoint paths>
git diff --cached --name-status
git diff --cached --check
git status --short
```

Forbidden staging forms include:

- `git add .`
- `git add -A`
- `git add --all`
- `git commit -a`
- `git add -f` for ignored files

The three source-tree backup scripts are legitimate tracked executables. The historical `backups/` `.dump` files are not part of the checkpoint change set and must not be newly staged, copied, regenerated, or committed.

### 4.4 If a multi-commit split is later mandated

A feature-aligned split is safer than splitting by the four review batches above, but it is not the recommended default. If explicitly required, the only defensible order is:

1. Internal-auth foundation: shared verifier, config, Compose/env contract, receiver delegation, outbox header, benchmark secret, and corresponding auth/tests.
2. Notification lease: `notifications_0004`, model, worker, setting, metadata import, and lease/email tests.
3. User lifecycle restoration: restored app/schema/config plus recovery, email-change, avatar, gateway, and security tests.
4. Backup/restore: both scripts, all three runbooks, and the backup test.
5. API documentation and audit evidence.

Even this split requires each commit to include its tests and its runtime/migration dependencies. It is more error-prone than the single checkpoint because intermediate commits may not represent a fully verifiable service state.

---

## 5. Clean-Checkout Verification Plan

This plan is to be executed only after explicit authorization to create the checkpoint. It must not be executed against the current worktree during Task 031.2.13.

### 5.1 Preconditions

- The checkpoint commit exists on an authorized branch.
- The exact path manifest in this report is satisfied.
- No new `.env`, dump, upload, cache, or temporary artifact is staged.
- PostgreSQL verification targets are separately provisioned disposable databases with names matching `test_*`.
- No live/shared database, container, or production secret is used.

### 5.2 Commit and tree integrity

Run in a separate clone, not the current worktree:

```bash
git clone --no-hardlinks <repository-path> /tmp/opencode/forum-checkpoint-verify
cd /tmp/opencode/forum-checkpoint-verify
git checkout <checkpoint-commit>
git status --porcelain=v2 --branch
git diff --check HEAD^ HEAD
git diff-tree --no-commit-id --name-status -r -M HEAD
```

Expected results:

- Clean worktree with no untracked files.
- Exactly the manifest paths in the checkpoint diff.
- No renames, deletions, or unexpected mode changes.
- `git diff --check` exit code `0`.

### 5.3 Required-file presence

Verify every path from Sections 2.2, 2.3, and 2.4, plus the tracked-clean dependencies in Section 2.5. At minimum:

```bash
for path in \
  src/common/internal_auth.py \
  migrations/notifications/versions/20261022_0004_email_delivery_lease.py \
  tests/conftest.py \
  tests/outbox_pg_isolation.py \
  tests/test_email_worker_lease_pg.py \
  tests/test_internal_service_auth.py \
  src/services/user/models.py \
  migrations/users/versions/20261022_0003_email_change_resend_columns.py; do
  test -f "$path" || { echo "MISSING: $path"; exit 1; }
done
```

Also verify that all 34 modified paths and all 22 audit reports from the manifest are present and non-empty.

### 5.4 Migration graph consistency

Parse the revision files without connecting to a database. Confirm exactly these heads:

```text
users_0003
content_0003
interactions_0002
moderation_0003
notifications_0004
search_0002
```

Confirm `notifications_0004.down_revision == "notifications_0003"` and `users_0003.down_revision == "users_0002"`. Do not use `scripts/migrate.py current all` as a read-only check: `migrations/async_env.py` executes `CreateSchema(..., if_not_exists=True)` even during Alembic `current`.

### 5.5 Static checks

Run against the clean checkout:

```bash
ruff check . --no-cache
ruff format --check . --no-cache
pyright
```

Record the actual results. Do not treat prior task results as a substitute for the clean-checkout run.

### 5.6 Offline and unit verification

Use a non-empty dummy internal secret and a deterministic test environment. Do not print the secret. Keep all `TEST_*_DATABASE_URL` variables unset for this phase so PostgreSQL tests skip.

Run the focused offline set, including at least:

```bash
ENVIRONMENT=test \
INTERNAL_SERVICE_SECRET='<non-empty-dummy-secret-at-least-32-chars>' \
INTERNAL_SERVICE_AUTH_BYPASS=false \
pytest -q \
  tests/test_internal_service_auth.py \
  tests/test_backup_restore.py \
  tests/test_email_dispatch_worker.py \
  tests/test_gateway.py \
  tests/test_notification_service.py \
  tests/test_outbox_hardening.py \
  tests/test_outbox_worker.py \
  tests/test_search_service.py \
  tests/test_security_hardening.py \
  tests/test_user_auth_lifecycle.py
```

Expected behavior with no test database URLs:

- Offline auth, configuration, backup, gateway, security, email-worker, and outbox-isolation policy tests run.
- PostgreSQL-backed tests in `tests/test_outbox_hardening.py` skip.
- `tests/test_email_worker_lease_pg.py` skips.
- `tests/test_outbox_concurrency_live.py` skips.

Do not run the pre-existing tests that hard-code live database URLs as part of this plan. They are identified in Section 6.

### 5.7 Disposable PostgreSQL integration verification

Provision only explicitly named disposable databases, for example:

- `test_content_verify`
- `test_notifications_verify`
- `test_search_verify`

Apply the corresponding service migrations to those databases only. The Notification target must reach `notifications_0004`; Content must reach `content_0003`; Search must reach `search_0002`. Do not run migrations against `forum_*` databases.

Then run with explicit, disposable-only URLs:

```bash
ENVIRONMENT=test \
INTERNAL_SERVICE_SECRET='<non-empty-dummy-secret-at-least-32-chars>' \
INTERNAL_SERVICE_AUTH_BYPASS=false \
TEST_CONTENT_DATABASE_URL='<disposable-content-url>' \
TEST_NOTIFICATIONS_DATABASE_URL='<disposable-notifications-url>' \
TEST_SEARCH_DATABASE_URL='<disposable-search-url>' \
pytest -q \
  tests/test_outbox_concurrency_live.py \
  tests/test_outbox_hardening.py \
  tests/test_email_worker_lease_pg.py
```

The helper must reject any URL that is empty, malformed, non-PostgreSQL, names a `forum_*` database, or does not use a `test_*`/`stress_*` name. Record each test result and the exact migration head used for each disposable database.

The current User PostgreSQL concurrency tests do not support disposable `TEST_*` variables. They must not be run against hard-coded live targets. Their isolation is a separate remediation item, not part of this checkpoint preparation.

### 5.8 Compose configuration validation

Use a non-empty dummy secret. Do not start containers or pull images:

```bash
INTERNAL_SERVICE_SECRET='<non-empty-dummy-secret-at-least-32-chars>' \
ENVIRONMENT=test \
INTERNAL_SERVICE_AUTH_BYPASS=false \
docker compose -f docker-compose.yml config --quiet
```

Also inspect the rendered configuration without printing secret values:

- All six application services receive the canonical secret.
- No application service uses the legacy `INTERNAL_SERVICE_KEY`.
- Notification receives `EMAIL_WORKER_LEASE_TIMEOUT_SECONDS`.
- Notification depends on healthy Mailpit.
- `INTERNAL_SERVICE_AUTH_BYPASS` remains false.

`docker compose config` is configuration-only and does not start containers.

### 5.9 Artifact-hygiene verification

Before accepting the checkout, verify that the checkpoint does not add any of the following:

- `.env` or other environment files containing real values.
- New `.dump`, `.sql`, `.pem`, `.key`, or archive files.
- `uploads/` content.
- `.pytest_cache/`, `.ruff_cache/`, `__pycache__/`, or `venv/`.
- `.jsonl` benchmark traces.
- Temporary files or unrelated generated reports.

Use tree and path checks, not content dumps:

```bash
git ls-tree -r --name-only HEAD
git status --ignored --porcelain=v1 -uall
```

Classify any match rather than printing secret contents. The current checkout already contains two pre-existing tracked backup directories under `backups/` with 12 `.dump` files and two old manifests. They are not part of the 64 changed entries and are not new checkpoint content. Their presence in baseline history is a documented risk in Section 6 and requires separate disposition if a dump-free repository is required.

### 5.10 Independent-checkout conclusion

The verification passes only if:

- The clean clone is independent of the current worktree.
- Required files and migration heads are present.
- Ruff, Ruff format, and Pyright pass.
- Offline tests pass and PostgreSQL tests skip or pass against disposable databases only.
- Compose configuration validates with a dummy secret.
- No new secret, dump, upload, cache, or temporary artifact is in the checkpoint.
- The checkpoint tree matches this manifest.

Record the checkpoint commit hash, command results, test counts, migration heads, and any skipped or excluded tests in the final checkpoint record.

---

## 6. Unresolved Risks and Checks Not Completed

| Risk or limitation | Evidence | Impact | Required action before or during authorization |
|---|---|---|---|
| Pre-existing tracked backup artifacts | `HEAD` tracks `backups/20261021_083119Z/` and `backups/20261021_084751Z/`, each with six `.dump` files and an old manifest; no `SHA256SUMS` | A clean checkout is not dump-free even though the checkpoint adds no new backup artifacts; the artifacts are not valid under the new restore procedure | Do not use or newly commit them; make a separate approved decision about historical tracked backups before a dump-free release |
| Pre-existing tests hard-code live/local database targets | `tests/test_avatar_email_change_pg.py`, `tests/test_recovery_concurrency_pg.py`, `tests/test_concurrency_postgres.py`, `tests/test_attachment_concurrency_pg.py`, and `tests/test_browser_e2e.py` contain localhost/application database targets | Running the full PostgreSQL test suite could target live/shared databases | Exclude them from this checkpoint's verification or remediate their isolation in a separate approved task |
| No disposable PostgreSQL verification was run in this task | No `TEST_*_DATABASE_URL` is provisioned; no database was contacted | Lease, outbox, and other PostgreSQL integration behavior remains unverified here | Provision disposable databases only after authorization and follow Section 5.7 |
| No clean checkout exists yet | No checkpoint commit exists | The manifest is evidence-backed but not yet independently verified | Create and verify the checkpoint only after explicit authorization |
| No Compose configuration validation was run in this task | Task constraints prohibit container/config execution now | Compose interpolation and six-service wiring are source-inspected, not runtime-validated | Run `docker compose config` with a dummy secret in the future verification |
| Actual `.env` has no canonical key assignments | The local `.env` is 6 bytes and contains no recognized `KEY=value` assignment; no value was printed | Compose interpolation will require a protected secret and explicit environment outside the repository | Supply the secret and environment through protected deployment configuration; never commit `.env` |
| Existing tracked tests contain development credential literals | Baseline test files predate this checkpoint and were not modified by this task | A generic secret scan will flag pre-existing fixtures | Classify them as pre-existing test fixtures; do not add real credentials; plan separate test-fixture cleanup if required |
| No runtime/container or end-to-end API verification was performed | Task constraints prohibit container actions and live endpoint calls | Source and tests may pass while the deployed stack remains on an older contract | Perform a separate authorized deployment/cutover verification; this checkpoint does not authorize it |
| No CI job enforces the checkpoint gate | Repository inspection found no equivalent automated checkpoint workflow | Future drift is possible despite this report | Add a separate CI/checkpoint gate if desired; do not claim this report is a CI result |
| Audit evidence and API documents are untracked | All 24 pre-existing documentation evidence files are absent from `HEAD` | A clean checkout lacks the historical evidence and API reference unless included in the checkpoint | Include them in Batch D unless a later explicit decision narrows the evidence scope |

Checks that could not be completed during Task 031.2.13:

- Clean-checkout verification, because no checkpoint commit exists.
- PostgreSQL integration tests, because no disposable databases are configured.
- Compose configuration validation, because this task must not start or configure containers.
- Ruff, Ruff format, and Pyright on a clean checkout, because there is no checkpoint checkout; prior task evidence is not a substitute.
- Full application E2E and browser tests, because they require running services, containers, or browsers.
- Verification of the deployed six-service runtime, because no runtime inspection or container action was authorized.

---

## 7. Validation of This Report and Worktree Preservation

| Check | Result |
|---|---|
| Read-only Git inventory performed | Yes |
| `git diff --check` | Exit code `0` |
| Staged changes | 0 |
| Deleted or unmerged tracked paths | 0 |
| Renames or mode changes | 0 |
| All 34 modified paths present in baseline | Yes |
| All 30 pre-existing untracked paths absent from baseline | Yes |
| Modified/untracked path overlap | 0 |
| Source, test, migration, script, or configuration mutation by this task | None |
| Staging, commit, push, reset, clean, stash, or branch switch | None |
| Fetch or ref change | None |
| Database connection, migration, or query | None |
| Container build, start, stop, restart, or image action | None |
| Secret value displayed or reproduced | None |
| Markdown heading, table, and fenced-block validation | Pass: 1 H1, 8 H2, 26 H3, 4 H4; 20 balanced fences; 0 table-column mismatches |
| Manifest path coverage in this report | All 34 modified and all 30 pre-existing untracked paths found exactly |
| Secret-value scan of this report | No development credential literal or real test-secret literal present |

The report itself is the only new file created by this task. The pre-existing 34 tracked modifications and 30 untracked files remain preserved. After report creation, the expected worktree state is 34 tracked modifications and 31 untracked entries.

---

## 8. Final Gate Decision

**READY FOR EXPLICIT CHECKPOINT AUTHORIZATION**

This verdict means the required-artifact and dependency evidence is sufficiently complete to support a subsequent, explicitly authorized checkpoint operation. It does not authorize staging, committing, pushing, fetching, resetting, cleaning, stashing, switching branches, connecting to databases, running migrations, building containers, or starting Task 031.2.14.

Any authorization must:

1. Approve one coherent checkpoint or explicitly approve a feature-aligned multi-commit alternative.
2. Use exact paths from this manifest and never a blanket staging command.
3. Exclude `.env`, `backups/`, uploads, caches, virtual environments, and temporary files.
4. Preserve the pre-existing tracked backup artifacts unless a separate approved remediation is authorized.
5. Run the clean-checkout verification in Section 5 before declaring the checkpoint accepted.
