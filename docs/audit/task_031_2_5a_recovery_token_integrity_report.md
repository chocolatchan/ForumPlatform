# Task 031.2.5a – Recovery Token Cardinality and Integrity Report

**Date:** 2026-10-23 (local +0700)
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`
**Type:** Source and isolated-test correction; no deployment, no live database writes
**Predecessor:** `docs/audit/task_031_2_4_integration_preflight_report.md` (finding H-2)
**Scope:** Password-recovery token cardinality only. Email-change, avatar, worker, and unrelated authentication behavior were not changed.

---

## 1. Executive Summary

The intended invariant is:

> At most one unconsumed `password_recovery` token exists per account. A new recovery request invalidates every prior unconsumed recovery token, and the newest row is the only valid recovery challenge.

The live database violates that invariant for one account, and the pre-fix source selected the active challenge with `scalar_one_or_none()` without a limit or deterministic tie-break. A request or verification for that account could therefore raise `MultipleResultsFound` and return an unhandled 500 before expiry, cooldown, or attempt logic ran.

A small application-level correction was made:

- Recovery request and verification now share `_lock_recovery_challenge()`.
- The helper locks **all** unconsumed `password_recovery` rows for the account, orders them newest-first with an ID tie-break, returns the newest row, and marks older rows consumed under the same lock.
- No `LIMIT 1` was used to hide the multiple-row condition. Legacy duplicates are explicitly normalized so older challenges cannot be replayed.

This is a defensive, self-healing application fix. It does **not** establish database enforcement of the invariant. A partial unique index is still required, after controlled live-data normalization. That migration/cleanup was deliberately not performed in this task.

No container, image, live database, `.env`, Compose file, or runtime configuration was changed.

---

## 2. Baseline and Scope

| Item | Value |
|---|---|
| Branch | `master` |
| HEAD | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` |
| Staged changes before/after | None |
| Source changed | `src/services/user/app.py` (already modified by prior tasks) |
| Tests changed | `tests/test_user_auth_lifecycle.py` (already modified by prior tasks) |
| New deliverable | This report |
| Live database queries run in this task | None |
| Containers/images touched | None |

All pre-existing Task 031.1 and 031.2.3c–f working-tree changes were preserved. No source was replaced with the reference container file. Nothing was staged or committed.

---

## 3. Intended Invariant and Evidence

### 3.1 Source evidence

The source establishes a single active recovery challenge:

- `recovery_request` locks the account and then, once cooldown/quota permit, runs one `UPDATE` that sets `consumed_at = now` for **every** unconsumed `password_recovery` token before inserting a replacement (`app.py:968–977`).
- `recovery_verify` ordered by `created_at.desc()` before selecting the active challenge (pre-fix `app.py:1032–1042`). The ordering shows the newest request is intended to supersede older ones.
- Verification consumes the challenge (`consumed_at = now`) before issuing a reset ticket (`app.py:1096–1127`).
- Verification invalidates all prior unconsumed reset tickets before creating exactly one new ticket (`app.py:1099–1127`).
- Reset consumes the ticket atomically (`app.py:1237–1238`) and a second use returns `INVALID_RESET_TOKEN`.
- `recovery_request`, `recovery_verify`, and `recovery_reset` use Account → token lock order to serialize lifecycle changes.

### 3.2 Contract evidence

The canonical recovery contract (`src/docs/forum_public_api.openapi.yaml:205–287`) exposes:

- one neutral request endpoint,
- one OTP verification endpoint that issues one short-lived reset token,
- one reset endpoint that accepts that reset token.

The contract does not state row-level cardinality explicitly. It does define a single OTP challenge and a single reset-ticket exchange. The source is therefore the primary evidence for “newest challenge supersedes prior challenges.”

### 3.3 Database evidence

The intended invariant is **not enforced by PostgreSQL**:

- `OneTimeToken` declares only `ck_one_time_attempts` (`src/services/user/models.py:102–109`). There is no unique index or partial unique constraint for unconsumed recovery tokens.
- The initial migration creates `one_time_tokens` with primary key and two check constraints only (`migrations/users/versions/20261020_0001_initial_users.py:127–153`). It creates no index for `one_time_tokens`.
- The preflight live inspection found only a primary-key index on `users.one_time_tokens`.
- The live account `2735494c-9a8d-4bd2-9461-d393351daccc` has two unconsumed `password_recovery` rows:
  - `c2bc32ec-8b04-493c-9824-1a304172cfcf`
  - `40e10b3d-6ad8-4954-8ad9-afce9d4c63d1`
