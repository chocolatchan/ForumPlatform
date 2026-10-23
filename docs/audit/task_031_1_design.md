# Task 031.1 – Email Worker Lease Recovery: Design Document

**Date:** 2026-10-22
**Branch:** `master`
**Baseline commit:** `bf3384c4c3b53b5f826ef119f2ad549997ff1d63`
**Predecessor:** Task 031 (see `task_031_design.md`, §4.4 — sending-row recovery was explicitly deferred)

---

## 1. Objective

Harden the Task 031 `EmailDispatchWorker` so that emails claimed for dispatch but
never confirmed (`EmailDelivery.status = 'sending'`) are automatically recovered
after a worker crash, restart, or prolonged SMTP stall — without ever reclaiming a
lease that an active worker still holds, and without exceeding the existing
max-attempt / dead-letter retry policy.

## 2. Problem Statement

Task 031's worker claimed rows by flipping `pending → sending`, dispatched over
SMTP, then recorded `delivered` / `failed`. If the process died between the claim
and the outcome write, the row stayed `sending` forever: no other code path ever
re-claimed it. `task_031_design.md` §4.4 recorded this as deferred work; this task
closes that gap.

## 3. Architectural Decisions

### 3.1 Lease timestamp instead of owner ID

A `sending_since` timestamp column (nullable `TIMESTAMP WITH TIME ZONE`) is added
to `notifications.email_deliveries` by migration
`20261022_0004_email_delivery_lease.py`. We deliberately do **not** store a worker
identity: the recovery decision depends only on *how long* the row has been in
`sending`, which works across restarts, rolling deploys, and any number of
replicas without needing clocks to agree on ownership.

### 3.2 Configurable lease timeout

`NotificationSettings.email_worker_lease_timeout_seconds` (default **300 s**,
env `EMAIL_WORKER_LEASE_TIMEOUT_SECONDS`) defines when a `sending` row is
considered an orphaned lease. It must comfortably exceed the worst-case batch
dispatch time (batch size × SMTP timeout); the default satisfies that for the
current batch size (10) and SMTP timeout (10 s per attempt).

### 3.3 Recovery predicate

A `sending` row is recoverable when:

- `sending_since <= now - lease_timeout` (expired lease), **or**
- `sending_since IS NULL` (row claimed by pre-migration code, or a crash between
  the status update and the lease timestamp update — new code writes both in the
  same transaction, so `NULL` implies an orphan).

Recovery runs (a) at worker startup, wrapped in `try/except` so a database outage
at boot does not kill the loop — it retries on the periodic schedule — and
(b) periodically every `max(60 s, lease_timeout / 2)`.

### 3.4 Concurrency safety: `SELECT … FOR UPDATE SKIP LOCKED`, bounded batch

The recovery query locks candidate rows with `SKIP LOCKED` so two replicas
recovering concurrently each claim disjoint rows; a row is recovered exactly
once. The query is bounded with `.limit(batch_size)` and ordered
`sending_since ASC NULLS FIRST` (oldest orphans first) to keep lock hold times
short; remaining orphans are picked up on the next periodic pass.

### 3.5 Attempt counting and retry policy

Recovery increments `attempts` (the crashed attempt is consumed — the worker
cannot know whether SMTP accepted the message), clears the lease, and:

- `attempts < max_attempts` → `status = 'failed'`, `next_attempt_at` set via the
  existing exponential-backoff function (row re-enters the normal dispatch queue);
- `attempts >= max_attempts` → `status = 'dead_letter'` (terminal).

Terminal `dead_letter` rows are never re-claimed by recovery or by
`process_batch`, so retries stay bounded even under repeated crashes.

### 3.6 Delivery semantics: at-least-once, with a documented ambiguity window

If a worker crashes *after* SMTP accepted the message but *before* the
`delivered` write, recovery will re-dispatch and the recipient may receive a
duplicate. Exactly-once would require two-phase-commit with the SMTP server,
which does not exist. The implementation and its docstring state
**at-least-once** semantics explicitly; no public email-status endpoint is
exposed that could be mistaken for a delivery receipt.

## 4. Operational Safety

- **Allowlist stays fail-closed**: recovery and dispatch both go through the
  existing `is_recipient_allowed` check; an empty allowlist blocks all sends and
  a non-matching recipient goes straight to `dead_letter` without touching SMTP.
  No wildcard recipient is configured anywhere in Compose or `.env.example`.
- **No secrets in logs**: recovery logs row IDs, attempt counts, and backoff
  durations only — never SMTP credentials, OTPs, reset tickets, or message bodies.
- **Graceful shutdown**: `stop()` still cancels the loop cleanly; an in-flight
  dispatch interrupted by shutdown leaves a `sending` row that the next startup
  recovers.
- **No new broker**: recovery is in-process on the existing PostgreSQL queue.
- **Compose**: `notification-service` gains the SMTP / allowlist / lease-timeout
  environment variables (previously it silently defaulted to the in-memory
  backend) and a `mailpit: service_healthy` dependency, mirroring `user-service`.

## 5. Test Design (PostgreSQL integration, `tests/test_email_worker_lease_pg.py`)

1. Expired `sending` lease is recovered → `failed` with backoff, attempts counted.
2. Active (fresh) lease is **not** recovered.
3. Two concurrent `recover_stale_leases()` calls recover the same expired row
   exactly once (SKIP LOCKED).
4. Row at `max_attempts` goes to `dead_letter` during recovery (terminal).
5. Worker startup recovers stale rows automatically.
6. `process_batch` sets `sending_since` on claim and clears it on outcome.
7. Concurrent `process_batch` calls never double-claim a pending row.
8. Worker restart with both stale *and* pending rows: stale recovered, pending dispatched.
9. Ambiguous SMTP result (timeout where acceptance is unknown): attempt counted,
   row retryable, subsequent success delivers — at-least-once verified.
10. Crash after SMTP acceptance, before the DB update: fresh lease protected,
    expired lease recovered and re-dispatched (possible duplicate documented).
11. Repeated failure → recovery cycles land in `dead_letter`; terminal state
    preserved against further recovery and claiming.
12. `NULL sending_since` orphan (pre-migration row) is recovered.
