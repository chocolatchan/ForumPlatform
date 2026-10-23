# Task 031.2.1 – Git State Inventory and Working Tree Preservation

**Inspection timestamp:** 2026-10-23 (local +0700), read-only inspection session
**Repository root:** `/home/chocolat/Documents/Project/ForumPlatform`
**Task type:** Read-only baseline checkpoint — no `git fetch`, no rebuilds, no restarts

---

## 1. Branch and HEAD

| Item | Value |
|---|---|
| Current branch | `master` |
| HEAD commit | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| HEAD == `origin/master` (existing remote-tracking ref, no fetch run) | ✅ Yes — both resolve to `bf3384c…` |
| Reported baseline commit exists locally | ✅ Yes (`git cat-file -t` → `commit`) |
| Recent history | `bf3384c` feat(tasks 028-031) → `2313bd7` 027-H1-R2 → `d2ea0c3` 027-H1 |
| Staged changes | None (`git diff --cached --stat` empty) |
| Deleted files | None |
| Stashes | None |
| Worktrees | Only the primary worktree on `master` |

---

## 2. Git Working-Tree Summary

- **Modified (unstaged):** 6 files, +121 / −12 lines total
- **Staged:** none
- **Deleted:** none
- **Untracked:** 5 new files (2 tests/migration, 3 audit docs)

### Complete path list

**Modified:**

| Path | Purpose |
|---|---|
| `.env.example` | Documents new `EMAIL_WORKER_LEASE_TIMEOUT_SECONDS` setting |
| `docker-compose.yml` | Adds SMTP / allowlist / lease-timeout env + `mailpit: service_healthy` dependency to `notification-service` |
| `src/services/notification/config.py` | New `email_worker_lease_timeout_seconds` setting (default 300) |
| `src/services/notification/email_worker.py` | Lease recovery: `recover_stale_leases()`, startup + periodic recovery, lease set/clear in `process_batch` |
| `src/services/notification/models.py` | `EmailDelivery.sending_since` column |
| `tests/test_email_dispatch_worker.py` | Fixture pollution fix (shared-MetaData schema stripping removed) + lint cleanups |

**Untracked:**

| Path | Purpose |
|---|---|
| `migrations/notifications/versions/20261022_0004_email_delivery_lease.py` | Adds `sending_since` column (already applied to local DB) |
| `tests/test_email_worker_lease_pg.py` | 12 PostgreSQL lease-recovery integration tests |
| `docs/audit/task_031_1_design.md` | Task 031.1 design document |
| `docs/audit/task_031_1_change_log.md` | Task 031.1 change log |
| `docs/audit/task_031_1_validation_report.md` | Task 031.1 validation report |

**Staged / deleted:** none.

---

## 3. Inventory of Existing Uncommitted Work

### Task 031 email dispatch (committed at `bf3384c`, follow-on edits uncommitted)
- Email dispatch worker source in `src/services/notification/` carries the
  uncommitted Task 031.1 hardening on top of the committed Task 031 worker.
- `tests/test_email_dispatch_worker.py` — existing 23-test regression suite,
  modified only for test-isolation hygiene and lint.

### Task 031.1 lease recovery (uncommitted)
- Lease timestamp column + migration `20261022_0004_email_delivery_lease.py`.
- `recover_stale_leases()` with `SKIP LOCKED`, expired/NULL lease predicate,
  attempt counting, terminal `dead_letter`, bounded batch, resilient startup.
- Configurable lease timeout (`email_worker_lease_timeout_seconds`).
- New PG integration suite `tests/test_email_worker_lease_pg.py` (12 tests).
- Three audit documents under `docs/audit/task_031_1_*`.

### Docker Compose SMTP configuration (uncommitted)
- `docker-compose.yml`: `notification-service` gains SMTP backend, Mailpit host,
  development allowlist (fail-closed defaults, no wildcard), lease-timeout env,
  and a healthy-Mailpit dependency; `.env.example` documents the new variable.

### Unrelated uncommitted changes
- **None.** `git diff HEAD` restricted to User/Content/Interaction/Moderation/
  Search services, gateway, `src/common/`, and the Task 029/030 test files is
  empty. Everything uncommitted belongs to Task 031/031.1 scope.

---

## 4. Container Inventory (Docker available, read-only inspection)

All containers report healthy; nothing was stopped, rebuilt, recreated, or
modified. Environment values were **not** printed.

| Container | Image ID | Image created | Container created | Status |
|---|---|---|---|---|
| `forum_user_service` | `sha256:4a1c1198a0ed…` | 2026-10-22 16:33 +0700 | 2026-10-22T09:33:43Z | running (healthy), up 9 h |
| `forum_notification_service` | `sha256:429d8167327e…` | 2026-10-23 00:18 +0700 | 2026-10-22T17:18:33Z | running (healthy), up ~42 min |
| `forum_api_gateway`, `forum_content_service` | — | — | — | running (healthy), up 9 h |
| `forum_moderation_service`, `forum_search_service`, `forum_interaction_service` | — | — | — | running (healthy), up 18 h |
| `forum_postgres`, `forum_redis`, `forum_mailpit` (v1.31.6) | — | — | — | running (healthy) |

**Discrepancy under investigation (reported only, NOT verified in this task):**
password recovery and email-change endpoints are reported to return HTTP 501 in
committed `src/services/user/app.py` while the running `forum_user_service`
image (image `4a1c1198a0ed`, built ~6 hours before the `bf3384c` commit)
reportedly implements those flows. Per task instructions this finding is recorded
as an unverified claim and deferred to Task 031.2.2; this task performed no
source or container inspection aimed at confirming it.

---

## 5. Commands Not Run and Why

| Command | Reason |
|---|---|
| `git fetch` | Not explicitly authorized by this task (read-only; existing remote-tracking ref used instead) |
| `git add`, `commit`, `push`, `reset`, `clean`, `stash` | Destructive / state-changing — prohibited |
| `docker compose build / up / restart / stop / rm` | Container modification prohibited |
| Environment-file content dumps (`.env`) | Would display secrets — prohibited; only variable *names* referenced in `.env.example` were inventoried |

---

## 6. Confirmation of Non-Modification

This task ran only read-only Git commands (`rev-parse`, `cat-file`, `log`,
`status`, `diff`, `worktree list`, `stash list`), read-only Docker commands
(`ps`, `inspect`, `images`), and created this single report file.

**No application code, tests, migrations, Docker configuration, or environment
files were changed. All pre-existing uncommitted Task 031/031.1 work remains
untouched. Nothing was committed or pushed.**