- Both were created at `2026-10-22 09:16:19Z` (milliseconds apart), expired at `2026-10-22 09:31:19Z`, and had `consumed_at IS NULL`. There are three total unconsumed recovery rows in the live database, all expired.

The live evidence is taken from the read-only Task 031.2.4 preflight. This task did not re-query or modify the live database.

---

## 4. Affected Queries

| Location (pre-fix) | Query purpose | Cardinality assumption | Action |
|---|---|---|---|
| `recovery_request`, `app.py:918–928` | Find active recovery token for cooldown/quota | At most one unconsumed recovery row | Replaced with `_lock_recovery_challenge()` |
| `recovery_verify`, `app.py:1032–1042` | Find active recovery token to verify | At most one unconsumed recovery row | Replaced with `_lock_recovery_challenge()` |
| `recovery_reset`, `app.py:1147–1153` | Pre-read reset-ticket owner by `token_hash` | At most one active ticket with that hash | Unchanged |
| `recovery_reset`, `app.py:1169–1179` | Re-read/lock reset ticket by `token_hash` | At most one active ticket with that hash | Unchanged |

The two reset queries match by a 256-bit random token hash, not by account-level active-challenge selection. They are not exposed to the observed per-account duplicate problem. `token_hash` has no database unique constraint, but no duplicate reset-hash evidence exists and changing this path would exceed the recovery-cardinality scope. It remains a low-risk hardening item, not part of this fix.

The email-change handlers use a similar single-result pattern for `EmailChangeRequest`, but the Task 031.2.4 preflight found no live duplicate email-change rows and this task explicitly excludes email-change changes.

---

## 5. Root Cause and Whether Current Code Can Create Duplicates

### 5.1 Direct cause of the 500

Both recovery read paths selected all matching unconsumed rows and passed the result to `scalar_one_or_none()`. SQLAlchemy raises `MultipleResultsFound` whenever more than one row matches. The handler had no exception mapping for that state, so the request could fail as an unhandled 500 before any recovery logic executed.

### 5.2 Whether current code can create the duplicates

Under the intended single-writer path on PostgreSQL, the current code should not create a second active recovery token:

1. `recovery_request` locks the `Account` row with `FOR UPDATE`.
2. It reads the existing active token.
3. It runs a bulk update consuming **all** unconsumed recovery tokens.
4. It inserts exactly one replacement.
5. Concurrent requests for the same account serialize on the account row.

However, this conclusion has three limits:

- It assumes no pre-existing duplicates. With duplicates already present, the pre-fix read raises before the invalidation update can repair them.
- It assumes every writer uses this account-lock and invalidation path. There is no database constraint preventing another writer or a migration/seed script from inserting duplicates.
- The running User Service was stale during the preflight. The live rows may have been written by older code whose locking/invalidation behavior differed from the restored source.

The two live rows were created milliseconds apart. That pattern is consistent with two requests racing through an older implementation that lacked the current account lock and bulk invalidation, or with direct test seeding. The exact historical writer cannot be proven from the available artifacts. This report does not claim a proven historical cause beyond “a path that did not enforce the single-active-token invariant.”

---

## 6. Implemented Correction

### 6.1 Shared challenge lock/normalization helper

Added `_lock_recovery_challenge(session, account_id, now)` at `src/services/user/app.py:883–909`:

```python
stmt = (
    select(OneTimeToken)
    .where(
        OneTimeToken.account_id == account_id,
        OneTimeToken.purpose == "password_recovery",
        OneTimeToken.consumed_at.is_(None),
    )
    .order_by(OneTimeToken.created_at.desc(), OneTimeToken.id.desc())
    .with_for_update()
)
active_tokens = list((await session.execute(stmt)).scalars().all())
if not active_tokens:
    return None

for superseded_token in active_tokens[1:]:
    superseded_token.consumed_at = now
return active_tokens[0]
```

Why this shape:

- It does not suppress duplicates with `LIMIT 1`.
- It treats the newest row as the authoritative challenge.
- It explicitly consumes every older unconsumed row under the same lock.
- The ID tie-break makes equal `created_at` values deterministic.
- The account lock remains the outer serialization mechanism; this helper preserves Account → token lock order.

