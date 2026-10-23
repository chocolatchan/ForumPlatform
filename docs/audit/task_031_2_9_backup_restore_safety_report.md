# Task 031.2.9: Backup and Restore Safety Remediation Report

**Date:** 2026-10-23

**Scope:** Backup/restore target safety, SHA-256 integrity verification, migration-head/runbook corrections

**Status:** Completed; no live/shared database, container, or image was modified

---

## 1. Executive Summary

Task 031.2.9 removed the unsafe behavior that allowed `restore_databases.sh` to target the live application database names by default. Restore now requires:

- an explicit completed backup directory;
- an explicit disposable target prefix matching `^restore_[a-z0-9_]{1,29}_$`;
- an explicit recognized environment;
- an exact, non-empty `--confirm-target` for destructive restores;
- SHA-256 verification of all six `.dump` artifacts before any Docker or PostgreSQL action.

The backup script now stages artifacts in a `.partial` directory with an `INCOMPLETE` marker, verifies each archive with `pg_restore --list`, writes `SHA256SUMS`, verifies it, writes a completed manifest, and only then renames the directory to its completed name. The backup/restore tests were replaced with offline tests that use temporary artifacts and a fake `docker` executable.

The stale migration heads were corrected to the heads present in the current migration files. Both operational runbooks were updated to avoid `upgrade all`, `current all`, stale heads, and unprefixed restore commands.

**No actual database backup or restore was performed. No isolated restore rehearsal was performed. No live/shared database, running container, or image was changed.**

---

## 2. Initial Findings

The findings in `task_031_2_6_rebuild_readiness_and_rollback_plan.md` and `task_031_2_7_pre_rebuild_blocker_remediation_plan.md` were re-verified before editing:

1. **Restore target was optional and unsafe.**
   - `scripts/backup/restore_databases.sh` treated the backup path and target prefix as optional positional arguments.
   - An empty prefix caused the target names to be the live names (`forum_users`, `forum_content`, etc.).
   - The script dropped and recreated targets and ran `pg_restore --clean --if-exists`.
   - `pg_restore` stderr was suppressed, and a second restore attempt was used to hide errors.
   - There was no live-name denylist, environment validation, target confirmation, or verify-only mode.

2. **Backup integrity was not verifiable.**
   - `scripts/backup/backup_databases.sh` created the final timestamped directory before the backup completed.
   - Validation checked only that each dump exceeded 100 bytes and that the script exited zero.
   - No SHA-256 checksums or completed/partial distinction existed.
   - The 14 tracked files under `backups/` contain old manifests and no `SHA256SUMS`; they must not be treated as checksum-verified backups.

3. **The backup/restore test was a live-database integration test.**
   - `tests/test_backup_restore.py` used `DatabaseSessionManager` and live SQLAlchemy queries.
   - Its expected heads were stale: `users_0002` and `notifications_0002`.
   - It performed live backup and isolated restore operations, so it was not a safe focused test for refusal paths.

4. **Runbooks contained unsafe commands and stale assumptions.**
   - `docs/operations/backup_restore_runbook.md` showed unprefixed `restore_databases.sh <path_to_backup_directory>` and `isolated_drill_` restore commands.
   - `docs/operations/staging_deployment_runbook.md` showed `scripts/migrate.py upgrade all` and `scripts/migrate.py current all`, with stale heads `users_0002` and `notifications_0002`.
   - It also showed an unprefixed restore for rollback.

5. **`current` is not a read-only verification command.**
   - `migrations/async_env.py` executes `CreateSchema(..., if_not_exists=True)` during the Alembic environment setup.
   - Therefore `scripts/migrate.py current` can issue DDL and must not be presented as a read-only check.

6. **No existing safety checks covered these paths.**
   - There was no automated guard test for empty prefixes, live-name rejection, checksum failure, or confirmation.

---

## 3. Changes and Rationale

### 3.1 `scripts/backup/backup_databases.sh`

- Added `umask 077` so backup directories and artifacts are created with restrictive permissions.
- Dumps are written to `<timestamp>.partial` and a final `<timestamp>` directory is created only after all validation succeeds.
- Added an `INCOMPLETE` marker at the start of a backup run; it is removed only before the final rename. A failed run leaves a `.partial` directory with the marker.
- Kept the existing six-database scope exactly:
  - `forum_users`
  - `forum_content`
  - `forum_interactions`
  - `forum_moderation`
  - `forum_notifications`
  - `forum_search`
- Each dump must be larger than 100 bytes and pass `pg_restore --list`.
- Generates `SHA256SUMS` covering exactly the six `.dump` files and verifies it with `sha256sum -c --strict` before completing the backup.
- Writes `backup_manifest.json` only after checksum verification, with:
  - `"status": "completed"`
  - `"checksum_algorithm": "sha256"`
  - `"checksum_file": "SHA256SUMS"`
  - `"checksum_scope": "six .dump files only"`
