# Task 031.2.14 — Reproducible Source Checkpoint Report

**Date:** 2026-10-23  
**Task:** Create a reproducible source checkpoint from the approved Task 031.2.13 manifest  
**Mode:** Authorized staging and commit of verified in-scope paths; no push, fetch, branch switch, database connection, migration, or container/image operation  
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`  
**Branch:** `master`  
**Recorded baseline:** `bf3384c4c3b53b5f826ef119f2ad549997ff1d63`  
**Checkpoint commit:** `b38a693a7e140584cdf7eae0e1e3f67945cdb328`  
**Outcome:** **CHECKPOINT CREATED AND VERIFIED FROM AN INDEPENDENT CLEAN CLONE**

---

## 1. Outcome Summary

A single coherent checkpoint commit was created from the exact 65-path manifest approved in Task 031.2.13.

| Item | Result |
|---|---|
| Baseline `HEAD` before staging | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| Baseline branch | `master` |
| Tracked modifications before staging | 34 |
| Pre-existing untracked files | 30 |
| Task 031.2.13 report added to the manifest | 1 |
| Paths staged | 65 |
| Paths committed | 65 |
| Staged/committed path mismatch | 0 |
| New commit | `b38a693a7e140584cdf7eae0e1e3f67945cdb328` |
| Commit parent | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| Baseline remains an ancestor | Yes |
| Tracked modifications remaining | 0 |
| Untracked files remaining after commit | 1 (this report) |
| Push performed | No |
| Database connection, migration, or query | No |
| Container start, build, load, retag, or restart | No |

The checkpoint contains the internal-service authentication foundation, the Notification lease migration and worker, restored User Service lifecycle handlers, backup/restore safety tooling and runbooks, outbox PostgreSQL test isolation, API documentation, and the Task 031.1 through Task 031.2.13 evidence chain.

---

## 2. Preflight Baseline and Worktree State

### 2.1 Repository state before staging

| Check | Observed value |
|---|---|
| `git rev-parse HEAD` | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| Branch | `master` |
| Baseline subject | `feat(tasks 028-031): account recovery, avatar & email-change, outbound email dispatch` |
| `origin/master` at inspection | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| Ahead/behind before commit | `+0 -0` |
| Tracked modifications | 34 |
| Untracked paths | 31 (30 pre-existing plus the Task 031.2.13 report) |
| Staged paths before this task | 0 |
| Deleted tracked paths | 0 |
| Unmerged paths | 0 |
| Renames | 0 |
| File-mode changes | 0 |
| Stashes | 0 |
| Worktrees | 1 |
| `git diff --check` | Exit code `0` |

The preflight state matched the Task 031.2.13 report exactly. No unexpected change had appeared since that inventory, so staging was permitted.

### 2.2 Preflight commands used

The following read-only inspections were performed before any staging:

```text
git status --short
git status --porcelain=v1 -uall
git diff --stat
git diff --name-status
git diff --cached --name-status
git ls-files --others --exclude-standard
git rev-parse HEAD
git branch --show-current
```

No fetch, pull, branch switch, reset, clean, stash, rebase, or amend was performed.

---

## 3. Manifest Reconciliation Summary

Every candidate path was reconciled against the approved Task 031.2.13 manifest and classified. No unrelated or uncertain artifact was staged.

| Class | Paths | Disposition |
|---|---:|---|
| Required implementation | 11 | Included |
| Required configuration | 5 | Included |
| Required migration | 2 | Included |
| Required test | 16 | Included |
| Required operational tooling | 3 | Included |
| Required documentation | 5 | Included |
| Audit evidence | 23 | Included |
| Unrelated or uncertain artifact | 0 | None staged |

The combined commit contains:

- 34 modified tracked paths.
- 31 newly added paths.
- 0 deletions.
- 0 renames.
- 0 mode changes.

No path was included merely because it was untracked. Each added path was required by an import, migration chain, test dependency, operational contract, or approved evidence manifest.

---

## 4. Commit Boundaries and Included Path Inventory

### 4.1 Decision: one coherent checkpoint

One coherent commit was used instead of dependency-ordered intermediate commits. The Task 031.2.13 dependency analysis established that the work is cross-coupled:

1. `src/common/internal_auth.py` is imported by four service receivers and the outbox worker.
2. The Notification migration, model, worker, configuration, Compose setting, and PostgreSQL tests form one functional unit.
3. The restored User handlers depend on tracked-clean models, migrations, security helpers, schemas, config, and tests.
4. The backup scripts, backup test, and three runbooks cross-check each other’s flags, checksum contract, and migration heads.
5. Both outbox test modules depend on the isolation helper and the deterministic root test configuration.
6. The audit evidence describes this exact worktree state; a partial commit would not match its own evidence.

A single commit keeps the checkpoint buildable and makes the clean-clone verification target unambiguous.

### 4.2 Batch A — Essential source, configuration, and migrations (19 paths)

| # | Exact path | Git change | Classification |
|---:|---|---|---|
| 1 | `.env.example` | Modified | Required configuration |
| 2 | `.env.staging.example` | Modified | Required configuration |
| 3 | `docker-compose.yml` | Modified | Required configuration |
| 4 | `migrations/notifications/env.py` | Modified | Required migration support |
| 5 | `migrations/notifications/versions/20261022_0004_email_delivery_lease.py` | Added | Required migration |
| 6 | `scripts/run_benchmarks.py` | Modified | Required tooling |
| 7 | `src/common/config.py` | Modified | Required implementation |
| 8 | `src/common/internal_auth.py` | Added | Required implementation |
| 9 | `src/common/outbox_worker.py` | Modified | Required implementation |
| 10 | `src/services/content/auth.py` | Modified | Required implementation |
| 11 | `src/services/notification/auth.py` | Modified | Required implementation |
| 12 | `src/services/notification/config.py` | Modified | Required configuration |
| 13 | `src/services/notification/email_worker.py` | Modified | Required implementation |
| 14 | `src/services/notification/models.py` | Modified | Required implementation |
| 15 | `src/services/search/auth.py` | Modified | Required implementation |
| 16 | `src/services/user/app.py` | Modified | Required implementation |
| 17 | `src/services/user/auth.py` | Modified | Required implementation |
| 18 | `src/services/user/config.py` | Modified | Required configuration |
| 19 | `src/services/user/schemas.py` | Modified | Required implementation |

### 4.3 Batch B — Corresponding tests and test harness (16 paths)

| # | Exact path | Git change | Classification |
|---:|---|---|---|
| 20 | `tests/conftest.py` | Added | Required test harness |
| 21 | `tests/outbox_pg_isolation.py` | Added | Required test harness |
| 22 | `tests/test_backup_restore.py` | Modified | Required test |
| 23 | `tests/test_e2e_integration.py` | Modified | Required test |
| 24 | `tests/test_email_dispatch_worker.py` | Modified | Required test |
| 25 | `tests/test_email_worker_lease_pg.py` | Added | Required test |
| 26 | `tests/test_gateway.py` | Modified | Required test |
| 27 | `tests/test_internal_service_auth.py` | Added | Required test |
| 28 | `tests/test_notification_service.py` | Modified | Required test |
| 29 | `tests/test_outbox_concurrency_live.py` | Modified | Required test |
| 30 | `tests/test_outbox_hardening.py` | Modified | Required test |
| 31 | `tests/test_outbox_worker.py` | Modified | Required test |
| 32 | `tests/test_search_service.py` | Modified | Required test |
| 33 | `tests/test_security_hardening.py` | Modified | Required test |
| 34 | `tests/test_staging_operational_readiness.py` | Modified | Required test |
| 35 | `tests/test_user_auth_lifecycle.py` | Modified | Required test |

`tests/test_backup_restore.py` is in the test batch, but its assertions are coupled to the Batch C scripts and runbooks. Both batches are in the same commit.

### 4.4 Batch C — Backup/restore tooling and operational runbooks (5 paths)

| # | Exact path | Git change | Classification |
|---:|---|---|---|
| 36 | `docs/operations/backup_restore_runbook.md` | Modified | Required documentation |
| 37 | `docs/operations/incident_recovery_runbook.md` | Modified | Required documentation |
| 38 | `docs/operations/staging_deployment_runbook.md` | Modified | Required documentation |
| 39 | `scripts/backup/backup_databases.sh` | Modified | Required operational tooling |
| 40 | `scripts/backup/restore_databases.sh` | Modified | Required operational tooling |

### 4.5 Batch D — API documentation and audit evidence (25 paths)

| # | Exact path | Git change | Classification |
|---:|---|---|---|
| 41 | `docs/api/forum_api_endpoint_reference.md` | Added | Required documentation |
| 42 | `docs/api/forum_api_workflow_guide.md` | Added | Required documentation |
| 43 | `docs/audit/task_031_1_change_log.md` | Added | Audit evidence |
| 44 | `docs/audit/task_031_1_design.md` | Added | Audit evidence |
| 45 | `docs/audit/task_031_1_validation_report.md` | Added | Audit evidence |
| 46 | `docs/audit/task_031_2_1_inventory_report.md` | Added | Audit evidence |
| 47 | `docs/audit/task_031_2_2_discrepancy_report.md` | Added | Audit evidence |
| 48 | `docs/audit/task_031_2_3a_image_preservation_report.md` | Added | Audit evidence |
| 49 | `docs/audit/task_031_2_3b_restoration_map.md` | Added | Audit evidence |
| 50 | `docs/audit/task_031_2_3c_recovery_restoration_report.md` | Added | Audit evidence |
| 51 | `docs/audit/task_031_2_3d_email_change_restoration_report.md` | Added | Audit evidence |
| 52 | `docs/audit/task_031_2_3e_avatar_restoration_report.md` | Added | Audit evidence |
| 53 | `docs/audit/task_031_2_3f_email_change_schema_report.md` | Added | Audit evidence |
| 54 | `docs/audit/task_031_2_4_integration_preflight_report.md` | Added | Audit evidence |
| 55 | `docs/audit/task_031_2_5a_recovery_token_integrity_report.md` | Added | Audit evidence |
| 56 | `docs/audit/task_031_2_5b_migration_chain_and_checkout_report.md` | Added | Audit evidence |
| 57 | `docs/audit/task_031_2_5c_internal_service_auth_report.md` | Added | Audit evidence |
| 58 | `docs/audit/task_031_2_6_rebuild_readiness_and_rollback_plan.md` | Added | Audit evidence |
| 59 | `docs/audit/task_031_2_7_pre_rebuild_blocker_remediation_plan.md` | Added | Audit evidence |
| 60 | `docs/audit/task_031_2_8_staging_security_guard_report.md` | Added | Audit evidence |
| 61 | `docs/audit/task_031_2_9_backup_restore_safety_report.md` | Added | Audit evidence |
| 62 | `docs/audit/task_031_2_10_internal_auth_deployment_readiness_report.md` | Added | Audit evidence |
| 63 | `docs/audit/task_031_2_11_rebuild_checkpoint_and_artifact_integrity_report.md` | Added | Audit evidence |
| 64 | `docs/audit/task_031_2_12_outbox_test_isolation_report.md` | Added | Audit evidence |
| 65 | `docs/audit/task_031_2_13_reproducible_source_checkpoint_report.md` | Added | Audit evidence |

### 4.6 Staging method

Staging used an explicit 65-path list, not a blanket command:

```text
git add --pathspec-from-file=<verified-65-path-list> --pathspec-file-nul
```

The staged path set was compared programmatically with the manifest:

| Check | Result |
|---|---|
| Manifest paths | 65 |
| Staged paths | 65 |
| Missing from stage | 0 |
| Extra in stage | 0 |
| Duplicate paths | 0 |
| `git add .`, `git add -A`, `git add --all`, or `git commit -a` | Not used |
| Ignored files staged | 0 |
| Secrets staged | 0 |
| Database dumps staged | 0 |

---

## 5. Commit Identity and History Integrity

### 5.1 Commit record

| Field | Value |
|---|---|
| Commit | `b38a693a7e140584cdf7eae0e1e3f67945cdb328` |
| Parent | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| Subject | `chore(tasks 031.2.13-031.2.14): checkpoint verified source state` |
| Author date | `2026-10-23T19:07:53+07:00` |
| Files changed | 65 |
| Insertions | 15,567 |
| Deletions | 846 |
| Deletions or renames | 0 |
| File-mode changes | 0 |

### 5.2 History checks

| Check | Result |
|---|---|
| `git rev-parse HEAD^` | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| `git merge-base --is-ancestor <baseline> HEAD` | Exit code `0` |
| Commit path count | 65 |
| `origin/master...HEAD` after commit | `0 1` (one commit ahead, none behind) |
| Amend, rebase, squash, or history rewrite | None |
| Tag created or modified | None |
| Push performed | None |

The checkpoint is a direct child of the recorded baseline. The baseline remains an ancestor.

---

## 6. Validation Results

### 6.1 Pre-commit validation on the worktree

| Check | Result |
|---|---|
| `git diff --check` before staging | Exit `0` |
| `git diff --cached --check` | Exit `2`; trailing whitespace only in six existing audit Markdown reports |
| `git diff --cached --check` excluding `docs/audit/*.md` | Exit `0` |
| Ruff lint on the 32 changed Python paths | Pass: `All checks passed!` |
| Ruff format check on the 32 changed Python paths | 10 files would be reformatted; no file was modified |
| Pyright with the project virtual-environment interpreter | Pass: `0 errors, 0 warnings, 0 informations` |
| Migration graph parse | Exactly one head per service |
| Compose static configuration | Exit `0` |
| Focused offline tests | 182 passed, 18 skipped |
| Secret scan of staged additions | No real credential introduced |
| Strong-pattern credential scan | Only documented local/example and test-fixture URLs found |

The `git diff --cached --check` whitespace notices concern Markdown two-space hard line breaks in these pre-existing evidence files:

```text
docs/audit/task_031_2_13_reproducible_source_checkpoint_report.md
docs/audit/task_031_2_2_discrepancy_report.md
docs/audit/task_031_2_5c_internal_service_auth_report.md
docs/audit/task_031_2_6_rebuild_readiness_and_rollback_plan.md
docs/audit/task_031_2_7_pre_rebuild_blocker_remediation_plan.md
docs/audit/task_031_2_8_staging_security_guard_report.md
```

No source, test, migration, script, Compose, or runbook path has a whitespace error. The evidence files were not rewritten merely to suppress the notice.

### 6.2 Migration graph

Parsed from revision files without connecting to a database:

| Service | Head |
|---|---|
| Content | `content_0003` |
| Interactions | `interactions_0002` |
| Moderation | `moderation_0003` |
| Notifications | `notifications_0004` |
| Search | `search_0002` |
| Users | `users_0003` |

`notifications_0004` has `down_revision = "notifications_0003"`. Each service has exactly one head.

### 6.3 Compose configuration validation

`docker compose -f docker-compose.yml config --quiet` completed with exit code `0` using a non-empty dummy internal-service secret and `INTERNAL_SERVICE_AUTH_BYPASS=false`. The command validated configuration only. No container was created, started, stopped, restarted, built, loaded, retagged, or removed.

### 6.4 Focused offline tests

Executed in the original worktree and repeated in the independent clone:

```text
tests/test_internal_service_auth.py
tests/test_backup_restore.py
tests/test_email_dispatch_worker.py
tests/test_gateway.py
tests/test_notification_service.py
tests/test_outbox_hardening.py
tests/test_outbox_worker.py
tests/test_search_service.py
tests/test_security_hardening.py
tests/test_user_auth_lifecycle.py
tests/test_email_worker_lease_pg.py
```

Result in both locations:

```text
182 passed, 18 skipped
```

The 18 skips were database-gated:

- 6 outbox tests skipped because `TEST_NOTIFICATIONS_DATABASE_URL`, `TEST_SEARCH_DATABASE_URL`, or `TEST_CONTENT_DATABASE_URL` was not set.
- 12 Notification lease tests skipped because `TEST_NOTIFICATIONS_DATABASE_URL` was not set.

The skip messages explicitly state that the tests refuse to fall back to the development `forum_notifications` database.

### 6.5 Independent clean-clone verification

A separate clone was created at:

```text
/tmp/opencode/forum-checkpoint-verify-b38a693
```

It was cloned with `--no-hardlinks`, checked out at `b38a693a7e140584cdf7eae0e1e3f67945cdb328`, and did not modify the user worktree.

| Check | Result |
|---|---|
| Clone `HEAD` | `b38a693a7e140584cdf7eae0e1e3f67945cdb328` |
| Clone worktree | Clean, detached HEAD |
| Clone commit path count | 65 |
| Required files present and non-empty | Pass |
| Migration heads | The six expected heads |
| Ruff lint on changed Python paths | Pass |
| Pyright | Pass: 0 errors |
| Focused tests | 182 passed, 18 skipped |
| Compose static validation | Exit `0` |
| Tracked `.env` file | None |
| Tracked private-key files | None |
| Tracked cache, virtual-environment, or `.jsonl` files | None |
| Tracked backup dump/archive files | 14 pre-existing baseline files under `backups/`; none added by this checkpoint |

The clean checkout was therefore tested independently, but only for static checks, offline tests, migration metadata, and Compose configuration. PostgreSQL integration was not executed.

---

## 7. Excluded Files and Remaining Worktree State

### 7.1 Excluded paths and reasons

| Excluded path or class | Reason |
|---|---|
| `.env` | Local environment file; ignored; may contain protected values; never staged |
| `backups/` contents | Pre-existing tracked backup artifacts from the baseline; not modified or newly staged |
| `.pytest_cache/`, `.ruff_cache/`, `__pycache__/` | Generated caches; ignored |
| `venv/` | Local virtual environment; ignored |
| `uploads/` | Local upload storage; ignored |
| `*.jsonl` benchmark traces | Generated local traces; ignored |
| Temporary files and verification clones under `/tmp/opencode/` | Outside the repository and not part of the checkpoint |

No excluded user file was deleted, moved, renamed, cleaned, or regenerated.

### 7.2 Remaining worktree after commit

Before writing this report:

```text
git status --short
```

produced no output. After this report is written, the expected remaining state is:

| State | Count |
|---|---|
| Tracked modifications | 0 |
| Staged changes | 0 |
| Untracked files | 1 (`docs/audit/task_031_2_14_reproducible_source_checkpoint_report.md`) |
| Ignored files | Unchanged local `.env`, caches, and virtual environment |

This report is the Task 031.2.14 deliverable. It is intentionally created after the checkpoint commit so that the commit hash and verification results are accurate. It was not added to the checkpoint commit.

---

## 8. Sensitive-Artifact Review

| Review item | Result |
|---|---|
| Real API keys, passwords, private tokens, or private keys added | None found |
| `.env` or real environment file staged | No |
| Database dumps or archives newly staged | No |
| Temporary files, logs, caches, generated binaries, or virtual environments staged | No |
| `.env.example` | Contains placeholders and documented example values only |
| `.env.staging.example` | Contains placeholders and documented example values only |
| Test secrets | Explicit dummy test values only |
| Local-only configuration | Not staged |
| `.gitignore` modified to hide unexplained files | No |
| Secret values printed in this report | No |

The strong-pattern scan found only documented local/example database URLs and test-fixture URLs. No production credential was identified. The local `.env` was not read for value inspection beyond confirming that it remains ignored and unstaged.

---

## 9. Safety Confirmations and Limitations

### 9.1 Safety confirmations

| Constraint | Confirmation |
|---|---|
| Push | Not performed |
| Fetch or pull | Not performed |
| Branch switch | Not performed |
| Reset, clean, stash, rebase, amend | Not performed |
| Database connection or query | Not performed |
| Migration execution | Not performed |
| Container build, load, retag, start, stop, restart, or removal | Not performed |
| Live endpoint request | Not performed |
| Deployment configuration change outside reviewed scope | None |
| Deletion or cleanup of excluded worktree files | None |
| Secret displayed | None |
| Task 031.2.15 started | No |

`docker compose config` was used only for static configuration validation; it does not start containers.

### 9.2 Validation limitations

- PostgreSQL integration tests were not executed. No disposable `TEST_*` database was provisioned, and the task forbids database connections and migrations. The 18 skipped tests remain unverified against PostgreSQL.
- The checkpoint proves source presence and offline behavior, not runtime integration, deployment readiness, or rollback readiness.
- Ruff format check is not clean for 10 of the 32 changed Python files and for additional pre-existing files. No formatting was applied, because that would modify preserved user work beyond the checkpoint scope. The project’s configured lint gate (`ruff check`) passes on all changed Python paths.
- Trailing-whitespace notices remain for six audit Markdown files because of intentional two-space hard line breaks. They are documentation-only and no affected file was rewritten.
- The baseline already tracks 14 backup dump/manifest files under `backups/`. The checkpoint adds none, but a dump-free repository still requires a separate approved remediation.
- Pre-existing tests that hard-code live/local database targets were not run and remain a separate isolation concern.

---

## 10. Final Record

The reproducible source checkpoint was created as one coherent commit:

```text
b38a693a7e140584cdf7eae0e1e3f67945cdb328
```

Its parent and recorded baseline is:

```text
bf3384c4c3b53b5f826ef119f2ad549997ff1d63
```

All 65 approved manifest paths are present in Git history. The baseline remains an ancestor. The clean clone verified file presence, the six migration heads, changed-file Ruff lint, Pyright, 182 focused offline tests with 18 expected database-gated skips, and Compose static configuration.

No push, database operation, migration, or container operation was performed. Task 031.2.15 was not started.
