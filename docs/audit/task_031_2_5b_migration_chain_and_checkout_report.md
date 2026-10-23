# Task 031.2.5b — Migration Chain Integrity and Clean-Checkout Readiness Report

**Date:** 2026-10-23
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`
**Branch:** `master`
**HEAD:** `bf3384c4c3b53b5f826ef119f2ad549997ff1d63`
**Type:** Source audit and narrow test/migration-environment correction; no deployment, no live database migration, no staging, no commit.

---

## 1. Verdict

**READY WITH EXPLICIT RISKS**

The Notification Service migration chain is structurally sound and linear, and the model, migration, and worker agree on the `sending_since` lease field. Two narrow source defects were corrected:

1. The PostgreSQL lease test could silently target the development `forum_notifications` database. It now requires `TEST_NOTIFICATIONS_DATABASE_URL`, skips safely when unset, and refuses the development database name at collection time.
2. The Notification Alembic environment imported only `Base`, so `Base.metadata` was empty. It now imports the Notification models, making metadata discovery correct for this service.

This is **not** a clean-checkout certification. The migration and lease test are still untracked and were not staged or committed, as required by this task. A clean checkout of `HEAD` therefore does **not** contain them. Live PostgreSQL validation remains deferred.

---

## 2. Initial branch, HEAD, and working-tree snapshot

| Item | Initial value |
|---|---|
| Branch | `master` |
| HEAD | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| Staged files | None |
| Tracked-modified files | 9 |
| Untracked files | 15 |

Initial tracked-modified files:

- `.env.example`
- `docker-compose.yml`
- `src/services/notification/config.py`
- `src/services/notification/email_worker.py`
- `src/services/notification/models.py`
- `src/services/user/app.py`
- `src/services/user/schemas.py`
- `tests/test_email_dispatch_worker.py`
- `tests/test_user_auth_lifecycle.py`

Initial untracked files:

- 13 prior Task 031.1 / 031.2 audit reports under `docs/audit/`
- `migrations/notifications/versions/20261022_0004_email_delivery_lease.py`
- `tests/test_email_worker_lease_pg.py`

After this task, `migrations/notifications/env.py` is also tracked-modified, and the untracked lease test has been corrected in place. Nothing was staged or committed.

---

## 3. Migration revision graph

Static parsing of all `migrations/*/versions/*.py` files found:

- 17 migration files
- 17 globally distinct revision IDs
- No duplicate revision IDs
- No missing `down_revision` targets
- Exactly one head per service

| Service | Chain / head |
|---|---|
| `users` | `users_0001 → users_0002 → users_0003` (head) |
| `content` | `content_0001 → content_0002 → content_0003` (head) |
| `interactions` | `interactions_0001 → interactions_0002` (head) |
| `moderation` | `moderation_0001 → moderation_0002 → moderation_0003` (head) |
| `notifications` | `notifications_0001 → notifications_0002 → notifications_0003 → notifications_0004` (head) |
| `search` | `search_0001 → search_0002` (head) |

The Notification chain is:

```text
notifications_0001  (initial notifications tables)
        ↓
notifications_0002  (consumed_events)
        ↓
notifications_0003  (email_deliveries)
        ↓
notifications_0004  (sending_since lease column; current head)
```

`venv/bin/alembic -c migrations/notifications/alembic.ini history --verbose` confirmed the same parent/head order without connecting to a database. Per-service `alembic heads` also reported exactly the expected head for every service.

### `notifications_0004` content review

`migrations/notifications/versions/20261022_0004_email_delivery_lease.py`:

- Revision: `notifications_0004` (`:13`)
- Parent: `notifications_0003` (`:14`)
- Upgrade: adds nullable `notifications.email_deliveries.sending_since` as `TIMESTAMP WITH TIME ZONE` (`:19–24`)
- Downgrade: drops only `sending_since` (`:27–28`)

No duplicate revision identifier or duplicate migration file was found. The filename/date ordering and `notifications_NNNN` naming convention are consistent with the existing Notification migrations.

### Index / constraint finding

`notifications_0004` intentionally adds no index or constraint. `notifications_0003` already defines:

- `ix_notifications_email_deliveries_status_next_attempt` on `(status, next_attempt_at)` (`20261022_0003_email_deliveries.py:56–61`)
- `ix_notifications_email_deliveries_recipient_id` (`:62–67`)
- `uq_email_delivery_event_recipient` (`:53`)

The lease recovery query filters `status = 'sending'` and `(sending_since <= cutoff OR sending_since IS NULL)` and orders by `sending_since` (`email_worker.py:315–326`). The existing index can filter on `status` but does not index `sending_since`. This is a performance consideration for a large backlog of stale `sending` rows, not a schema-correctness defect. No new index was invented, because no repository evidence establishes one and adding it would require a new migration and database validation.

---

## 4. Model / migration / worker consistency

The `sending_since` lease field is consistent across the relevant layers:

| Layer | Evidence | Result |
|---|---|---|
| ORM model | `src/services/notification/models.py:110` — optional `datetime`, `DateTime(timezone=True)`, `nullable=True` | Matches |
| Migration | `20261022_0004_email_delivery_lease.py:22` — nullable `DateTime(timezone=True)` | Matches |
| Recovery query | `email_worker.py:287–351` — selects expired or `NULL` leases, increments attempts, moves to `failed` or `dead_letter` | Matches |
| Claim path | `email_worker.py:378–381` — sets `status = "sending"` and `sending_since` in the claim transaction | Matches |
| Outcome path | `email_worker.py:412–420` — clears `sending_since` on success or failure | Matches |
| Config | `src/services/notification/config.py:52–55` — default lease timeout `300` seconds | Matches |
| Compose / example | `docker-compose.yml:221`, `.env.example:60` — pass `EMAIL_WORKER_LEASE_TIMEOUT_SECONDS` with default `300` | Matches |

No mismatch was found for `sending_since`.

A pre-existing model/migration index drift remains: the model declares `index=True` on `status` and `next_attempt_at`, while the migration defines a composite `(status, next_attempt_at)` index instead. This predates Task 031.2.5b and was not changed. It matters for autogenerate/`alembic check`, not for applying the handwritten migrations.

### Alembic metadata discovery

Before correction, `migrations/notifications/env.py` imported only `Base` and never imported `src.services.notification.models`. A direct metadata check showed:

```text
base tables before model import: []
```

`alembic check` also reported every Notification table as “removed,” confirming that the environment had no target metadata.

The corrected environment imports the Notification model module at `migrations/notifications/env.py:7`. A stubbed environment import (calling `runpy` with `run_service_migrations` mocked, without a database connection) showed:

```text
metadata_tables: ['notifications.consumed_events', 'notifications.email_deliveries', 'notifications.items', 'notifications.preferences']
```

This fixes metadata discovery for the Notification Service. The other five service `env.py` files still import only `Base`; that repository-wide pattern is outside this task’s scope and is recorded as a follow-up risk. It does not prevent the handwritten Notification migrations from applying, but it makes autogenerate/`alembic check` unreliable until addressed.

---

## 5. PostgreSQL lease-test isolation and coverage

### Original isolation defect

The original test hardcoded:

```text
postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_notifications
```

It therefore defaulted to the normal development database and could insert/delete `email_deliveries` rows there. The live notification worker could also claim those rows, making the test flaky and potentially mixing test rows with development data. No explicit opt-in or skip guard existed.

### Corrected behavior

`tests/test_email_worker_lease_pg.py` now:

- Requires `TEST_NOTIFICATIONS_DATABASE_URL` (`:34`).
- Marks the whole module skipped when the variable is unset (`:35–42`), so a fresh checkout does not accidentally connect or fail on a missing database.
- Fails closed at collection time if the configured database name is `forum_notifications` (`:43–49`).
- Contains no embedded database password or absolute local path.
- Keeps cleanup scoped to the UUIDs generated by each test (`_delete_rows`, `:88–93`).

The test still requires an already-migrated disposable database, specifically one at `notifications_0004` (the `sending_since` column must exist). It does not create or migrate the database automatically.

Residual isolation assumptions:

- The configured database name is not the development database, but the test cannot prove that an arbitrarily named database is disposable.
- If run concurrently with another process against the same test database, or with `pytest-xdist` against a shared database, rows can still contend.
- Cleanup is best effort. A hard kill before `finally` can leave test rows behind, though they are random UUIDs and do not delete unrelated rows.
- The test DB should be dedicated to tests and should not be used by any running worker.

### Coverage assessment

The suite uses real `asyncpg`/PostgreSQL and the worker’s real `FOR UPDATE SKIP LOCKED` queries. Only SMTP dispatch is mocked; locking and database semantics are genuine.

| Scenario | Test | Covered |
|---|---|---|
| Expired lease recovered to `failed` | `test_stale_sending_row_recovered:102` | Yes |
| Active lease preserved | `test_active_lease_not_recovered:138` | Yes |
| Competing recovery workers claim once | `test_concurrent_workers_recover_same_stale_row_only_once:171` | Yes |
| Max-attempt row moved to `dead_letter` | `test_stale_row_at_max_attempts_moved_to_dead_letter:220` | Yes |
| Startup recovery | `test_worker_startup_recovers_stale_rows:256` | Yes |
| Claim sets and clears `sending_since` | `test_process_batch_sets_sending_since:299` | Yes |
| Concurrent `process_batch` does not double-claim | `test_no_duplicate_claim_under_concurrent_process_batch:341` | Yes |
| Restart recovers stale and dispatches pending | `test_worker_restart_recovers_stale_and_dispatches_pending:389` | Yes |
| Ambiguous SMTP result is retryable and counted | `test_ambiguous_smtp_result_is_retryable_and_counts_attempt:450` | Yes |
| Crash after acceptance recovers/redelivers | `test_crash_after_smtp_accept_before_db_update_recovers_and_redispatches:526` | Yes |
| Repeated failures reach terminal `dead_letter` | `test_repeated_failures_reach_dead_letter_via_recovery_cycles:603` | Yes |
| `NULL sending_since` orphan recovered | `test_null_sending_since_orphan_is_recovered:692` | Yes |

Coverage gaps and operational risks:

- **Bounded recovery batches are not explicitly tested.** No test seeds more than `batch_size` stale rows and asserts that recovery processes only the requested limit.
- No per-test timeout is configured. If a database connection or lock hangs, the test can run until an external CI timeout.
- The two tests that start the worker now call `worker.stop()` again in their `finally` blocks (`:288–291` and `:439–440`) so a failure before the normal stop does not leave a background worker running.
- The suite does not verify migration application/downgrade; it assumes `notifications_0004` is already applied.

Other PostgreSQL tests in the repository still hardcode development database URLs, for example `tests/test_recovery_concurrency_pg.py:28–30` and `tests/test_avatar_email_change_pg.py:42–45`. They were not modified here, but they remain a clean-checkout/CI isolation risk.

---

## 6. Disposition of untracked files

| Path | Tracked? | Ignored? | Purpose | Disposition |
|---|---|---|---|---|
| `migrations/notifications/versions/20261022_0004_email_delivery_lease.py` | No | No | Required Notification lease migration; sole head after `0003` | Legitimate source artifact. Keep intact. Must be added to version control before a clean checkout/rebuild. Not staged in this task. |
| `tests/test_email_worker_lease_pg.py` | No | No | Required real-PostgreSQL lease integration tests | Legitimate source artifact. Corrected for isolation/worker cleanup; keep intact. Must be added to version control before clean-checkout validation. Not staged in this task. |
| 13 existing audit reports under `docs/audit/` | No | No | Prior task documentation | Unrelated to this audit; left untracked as required. |

Confirmation:

- `git ls-files migrations/notifications/versions/20261022_0004_email_delivery_lease.py tests/test_email_worker_lease_pg.py` returned nothing.
- `git check-ignore` returned no match for either file (exit 1), so neither is ignored.

### Clean-checkout consequence

`Dockerfile:18` copies `migrations/` into the image. A build from the current working tree can see the untracked `0004` migration; a clean checkout of `HEAD` cannot. The same applies to the untracked lease test. Until both files are committed, a fresh clone is not reproducible for this feature.

---

## 7. Files changed by this task

| File | Change | Reason |
|---|---|---|
| `migrations/notifications/env.py` | Added `from src.services.notification import models as notification_models  # noqa: F401` at `:7` | Populates `Base.metadata` for Notification Alembic metadata discovery. No migration was created or edited. |
| `tests/test_email_worker_lease_pg.py` | Added `TEST_NOTIFICATIONS_DATABASE_URL` gate, development-database rejection, no-credential default, worker stop in failure paths, and Ruff formatting | Prevents accidental writes to `forum_notifications`, makes the test skip safely in a fresh checkout, and avoids leaking background workers on failure. |
| `docs/audit/task_031_2_5b_migration_chain_and_checkout_report.md` | New report | This deliverable. |

No application handler, worker behavior, recovery-token logic, Docker Compose file, environment file, or unrelated test was changed.

---

## 8. Checks run and results

| Check | Result |
|---|---|
| Static migration graph parser across all services | 17 files, 17 distinct revisions, no missing parents, one head per service |
| `alembic -c migrations/notifications/alembic.ini history --verbose` | Linear `0001 → 0002 → 0003 → 0004`; `0004` is head |
| `alembic heads` for all six services | Expected single head per service; Notification head is `notifications_0004` |
| `ruff check migrations/notifications/env.py tests/test_email_worker_lease_pg.py` | Passed |
| `ruff format --check` on both changed files | Passed after formatting the untracked test |
| `pytest --collect-only -q tests/test_email_worker_lease_pg.py` with `TEST_NOTIFICATIONS_DATABASE_URL` unset | 12 collected, exit 0 |
| `pytest -q -rs tests/test_email_worker_lease_pg.py` with the variable unset | 12 skipped, exit 0; no database connection |
| Collection with a fake test URL ending in `/forum_notifications_test` | 12 collected, exit 0; no database connection |
| Collection with the forbidden `/forum_notifications` URL | `pytest.UsageError` at collection, exit 2; no database connection |
| Stubbed `runpy` import of `migrations/notifications/env.py` with migrations runner mocked | Metadata contained the four Notification tables; no database connection |
| `git diff --check` | Exit 0 |
| `git ls-files` / `git check-ignore` on the two artifacts | Both untracked and not ignored |
| PostgreSQL lease tests | **Not run** against any database |

### Read-only `alembic check` caveat

One `alembic check` command was executed against the default local Notification database before the isolation audit concluded. It connected to the default `forum_notifications` URL, performed Alembic’s metadata comparison, and failed because `target_metadata` was empty. No revision was applied, stamped, upgraded, downgraded, or rolled back, and no data insert/update/delete was performed. The shared environment helper also issues `CREATE SCHEMA IF NOT EXISTS notifications`, which is a no-op against the existing schema. The command was not repeated after the `env.py` correction, because further live database access is deferred.

A passing static graph check is **not** proof that `notifications_0001..0004` apply successfully to a real PostgreSQL database. That remains deferred.

---

## 9. Verified facts vs. assumptions / unverified conditions

### Verified by inspection or safe local checks

- The migration graph is linear and has exactly one head per service.
- `notifications_0004` is the sole Notification head and adds only nullable `sending_since`.
- `notifications_0004` downgrade drops only `sending_since`.
- Model, migration, worker claim, worker recovery, and worker outcome paths agree on `sending_since`.
- The untracked migration and test are legitimate source artifacts, not ignored files.
- The original lease test defaulted to the development database; the corrected test does not.
- The Notification Alembic environment now discovers the four Notification tables.

### Unverified / assumptions

- The live notification database was not re-inspected in this task. The prior Task 031.2.4 report states it is at `notifications_0004` and has the `sending_since` column; that remains a prior-task claim, not a Task 031.2.5b fact.
- No disposable PostgreSQL test database was provisioned, so the 12 tests were not executed.
- Real migration application, downgrade, lock behavior, and recovery-query performance on PostgreSQL remain unverified.
- The other service `env.py` metadata-discovery defect and the model/migration index drift remain unresolved.
- Container/image state was not inspected or changed.

---

## 10. Explicitly deferred database validation

The following must be performed later against an isolated disposable PostgreSQL environment, not the development database:

1. Apply the full Notification chain `notifications_0001..0004` to an empty PostgreSQL database.
2. Verify `notifications.email_deliveries.sending_since` is `timestamp with time zone` and nullable.
3. Verify `notifications_0004` does not fail on a fresh database and that its downgrade removes only `sending_since`.
4. Run all 12 lease tests with `TEST_NOTIFICATIONS_DATABASE_URL` pointing at the disposable database.
5. Add a bounded-batch test that seeds more than `batch_size` stale rows and verifies recovery is limited.
6. Verify recovery-query behavior and latency with a realistic backlog; decide separately whether an index on `(status, sending_since)` is warranted.
7. Re-run `alembic check` only against a disposable database and review the pre-existing index/model drift separately.
8. Verify a clean checkout/build after the two artifacts are committed: the image must contain `migrations/notifications/versions/20261022_0004_email_delivery_lease.py`, and the test must be present.
9. Confirm the live retained database version with a read-only check before any rebuild; do not apply `0004` again if it is already stamped.

---

## 11. Recommended prerequisites for the controlled rebuild / integration phase

1. **Commit the two required artifacts in a separate controlled task.** This task was explicitly forbidden from staging or committing. A clean checkout is not reproducible until the migration and lease test are tracked.
2. Provision a dedicated disposable PostgreSQL database and apply the Notification migrations there.
3. Set `TEST_NOTIFICATIONS_DATABASE_URL` to that disposable database; never point it at `forum_notifications`.
4. Verify the test suite skips cleanly when the variable is absent and runs only against the isolated database when it is present.
5. Snapshot/back up retained volumes before any rebuild. Confirm live `notifications_0004` and `users_0003` with read-only checks before deciding whether migration execution is needed.
6. Rebuild from a clean checkout only after the two artifacts are committed, and verify the migration file is present inside the image.
7. Run PostgreSQL lease tests and migration apply/downgrade checks only on the disposable database.
8. Treat the missing `sending_since` index, the other services’ empty Alembic metadata, and the hardcoded PG tests in other test modules as separate follow-up work.

---

## 12. Safety confirmation

- No database migration was applied, upgraded, downgraded, stamped, or rolled back.
- No application data was inserted, updated, deleted, or truncated by this task.
- The only live database contact was the read-only `alembic check` metadata comparison described in §8; it did not execute a revision and did not modify data.
- No container or image was rebuilt, restarted, stopped, recreated, removed, loaded, retagged, or deleted.
- No `.env`, secret, Docker Compose, or runtime configuration was edited.
- Nothing was staged or committed; `HEAD` remains `bf3384c4c3b53b5f826ef119f2ad549997ff1d63`.
- No reset, clean, stash, or overwrite of existing work was performed.
- Task 031.1 and Task 031.2.3c–5a changes, including the recovery-token fix, remain intact.
- Exactly one new report was created: this file.