- Renames the staging directory to the completed directory only after the manifest is written and the incomplete marker is removed.

### 3.2 `scripts/backup/restore_databases.sh`

- Replaced positional defaults with required explicit flags: `--backup-dir`, `--target-prefix`, and `--environment`.
- Added `--verify-only` for validation without Docker/PostgreSQL access.
- Added `--confirm-target`, which must exactly equal `--target-prefix` for destructive restores.
- Required target prefixes to match `^restore_[a-z0-9_]{1,29}_$`.
  - The prefix must start with `restore_`.
  - A trailing underscore is mandatory, preventing an ambiguous concatenation with the base database name.
  - Only lowercase letters, digits, and underscores are accepted.
  - Target names are checked against PostgreSQL’s 63-byte limit.
- Added a live/shared denylist covering the six application databases, `postgres`, `template0`, and `template1`.
- Accepted only `development`, `test`, `staging`, and `production` as recognized environments; missing or unknown values fail closed.
- Requires a completed manifest, six expected dump artifacts, and `SHA256SUMS` before any destructive action.
- Verifies the checksum file shape and checksums before any Docker or `psql` invocation.
- Rejects `.partial` directories and any directory containing an `INCOMPLETE` marker.
- Removed stderr suppression and removed the silent second restore attempt.
- Runs `pg_restore --no-owner --single-transaction --exit-on-error` after dropping and recreating each validated disposable target.
- Added a final target denylist re-check immediately before each destructive statement. There is no `--allow-live` or other bypass.

### 3.3 `tests/test_backup_restore.py`

Replaced the live integration test with 21 offline tests using temporary backup artifacts and a fake `docker` executable. The tests cover:

- backup checksum generation through the real backup script with a synthetic fake-docker dump;
- verify-only acceptance of a completed valid artifact;
- failed backup retained as `.partial` and rejected by restore;
- missing target;
- empty/live prefix;
- ambiguous or malformed prefix;
- unknown or empty environment;
- missing checksum;
- malformed checksum;
- checksum mismatch;
- refusal before any Docker call when validation fails;
- missing, empty, and mismatched destructive confirmation;
- offline migration-head expectations;
- runbook safety and current-head content.

The fake `docker` executable records any invocation and fails unexpected commands. Tests do not use `DatabaseSessionManager`, SQLAlchemy, a live PostgreSQL connection, or destructive SQL.

### 3.4 Runbooks

`docs/operations/backup_restore_runbook.md`:

- Documents the `.partial`/`INCOMPLETE` behavior and completed manifest.
- Defines the `SHA256SUMS` format and its exact scope.
- Documents checksum generation and manual verification.
- Requires verify-only validation before a disposable restore.
- Requires an explicit disposable `restore_*_` prefix and exact confirmation.
- Replaces live database restore instructions with disposable target instructions.
- Documents the current heads and read-only verification.
- Explicitly warns that `scripts/migrate.py current` may issue DDL through `migrations/async_env.py`.

`docs/operations/staging_deployment_runbook.md`:

- Names `staging` and the six target databases explicitly.
- Replaces `upgrade all` and `current all` with read-only verification guidance.
- Corrects heads to `users_0003`, `content_0003`, `interactions_0002`, `moderation_0003`, `notifications_0004`, and `search_0002`.
- Removes the unprefixed rollback restore command.
- Uses a verify-only restore command with an explicit disposable target prefix and `SHA256SUMS` validation.
- States that migration application is a separate mutating operation requiring explicit per-database targeting and approval.

---

## 4. Restore-Target Safety Design

### 4.1 Required invocation shape

```text
restore_databases.sh \
  --backup-dir <completed-backup-directory> \
  --target-prefix restore_<name>_ \
  --environment <development|test|staging|production> \
  --verify-only
```

For a destructive restore, `--verify-only` is omitted and:

```text
--confirm-target restore_<name>_
```

is required. The confirmation must be non-empty and exactly equal to `--target-prefix`.

### 4.2 Target construction and validation

For each base database, the target is:

```text
<target-prefix><base-database>
```

Example with `--target-prefix restore_drill_20261023_`:

- `restore_drill_20261023_forum_users`
- `restore_drill_20261023_forum_content`
- `restore_drill_20261023_forum_interactions`
- `restore_drill_20261023_forum_moderation`
- `restore_drill_20261023_forum_notifications`
- `restore_drill_20261023_forum_search`

The following fail before any destructive action:

- missing `--target-prefix`;
- empty `--target-prefix`;
- prefixes not beginning with `restore_`;
- prefixes without a trailing underscore;
- uppercase or hyphenated prefixes;
- target names longer than 63 bytes;
- any target equal to a live/shared name.