### 6.2 `recovery_request`

`existing_token = await _lock_recovery_challenge(session, account.id, now)` replaces the scalar query at `app.py:948`.

Consequences:

- Legacy duplicates no longer cause `MultipleResultsFound`.
- If cooldown/quota blocks a new OTP, older duplicate rows are still consumed while the newest challenge remains active.
- If a new OTP is issued, the existing bulk invalidation consumes the remaining active row(s) before the replacement is inserted.

### 6.3 `recovery_verify`

`token_record = await _lock_recovery_challenge(session, account.id, now)` replaces the scalar query at `app.py:1051–1053`.

Consequences:

- The newest challenge is the only candidate for verification.
- Older duplicate OTPs are consumed and cannot be used after the newest challenge succeeds, expires, or exhausts attempts.
- Expired newest challenges still return `OTP_EXPIRED` and are consumed; no older duplicate remains to retry.
- A replay after successful verification finds no active recovery token and returns `INVALID_OTP`.

### 6.4 Reset behavior

Reset code and semantics were intentionally not changed:

- one-time ticket consumption remains atomic under lock;
- replay returns `INVALID_RESET_TOKEN`;
- password history/session revocation behavior is untouched.

---

## 7. Defensive Fix vs. Database Enforcement

The new helper is a **defensive query correction plus application-level normalization**. It repairs duplicate state when a recovery handler next touches the account and prevents the observed 500. It is not database enforcement:

- A crash or direct writer can still insert a second unconsumed row.
- Another service/script that bypasses this code path is not constrained.
- The invariant is still only a source-level convention.

True enforcement requires a partial unique index after live-data cleanup:

```sql
CREATE UNIQUE INDEX uq_one_time_tokens_active_recovery
ON users.one_time_tokens (account_id)
WHERE purpose = 'password_recovery' AND consumed_at IS NULL;
```

This cannot be applied while the live duplicate rows exist; index creation would fail. The required live-data cleanup and migration are documented in §10 and were **not** executed.

---

## 8. Tests Added

Added to `tests/test_user_auth_lifecycle.py` using the existing isolated SQLite fixture. These tests never connect to the live PostgreSQL database.

| Test | Coverage |
|---|---|
| `test_recovery_request_normalizes_duplicate_unconsumed_tokens` | Two unconsumed tokens exist; request returns 202 without a multiple-row error, older rows are consumed, exactly one new active challenge exists, and a repeated request during cooldown does not issue a second challenge. |
| `test_recovery_verify_uses_newest_duplicate_and_consumes_older` | Two unconsumed tokens plus one already-consumed token exist; verification of the newest succeeds, the older OTP is rejected and consumed, the reset succeeds once, and reset-ticket replay returns `INVALID_RESET_TOKEN`. |
| `test_recovery_verify_consumes_expired_duplicate_tokens_without_error` | Two expired unconsumed tokens exist; verification returns `OTP_EXPIRED` instead of 500, all duplicate rows are consumed, and the next verification returns `INVALID_OTP`. |

Existing lifecycle coverage remains relevant and passed:

- `test_password_recovery_and_reset_lifecycle` covers neutral responses, wrong OTP, successful verification, OTP replay, password reuse rejection, reset replay, session revocation, and old/new password login.

### 8.1 Concurrency coverage

No new “concurrent” test was run against SQLite because SQLite ignores PostgreSQL row-level `FOR UPDATE` semantics and cannot safely prove one-winner behavior under competing writers. A test that passed or failed there would not be transferable evidence for PostgreSQL.

The existing live PostgreSQL test `tests/test_recovery_concurrency_pg.py` targets the running stack and live database. It was **not run** in this task. PostgreSQL concurrency remains unproven by this task's execution and still requires the controlled live-stack validation planned after the stale User Service is replaced.

The deterministic ordering and duplicate normalization added here are covered by isolated tests. The locking behavior itself remains dependent on PostgreSQL row locks.

---

## 9. Validation Results

All commands were run with the project virtual environment. No live database was used.

