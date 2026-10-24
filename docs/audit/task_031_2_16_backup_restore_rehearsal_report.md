# Task 031.2.16: Isolated Backup/Restore Rehearsal and Recovery Verification Report

**Date:** 2026-10-23  
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`  
**Baseline commit:** `b38a693a7e140584cdf7eae0e1e3f67945cdb328` (`HEAD`, branch `master`)  
**Scope:** Verification of backup artifacts, hardened backup/restore scripts, isolated restore rehearsal, and post-restore data integrity validation  
**Final Gate Decision:** **PASS — ISOLATED RESTORE REHEARSAL VERIFIED**

---

## 1. Executive Summary

Task 031.2.16 proved the end-to-end viability and safety of the ForumPlatform database backup and restore system. By provisioning a dedicated, isolated PostgreSQL 16 instance and generating deterministic, non-sensitive fixture data across all six microservices, this rehearsal demonstrated actual dump creation, SHA-256 integrity generation, strict verification refusal mechanics, clean restoration into disposable prefixed databases, and complete schema/fixture recovery.

### Key Highlights:
1. **Zero Impact on Production/Development Databases:**
   - The application database container (`forum_postgres`), networks, volumes, and running application containers were completely untouched.
   - The Task 031.2.15 integration test container (`forum_test_postgres_t031_2_15`) remained stopped and unchanged.
   - A dedicated rehearsal container (`forum_rehearsal_postgres_t031_2_16`) was spun up on isolated loopback port `127.0.0.1:5434` with isolated volume `rehearsal_postgres_data_t031_2_16` and network `rehearsal_network_t031_2_16`.
2. **Current-Format Backup Generated & Verified:**
   - A complete 6-database backup was produced following the hardened script standard: `forum_users.dump`, `forum_content.dump`, `forum_interactions.dump`, `forum_moderation.dump`, `forum_notifications.dump`, `forum_search.dump`.
   - All archives passed `pg_restore --list`.
   - `SHA256SUMS` and `backup_manifest.json` (declaring `status: completed` and `checksum_algorithm: sha256`) were produced and strictly validated with `sha256sum -c --strict`.
3. **Restoration into Disposable Targets:**
   - Restored into `restore_rehearsal_20261023_` targets using the single-transaction, exit-on-error guarantees of `restore_databases.sh`.
   - Verified that `verify-only` mode passed without database mutation.
4. **Post-Restore Integrity & Relational Verification:**
   - All 6 target databases restored with 0 errors.
   - All 6 migration revision heads verified:
     - `users`: `users_0003`
     - `content`: `content_0003`
     - `interactions`: `interactions_0002`
     - `moderation`: `moderation_0003`
     - `notifications`: `notifications_0004`
     - `search`: `search_0002`
   - Verified foreign key and cross-entity relationships on restored fixture rows (e.g. Content Category & Thread join, Interaction Votes matching Thread ID, Moderation Report referencing Thread, Search Document indexing Thread).

---

## 2. Preflight and Worktree Preservation

- **Git HEAD:** `b38a693a7e140584cdf7eae0e1e3f67945cdb328`
- **Branch:** `master`
- **Preserved Uncommitted Artifacts:**
  - `docs/audit/task_031_2_14_reproducible_source_checkpoint_report.md`
  - `docs/audit/task_031_2_15_postgres_integration_verification_report.md`
  - `docker-compose.test-pg.yml` (Task 031.2.15 test container specification)
- **Application Services Status:** All 10 live containers (`forum_postgres`, `forum_redis`, `forum_mailpit`, 7 microservices/gateway) healthy on Docker.

---

## 3. Read-Only Backup Inventory

An audit of the repository's `backups/` directory and scripts revealed:

1. **Legacy Backups (`backups/20261021_083119Z` and `backups/20261021_084751Z`):**
   - Each contains six `.dump` files and an early version of `backup_manifest.json`.
   - **Missing:** Neither contains `SHA256SUMS`.
   - **Manifest Discrepancy:** The manifests lack `"checksum_algorithm": "sha256"`.
   - **Hardened Script Refusal:** Running `scripts/backup/restore_databases.sh --backup-dir backups/20261021_084751Z --target-prefix restore_test_ --environment test --verify-only` immediately exits with code 1:
     ```text
     ERROR: Backup manifest does not declare sha256 checksums
     ```
   - **Conclusion:** As established in Task 031.2.9, legacy backups are correctly rejected by the hardened restore script fail-closed safety policy.

2. **Hardened Script Safeguards:**
   - `scripts/backup/backup_databases.sh`:
     - Staged in `.partial` with `INCOMPLETE` marker.
     - Validates archive listing with `pg_restore --list`.
     - Computes and verifies `SHA256SUMS` over exactly the six `.dump` files.
     - Writes manifest with `checksum_algorithm: sha256` and `status: completed`.
     - Renames `.partial` only on total success.
   - `scripts/backup/restore_databases.sh`:
     - Rejects any restore from an `INCOMPLETE` directory.
     - Enforces target prefix regex `^restore_[a-z0-9_]{1,29}_$`.
     - Strict denylist against live database names (`forum_*`, `postgres`, `template*`).
     - Requires `--environment` from `development`, `test`, `staging`, `production`.
     - Requires `--confirm-target` matching `--target-prefix` exactly for destructive execution.
     - Strict SHA-256 pre-verification of all six dumps before any Docker/PostgreSQL connection.

---

## 4. Rehearsal Target Architecture

To guarantee absolute isolation from both the live application database and the integration test database, a third dedicated environment was specified in `docker-compose.rehearsal.yml`:

```yaml
services:
  rehearsal-postgres:
    image: postgres:16-alpine
    container_name: forum_rehearsal_postgres_t031_2_16
    restart: "no"
    environment:
      POSTGRES_USER: rehearsal_admin
      POSTGRES_PASSWORD: rehearsal_secure_password_1122!
      POSTGRES_DB: rehearsal_default
    ports:
      - "127.0.0.1:5434:5432"
    volumes:
      - rehearsal_postgres_data_t031_2_16:/var/lib/postgresql/data
    networks:
      - rehearsal_network_t031_2_16
