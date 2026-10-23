# Operational Runbook: Database Backup and Restore

**Document Version:** 1.1.0

**Scope:** PostgreSQL 16 multi-database architecture (`forum_users`, `forum_content`, `forum_interactions`, `forum_moderation`, `forum_notifications`, `forum_search`)

**Target Environment:** Staging and production operators; restore is restricted to explicitly named disposable `restore_*` databases.

---

## 1. Overview and Architecture

The Forum Platform adheres to **DEC-002: Service Data Isolation**. Six independent logical databases are hosted within the PostgreSQL container:

- `forum_users`
- `forum_content`
- `forum_interactions`
- `forum_moderation`
- `forum_notifications`
- `forum_search`

Backups are executed natively via PostgreSQL `pg_dump` with custom archive format (`-Fc`).

**Restore safety invariant:** this runbook does not permit a restore into any of the six live/shared databases above. The restore script requires an explicit disposable target prefix, rejects live names, verifies SHA-256 checksums before any destructive action, and requires exact confirmation for a destructive restore.

---

## 2. Backup Procedure

### 2.1 Automated Script Execution

Run the automated backup script from the project root:

```bash
./scripts/backup/backup_databases.sh [destination_directory]
```

If `destination_directory` is omitted, backups are staged under `./backups/<UTC_TIMESTAMP>.partial/` and the completed directory is `./backups/<UTC_TIMESTAMP>/`.

### 2.2 Output Artifacts

A completed backup directory contains:

```text
backups/20261021_120000Z/
├── forum_users.dump
├── forum_content.dump
├── forum_interactions.dump
├── forum_moderation.dump
├── forum_notifications.dump
├── forum_search.dump
├── SHA256SUMS
└── backup_manifest.json
```

The `.partial` directory and `INCOMPLETE` marker used during a backup are never valid restore sources. A completed directory is created only after every dump, archive listing, checksum generation, and checksum verification succeeds.

### 2.3 SHA-256 Checksum Format

`SHA256SUMS` uses the standard GNU `sha256sum` format and covers **exactly the six `.dump` artifacts**:

```text
<64 lowercase hex characters>  <database>.dump
```

Each artifact appears exactly once, with two spaces between the digest and the filename. The manifest and the checksum file itself are not checksummed by `SHA256SUMS`.

Generate the checksums only through the backup script. Verify a completed backup manually with:

```bash
cd /path/to/completed/backup
sha256sum -c --strict SHA256SUMS
```

### 2.4 Verification Criteria

1. Script exits with return code `0`.
2. All six `.dump` files exist, are larger than 100 bytes, and pass `pg_restore --list`.
3. `SHA256SUMS` contains exactly six well-formed entries.
4. `sha256sum -c --strict SHA256SUMS` reports all six files as `OK`.
5. `backup_manifest.json` contains `"status": "completed"` and `"checksum_algorithm": "sha256"`.

Backups created before this checksum format was introduced lack `SHA256SUMS` or the required manifest fields. The hardened restore script rejects them. Do not use such a backup for restore until it has been separately verified and checksummed through an approved process.

---

## 3. Restore Procedure

### 3.1 Preconditions

- A completed backup directory with six dumps, `SHA256SUMS`, and a completed manifest.
- An explicit recognized environment: `development`, `test`, `staging`, or `production`.
- An explicit disposable target prefix matching `^restore_[a-z0-9_]{1,29}_$`.
- Written approval for the destructive restore. The exact prefix must be supplied again as `--confirm-target`.

The restore script never defaults to live database names. An empty, missing, malformed, or non-`restore_` prefix fails before Docker or PostgreSQL is contacted. The six target names are always distinct from the live databases.

### 3.2 Verify the Backup and Target Without Mutating Anything

Use verify-only mode first. It validates the environment, target names, manifest, artifact names, and SHA-256 checksums, then exits without contacting Docker or PostgreSQL:

```bash
./scripts/backup/restore_databases.sh \
  --backup-dir /secure/backups/20261021_120000Z \
  --target-prefix restore_drill_20261023_ \
  --environment staging \
  --verify-only
```

Expected result: `VERIFICATION COMPLETE: no database or container was contacted or modified.`

If verification fails, stop. Do not proceed to a destructive restore.

### 3.3 Disposable Restore

After approval, the same target may be restored with an exact confirmation. This creates or replaces only the prefixed databases shown below; it never targets the live names:

```bash
./scripts/backup/restore_databases.sh \
  --backup-dir /secure/backups/20261021_120000Z \
  --target-prefix restore_drill_20261023_ \
  --environment staging \
  --confirm-target restore_drill_20261023_
```

For the example prefix, the targets are:

- `restore_drill_20261023_forum_users`
- `restore_drill_20261023_forum_content`
- `restore_drill_20261023_forum_interactions`
- `restore_drill_20261023_forum_moderation`
- `restore_drill_20261023_forum_notifications`
- `restore_drill_20261023_forum_search`

The script drops and recreates each target, then runs `pg_restore --no-owner --single-transaction --exit-on-error`. It does not suppress restore errors. Never substitute a live database name or remove the target/confirmation flags.

### 3.4 Post-Restore Integrity Verification

Verify the restored disposable databases, not the live databases. Example for one target:

```bash
docker compose exec -T postgres psql -U forum_admin \
  -d restore_drill_20261023_forum_content \
  -c "SELECT version_num FROM content.alembic_version_content;"
```

Expected revision heads for the current repository:

| Service | Read-only check database | Expected revision |
|---|---|---|
| users | `restore_drill_20261023_forum_users` | `users_0003` |
| content | `restore_drill_20261023_forum_content` | `content_0003` |
| interactions | `restore_drill_20261023_forum_interactions` | `interactions_0002` |
| moderation | `restore_drill_20261023_forum_moderation` | `moderation_0003` |
| notifications | `restore_drill_20261023_forum_notifications` | `notifications_0004` |
| search | `restore_drill_20261023_forum_search` | `search_0002` |

These are read-only `SELECT` statements. Do not use `scripts/migrate.py current` as a read-only verification command: `migrations/async_env.py` executes `CreateSchema(..., if_not_exists=True)` even during `current`, so it may issue DDL. Offline revision heads should be inspected from the migration files, not by connecting to a database.

### 3.5 Migration Commands That Mutate

- `scripts/migrate.py upgrade <service|all>` mutates the selected database(s).
- `scripts/migrate.py downgrade <service> <revision>` mutates the selected database.
- `scripts/migrate.py revision --autogenerate` may connect to a database.
- `scripts/migrate.py current` may create schemas through the shared Alembic environment.

None of these commands are part of backup/restore verification. Do not run migrations against a live/shared database as part of a restore drill. Use a separate approved migration procedure with an explicitly named environment and database.

---

## 4. Retention Policy and Security

1. **Retention:** Daily backups are retained for 14 days; weekly backups for 60 days.
2. **Secrets Handling:**
   - Passwords must never be passed via CLI arguments or embedded in scripts.
   - The scripts use the container-internal connection settings; do not print environment files or dumps.
3. **Artifact Protection:** Backup directories are created with restrictive permissions (`umask 077`). Store completed backups in approved encrypted storage.
4. **Automated Safety Tests:**
   ```bash
   ./venv/bin/pytest tests/test_backup_restore.py -v
   ```
   These tests use temporary artifacts and a fake `docker` executable. They validate checksum generation/verification, refusal paths, target validation, confirmation, and migration-head expectations without a live database.

No live backup or isolated restore rehearsal is performed by the automated test suite. A real isolated restore rehearsal requires a disposable PostgreSQL instance and an explicitly approved target; it is not claimed to have occurred by this runbook.