### 4.3 Environment and confirmation

- Missing or empty `--environment` fails.
- Unknown values such as `prod` or ` staging` fail.
- Destructive mode without `--confirm-target` fails.
- An empty or different `--confirm-target` fails.
- `--verify-only` does not require confirmation because it does not mutate anything.

### 4.4 Validation order

The restore script performs the following checks before any Docker/PostgreSQL action:

1. Parse and require explicit flags.
2. Validate environment.
3. Validate target-prefix shape.
4. Validate backup directory, `.partial`/`INCOMPLETE` state, manifest, and checksum file presence.
5. Validate every target name against live names and length limits.
6. Validate confirmation.
7. Validate checksum-file shape and artifact presence.
8. Run `sha256sum -c --strict`.

Only after all checks pass does the script enter `--verify-only` completion or the destructive loop. The destructive loop re-checks the target against the live denylist immediately before each `DROP DATABASE`.

---

## 5. Checksum Format and Verification Behavior

### 5.1 Artifact scope

`SHA256SUMS` covers **exactly the six `.dump` files**:

```text
forum_users.dump
forum_content.dump
forum_interactions.dump
forum_moderation.dump
forum_notifications.dump
forum_search.dump
```

It does not cover `backup_manifest.json`, the `SHA256SUMS` file itself, or any temporary file.

### 5.2 Format

The file uses the standard GNU `sha256sum` format:

```text
<64 lowercase hexadecimal characters><two spaces><database>.dump
```

Example shape:

```text
0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef  forum_users.dump
```

### 5.3 Generation

The backup script generates checksums only after all six dumps succeed and archive listings pass. It writes `SHA256SUMS` in the staging directory and immediately verifies it. A completed directory is not renamed into place until checksum verification and manifest creation succeed.

### 5.4 Restore verification

Restore fails closed when:

- `SHA256SUMS` is missing;
- `SHA256SUMS` is empty;
- it does not contain exactly six entries;
- an entry is malformed (wrong digest length/characters, wrong separator, or wrong filename);
- an expected dump is missing;
- a dump checksum does not match;
- the manifest does not declare `status=completed` and `checksum_algorithm=sha256`;
- the backup directory is `.partial` or contains `INCOMPLETE`.

The checksum output is suppressed and a generic failure is emitted, so backup contents are not printed. Verification happens before Docker, `psql`, `DROP DATABASE`, `CREATE DATABASE`, or `pg_restore`.

### 5.5 Partial-backup protection

- A failed backup remains at `<timestamp>.partial`.
- It retains the `INCOMPLETE` marker.
- It has no completed manifest.
- Restore rejects `.partial` directories and `INCOMPLETE` markers before checksum processing.

### 5.6 Existing backups

The 14 tracked backup artifacts under `backups/` predate this format and do not contain `SHA256SUMS` or the new manifest fields. The hardened restore script rejects them. No existing backup was modified, checksummed, or restored during this task.

---

## 6. Migration-Head Verification

Offline parsing of the revision files under `migrations/<service>/versions/` produced:

| Service | Expected head |
|---|---|
| users | `users_0003` |
| content | `content_0003` |
| interactions | `interactions_0002` |
| moderation | `moderation_0003` |
| notifications | `notifications_0004` |
| search | `search_0002` |

The test suite uses an AST-based offline check and asserts this exact mapping. No database was queried.

### 6.1 Offline versus database-mutating commands

- **Offline graph inspection:** reading `migrations/<service>/versions/*.py` and computing heads from `revision`/`down_revision`. This does not connect to a database.
- **Read-only database check:** explicit `psql -d <database> -c "SELECT version_num FROM <schema>.alembic_version_<schema>;"`. The runbooks use explicit database names and read-only `SELECT` statements.
- **Potentially mutating:**
  - `scripts/migrate.py upgrade <service|all>` mutates the selected database(s).
  - `scripts/migrate.py downgrade <service> <revision>` mutates the selected database.
  - `scripts/migrate.py revision --autogenerate` may connect to a database.
  - `scripts/migrate.py current` may issue DDL because the shared Alembic environment executes `CreateSchema(..., if_not_exists=True)` even for `current`.

The staging runbook no longer uses `current all` or `upgrade all`. It states that migration application is a separate mutating operation requiring explicit targeting and approval; no migrations were run during this task.

---

## 7. Tests and Static Analysis

### 7.1 Focused test results

```text
./venv/bin/pytest tests/test_backup_restore.py -q
21 passed
```

```text
./venv/bin/pytest tests/test_staging_operational_readiness.py -q
5 passed
```