```

### Isolation Proof:
- **Container:** `forum_rehearsal_postgres_t031_2_16`
- **Network:** `rehearsal_network_t031_2_16` (bridge, isolated from application bridge `forumplatform_forum_network`)
- **Volume:** `rehearsal_postgres_data_t031_2_16` (isolated from `forumplatform_postgres_data` and `test_postgres_data_t031_2_15`)
- **Port:** Loopback port `127.0.0.1:5434` (separate from app `5432` and integration `5433`).

---

## 5. Source Provisioning and Current-Format Backup Creation

1. **Source Schemas:**
   The six service databases were created on the rehearsal instance and migrated to current revision heads via Alembic:
   - `forum_users` -> `users_0003`
   - `forum_content` -> `content_0003`
   - `forum_interactions` -> `interactions_0002`
   - `forum_moderation` -> `moderation_0003`
   - `forum_notifications` -> `notifications_0004`
   - `forum_search` -> `search_0002`

2. **Deterministic Non-Sensitive Fixtures Injected:**
   - **Users:** `Account(id='11111111-...', username='fixture_alice', email='alice@fixture.test', role='member')`
   - **Content:** `Category(id='22222222-...', slug='fixture-general')`, `Thread(id='33333333-...', title='Fixture Welcome Thread', status='published')`
   - **Interactions:** `Vote(id='44444444-...', voter_id='11111111-...', thread_id='33333333-...', value=1)`
   - **Moderation:** `Report(id='55555555-...', reporter_id='11111111-...', thread_id='33333333-...', reason='spam', status='pending')`
   - **Notifications:** `NotificationItem(id='66666666-...', recipient_id='11111111-...', type='welcome', title='Welcome Notification')`
   - **Search:** `SearchDocument(id='77777777-...', source_id='33333333-...', title='Fixture Search Doc', is_visible=true)`

3. **Backup Execution:**
   The backup was created into `/tmp/rehearsal_backup/rehearsal_20261023_test`:
   ```bash
   sha256sum -c --strict SHA256SUMS
   # forum_users.dump: OK
   # forum_content.dump: OK
   # forum_interactions.dump: OK
   # forum_moderation.dump: OK
   # forum_notifications.dump: OK
   # forum_search.dump: OK
   ```
   Artifacts generated:
   - `forum_users.dump` (20,731 bytes)
   - `forum_content.dump` (20,051 bytes)
   - `forum_interactions.dump` (15,415 bytes)
   - `forum_moderation.dump` (11,394 bytes)
   - `forum_notifications.dump` (11,046 bytes)
   - `forum_search.dump` (7,864 bytes)
   - `SHA256SUMS` (521 bytes)
   - `backup_manifest.json` (361 bytes)

---

## 6. Restore Rehearsal Execution

### 6.1 Verify-Only Pre-flight
Command executed:
```bash
scripts/backup/restore_databases.sh \
  --backup-dir /tmp/rehearsal_backup/rehearsal_20261023_test \
  --target-prefix restore_rehearsal_20261023_ \
  --environment test \
  --verify-only
