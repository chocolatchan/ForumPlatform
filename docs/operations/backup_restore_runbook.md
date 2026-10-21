# Operational Runbook: Database Backup and Restore

**Document Version:** 1.0.0  
**Scope:** PostgreSQL 16 multi-database architecture (`forum_users`, `forum_content`, `forum_interactions`, `forum_moderation`, `forum_notifications`, `forum_search`)  
**Target Environment:** Staging and Production  

---

## 1. Overview and Architecture

The Forum Platform adheres to **DEC-002: Service Data Isolation**. Six independent logical databases are hosted within the PostgreSQL container:
- `forum_users`
- `forum_content`
- `forum_interactions`
- `forum_moderation`
- `forum_notifications`
- `forum_search`

Backups are executed natively via PostgreSQL `pg_dump` with custom archive format (`-Fc`), enabling compression and selective/multi-threaded table restoration.

---

## 2. Backup Procedure

### 2.1 Automated Script Execution
Run the automated backup script from the project root:
```bash
./scripts/backup/backup_databases.sh [destination_directory]
```
If `destination_directory` is omitted, backups are saved to `./backups/<UTC_TIMESTAMP>/`.

### 2.2 Output Artifacts
Each backup execution creates a timestamped folder containing:
```
backups/20261021_120000Z/
├── forum_users.dump
├── forum_content.dump
├── forum_interactions.dump
├── forum_moderation.dump
├── forum_notifications.dump
├── forum_search.dump
└── backup_manifest.json
```

### 2.3 Verification Criteria
1. Script exits with return code `0`.
2. All 6 `.dump` files exist and are greater than 1,000 bytes.
3. `backup_manifest.json` indicates `"status": "completed"`.

---

## 3. Restore Procedure

### 3.1 Standard Full Restore
To restore all 6 databases from an existing backup folder:
```bash
./scripts/backup/restore_databases.sh <path_to_backup_directory>
```

### 3.2 Isolated Restore (Disaster Recovery Drill / Staging Validation)
To restore into an isolated environment without overwriting primary databases, provide a prefix:
```bash
./scripts/backup/restore_databases.sh ./backups/20261021_120000Z/ isolated_drill_
```
This restores the archives into:
- `isolated_drill_forum_users`
- `isolated_drill_forum_content`
- `isolated_drill_forum_interactions`
- `isolated_drill_forum_moderation`
- `isolated_drill_forum_notifications`
- `isolated_drill_forum_search`

### 3.3 Post-Restore Integrity Verification
After restore, verify database heads:
```bash
docker compose exec postgres psql -U forum_admin -d isol_forum_content -c "SELECT * FROM content.alembic_version_content;"
```
Expected output:
```
 version_num  
--------------
 content_0003
```

---

## 4. Retention Policy & Security

1. **Retention:** Daily backups retained for 14 days; weekly backups retained for 60 days.
2. **Secrets Handling:**
   - Passwords must **never** be passed via CLI arguments or embedded in scripts.
   - Script uses container internal trust or `PGPASSWORD` injected via container environment.
3. **Automated Verification Drill:**
   The automated test suite exercises a complete live backup and isolated restore drill via:
   ```bash
   ./venv/bin/pytest tests/test_backup_restore.py -v
   ```