The 21 backup/restore tests use temporary artifacts and a fake Docker executable. They exercise refusal paths and verify-only acceptance of a valid synthetic artifact. They do not execute destructive SQL or contact a database.

### 7.2 Shell and Python validation

```text
bash -n scripts/backup/backup_databases.sh
bash -n scripts/backup/restore_databases.sh
```

Both passed.

```text
./venv/bin/ruff check tests/test_backup_restore.py
All checks passed!

./venv/bin/ruff format --check tests/test_backup_restore.py
1 file already formatted

./venv/bin/pyright tests/test_backup_restore.py --pythonpath venv/bin/python
0 errors, 0 warnings, 0 informations

git diff --check
(no output)
```

### 7.3 Validation classification

- **Source/test validation:** Performed. Shell syntax, focused tests, Ruff, Pyright, formatting check, and diff whitespace check passed.
- **Actual backup integrity verification:** Not performed against a live database. The checksum generation and verification logic was exercised with synthetic dump artifacts and a fake Docker executable. No live `pg_dump` archive was checksum-verified or restored.
- **Actual isolated restore rehearsal:** Not performed. No disposable PostgreSQL instance was created, and the destructive restore branch was not executed against any database.

---

## 8. Remaining Risks

1. **No isolated restore rehearsal has been performed.** The script logic and refusal paths are tested, but a future approved rehearsal against a disposable PostgreSQL instance is still needed to validate actual `pg_dump`/`pg_restore` compatibility, role/ownership behavior, and single-transaction restore.
2. **Existing tracked backups are not checksum-verified.** They lack `SHA256SUMS` and the new manifest fields and are rejected by the hardened script until an approved verification/checksum process is applied.
3. **Checksums and manifest are not signed.** SHA-256 detects accidental corruption or mismatch, but an actor with write access to the backup directory could replace both the dump and the checksum. Access controls and trusted storage remain necessary.
4. **The restore script assumes the expected Compose service and database role.** It uses `postgres` and `forum_admin`; a different deployment topology would require review.
5. **`pg_restore --single-transaction` compatibility is not live-tested.** Some archive contents can be incompatible with single-transaction restore; that must be confirmed during the isolated rehearsal.
6. **`scripts/migrate.py current` remains potentially mutating.** This task corrected runbook usage but did not change the migration CLI or shared Alembic environment (out of scope).
7. **Required host tools.** Backup requires `sha256sum` and `jq`; restore requires `sha256sum`, `bash`, and the Docker Compose CLI. These prerequisites are not newly bundled.
8. **Environment validation is not a database-connection binding.** The primary safety boundary is the mandatory `restore_` target prefix plus the live-name denylist. The environment flag is an additional explicit guard and audit value.

---

## 9. Runtime and Safety Confirmation

- No live/shared database was connected to, queried, migrated, backed up, or restored.
- No `pg_dump`, `pg_restore`, `psql`, or migration command was executed against a real database.
- No real `docker` command was executed; tests used a fake `docker` executable in a temporary PATH.
- No container was built, retagged, started, stopped, recreated, or otherwise modified.
- No image was built, retagged, removed, or otherwise modified.
- No file under `backups/` was modified, added, or checksummed.
- No secrets, credentials, database connection strings, OTPs, or backup contents were printed or exposed.
- Existing tracked modifications and untracked artifacts were preserved. Nothing was staged, committed, reset, cleaned, or stashed.
- No work was performed on Task 031.2.10.

### Task 031.2.9 files

**Modified:**

- `scripts/backup/backup_databases.sh`
- `scripts/backup/restore_databases.sh`
- `tests/test_backup_restore.py`
- `docs/operations/backup_restore_runbook.md`
- `docs/operations/staging_deployment_runbook.md`

**New:**

- `docs/audit/task_031_2_9_backup_restore_safety_report.md`

No other file was intentionally changed for Task 031.2.9.

---

## 10. Acceptance Criteria Review

| Criterion | Result |
|---|---|
| Restore cannot proceed without an explicit safe target | Satisfied: `--target-prefix`, `--backup-dir`, and `--environment` are mandatory. |
| Live/shared targets are rejected by default | Satisfied: `restore_` prefix pattern and live-name denylist; empty/unsafe prefixes fail before Docker. |
| Checksum validation occurs before destructive restore actions | Satisfied: manifest and `SHA256SUMS` validation precedes all Docker/`psql` calls. |
| Missing or invalid checksums fail closed | Satisfied by code and focused tests. |
| Stale migration-head assumptions are corrected | Satisfied: tests and runbooks use `users_0003` and `notifications_0004`, with the other current heads. |
| Focused tests cover refusal paths and a valid isolated artifact | Satisfied: 21 offline tests, including synthetic valid artifact verification. |
| No live/shared database or runtime was modified | Confirmed above. |