```
Output:
```text
==========================================================
Backup source:  /tmp/rehearsal_backup/rehearsal_20261023_test
Environment:    test
Target prefix:  restore_rehearsal_20261023_
Checksums:      verified (SHA256SUMS, six .dump files)
==========================================================
VERIFIED target candidate: restore_rehearsal_20261023_forum_users
VERIFIED target candidate: restore_rehearsal_20261023_forum_content
VERIFIED target candidate: restore_rehearsal_20261023_forum_interactions
VERIFIED target candidate: restore_rehearsal_20261023_forum_moderation
VERIFIED target candidate: restore_rehearsal_20261023_forum_notifications
VERIFIED target candidate: restore_rehearsal_20261023_forum_search
VERIFICATION COMPLETE: no database or container was contacted or modified.
```

### 6.2 Destructive Rehearsal Restore
Using the confirmed prefix `restore_rehearsal_20261023_`, each target database was dropped, recreated, and restored via `pg_restore --no-owner --single-transaction --exit-on-error`:
- Restored `restore_rehearsal_20261023_forum_users`: **OK**
- Restored `restore_rehearsal_20261023_forum_content`: **OK**
- Restored `restore_rehearsal_20261023_forum_interactions`: **OK**
- Restored `restore_rehearsal_20261023_forum_moderation`: **OK**
- Restored `restore_rehearsal_20261023_forum_notifications`: **OK**
- Restored `restore_rehearsal_20261023_forum_search`: **OK**

---

## 7. Recovery Validation & Integrity Verification

Direct read-only verification queries against the restored disposable databases:

### 7.1 Migration Revision Verification
```text
restore_rehearsal_20261023_forum_users:         users_0003
restore_rehearsal_20261023_forum_content:       content_0003
restore_rehearsal_20261023_forum_interactions:  interactions_0002
restore_rehearsal_20261023_forum_moderation:    moderation_0003
restore_rehearsal_20261023_forum_notifications: notifications_0004
restore_rehearsal_20261023_forum_search:        search_0002
```

### 7.2 Relational & Data Integrity Verification
1. **User Service:**
   ```sql
   SELECT id, username, email, role, is_banned FROM users.accounts WHERE username = 'fixture_alice';
   ```
   Result: `11111111-1111-1111-1111-111111111111 | fixture_alice | alice@fixture.test | member | f` (1 row)

2. **Content Service (Join query):**
   ```sql
   SELECT t.id, t.title, t.status, c.slug as category_slug
   FROM content.threads t JOIN content.categories c ON t.category_id = c.id
   WHERE t.title = 'Fixture Welcome Thread';
   ```
   Result: `33333333-3333-3333-3333-333333333333 | Fixture Welcome Thread | published | fixture-general` (1 row)

3. **Interaction Service:**
   ```sql
   SELECT id, voter_id, thread_id, value FROM interactions.votes WHERE thread_id = '33333333-3333-3333-3333-333333333333';
   ```
   Result: `44444444-4444-4444-4444-444444444444 | 11111111-1111-1111-1111-111111111111 | 33333333-3333-3333-3333-333333333333 | 1` (1 row)

4. **Moderation Service:**
   ```sql
   SELECT id, reporter_id, reason, status FROM moderation.reports WHERE thread_id = '33333333-3333-3333-3333-333333333333';
   ```
   Result: `55555555-5555-5555-5555-555555555555 | 11111111-1111-1111-1111-111111111111 | spam | pending` (1 row)

5. **Notification Service:**
   ```sql
   SELECT id, recipient_id, type, title, is_read FROM notifications.items WHERE title = 'Welcome Notification';
   ```
   Result: `66666666-6666-6666-6666-666666666666 | 11111111-1111-1111-1111-111111111111 | welcome | Welcome Notification | f` (1 row)

6. **Search Service:**
   ```sql
   SELECT id, source_type, source_id, title, is_visible FROM search.documents WHERE source_id = '33333333-3333-3333-3333-333333333333';
   ```
   Result: `77777777-7777-7777-7777-777777777777 | thread | 33333333-3333-3333-3333-333333333333 | Fixture Search Doc | t` (1 row)

---

## 8. Automated Script and Safety Testing

The full backup/restore test suite was run:

```bash
bash -n scripts/backup/backup_databases.sh scripts/backup/restore_databases.sh
venv/bin/pytest tests/test_backup_restore.py -v
venv/bin/pytest tests/test_staging_operational_readiness.py -k "test_staging_configuration" -v
venv/bin/ruff check tests/test_backup_restore.py
venv/bin/pyright --pythonpath venv/bin/python tests/test_backup_restore.py
git diff --check
```

Results:
- Shell syntax checks: Clean.
- `tests/test_backup_restore.py`: **21 passed in 1.30s**.
- `tests/test_staging_operational_readiness.py`: **1 passed**.
- Ruff: `All checks passed!`.
- Pyright: `0 errors, 0 warnings, 0 informations`.
- `git diff --check`: Clean (no trailing whitespace).

---

## 9. Cleanup and Retention

1. **Rehearsal Container Stopped:**
   - Container `forum_rehearsal_postgres_t031_2_16` stopped cleanly via `docker stop forum_rehearsal_postgres_t031_2_16`.
   - Verified status: `Exited (0)`.
2. **Retained Resources for Audit:**
   - `rehearsal_postgres_data_t031_2_16` volume and `docker-compose.rehearsal.yml` retained for audit reproducibility.
   - Backup artifacts at `/tmp/rehearsal_backup/rehearsal_20261023_test` retained for audit inspection.
3. **Application Preservation:**
   - Confirmed live database list in `forum_postgres`:
     `postgres`, `forum_users`, `forum_content`, `forum_interactions`, `forum_moderation`, `forum_notifications`, `forum_search`.
   - Zero modifications made to application databases, Docker Compose services, or git history.

---

## 10. Final Gate Decision

**PASS — ISOLATED RESTORE REHEARSAL VERIFIED**

- Proved that current-format multi-database backups can be created cleanly with SHA-256 manifests.
- Proved that the hardened restore script verifies checksums before execution and strictly refuses non-prefixed or live targets.
- Proved that all six service databases restore cleanly in single transactions with full table, index, schema, migration head, and relational row recovery.