| Command | Result |
|---|---|
| `venv/bin/pytest tests/test_user_auth_lifecycle.py -k recovery -q` | **4 passed**, 14 deselected, 2.83s |
| `venv/bin/pytest tests/test_user_auth_lifecycle.py -q` | **18 passed**, 7.80s |
| `venv/bin/ruff check src/services/user/app.py tests/test_user_auth_lifecycle.py` | **All checks passed** |
| `venv/bin/pyright --pythonpath venv/bin/python3` | **0 errors, 0 warnings, 0 informations** |
| `git diff --check` | **Passed** (no whitespace errors) |
| `venv/bin/ruff format --check src/services/user/app.py tests/test_user_auth_lifecycle.py` | Reports pre-existing unformatted lines in both already-modified files |

### 9.1 Formatter note

`ruff format --check` flags unrelated pre-existing formatting in the working tree, including avatar URL expressions, an `acc_stmt` assignment, logger calls, and older test lines. The newly added recovery helper and new tests were not among the flagged locations. The formatter was intentionally not run because it would rewrite unrelated Task 031.1/031.2.3 uncommitted work. Lint and type checks for the changed code pass.

---

## 10. Required Follow-Up: Live-Data Normalization and Database Invariant

The following work is **deferred** and must be performed in a separate controlled maintenance task. Do not run it against the live database as part of this task.

### 10.1 Controlled data normalization

1. Take a verified backup and record read-only counts of unconsumed `password_recovery` tokens grouped by `account_id`.
2. For each account with more than one unconsumed recovery token:
   - choose the newest row by `created_at DESC, id DESC` as the authoritative challenge;
   - set `consumed_at` to a recorded maintenance timestamp on all older unconsumed recovery rows;
   - do not delete token rows and do not change the authoritative row's OTP hash, expiry, attempts, or resend state.
3. If the authoritative row is already expired, it may still be soft-consumed or left for normal expiry handling; this decision must be explicit and logged.
4. Re-run the count until every account has at most one unconsumed recovery token.
5. Preserve an audit record of account IDs, token IDs, prior `consumed_at`, and the maintenance timestamp.

For the known live account `2735494c-9a8d-4bd2-9461-d393351daccc`, both rows are already expired. No token was consumed, deleted, or invalidated in this task.

### 10.2 Migration

After normalization succeeds, add a User Service migration that creates the partial unique index:

```sql
CREATE UNIQUE INDEX uq_one_time_tokens_active_recovery
ON users.one_time_tokens (account_id)
WHERE purpose = 'password_recovery' AND consumed_at IS NULL;
```

Requirements for that migration:

- Use `CONCURRENTLY` only if the migration framework and transaction policy safely support it; otherwise schedule an appropriate maintenance window.
- Make the index creation fail closed if duplicate active rows remain.
- Add a downgrade that drops the index.
- Add a regression test that attempts to insert two unconsumed recovery rows and expects an integrity error.
- Consider whether `token_hash` also needs a unique constraint; that is a separate integrity decision and is not required for this fix.

The application helper remains useful after the migration: it handles legacy rows during the transition and gives deterministic behavior if a future writer is introduced.

### 10.3 Live validation after remediation

Only after the User Service is rebuilt from current source and the stale container is replaced:

1. Re-run read-only cardinality checks.
2. Exercise recovery request/verify/reset against the affected account with a controlled test token.
3. Run `tests/test_recovery_concurrency_pg.py` against the controlled stack.
4. Verify the partial unique index exists and blocks a duplicate insert.

None of these steps were performed here.

---

## 11. Scope and Safety Confirmation

- No container was rebuilt, restarted, stopped, or recreated.
- No image, Docker Compose file, image tag, `.env`, or runtime configuration was changed.
- No live database was read, written, cleaned, or migrated in this task.
- No live recovery token was consumed, deleted, or invalidated.
- No migration was added or applied.
- No source file was overwritten with the reference container file.
- No changes were made to email-change, avatar, notification worker, or unrelated authentication code.
- No changes were staged or committed.
- Existing uncommitted Task 031.1 and 031.2.3c–f work was preserved.

### Files changed by this task

- `src/services/user/app.py` — shared recovery challenge lock/normalization helper; request and verify now use it.
- `tests/test_user_auth_lifecycle.py` — three focused cardinality/expiry/replay tests.
- `docs/audit/task_031_2_5a_recovery_token_integrity_report.md` — this report.

---

## 12. Review Status

Source correction and isolated regression tests are complete. Database invariant enforcement and live-data remediation are documented but intentionally deferred.

**Stop for review.** No staging, commit, rebuild, container change, or live database action was performed.
