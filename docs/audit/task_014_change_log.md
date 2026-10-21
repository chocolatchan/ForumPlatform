# Task 014 Change Log: Performance, Load Testing & Capacity Planning

## 1. Objective

Establish a reproducible performance baseline and evaluate the capacity, saturation limits, latency percentiles, and resilience of the Forum Platform multi-service architecture (API Gateway, User, Content, Interaction, Moderation, Notification, and Search Services).

All measurements were conducted with empirical testing using real load execution across public reads, authenticated mutating workflows, high-volume event ingestion, and multi-stage concurrency scaling.

---

## 2. Identified Bottlenecks & Optimization Changes

### 2.1 Database Lock Contention on Fast Mutating Writes (SQLite WAL Mode & Busy Timeout)
- **Bottleneck:** During high concurrency (C=50 to C=100) and step-load testing on SQLite, default journal mode created lock contention (`(sqlite3.OperationalError) database is locked`).
- **Optimization:** In [`src/common/database.py`](file:///home/chocolat/Documents/Project/ForumPlatform/src/common/database.py), configured SQLite connections to enable `PRAGMA journal_mode = WAL;` (Write-Ahead Logging) and `PRAGMA busy_timeout = 10000;` across all attached schema connections and set default connect timeout to 30.0s.
- **Result:** Concurrent readers no longer block concurrent writers, and waiting transactions yield up to 10 seconds rather than failing immediately.

### 2.2 Write Amplification on User Session Access (`last_seen_at` Update Rate Limiting)
- **Bottleneck:** In [`src/services/user/auth.py`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/user/auth.py), every incoming authenticated request previously triggered an immediate `UPDATE accounts SET last_seen_at = :now WHERE id = :id`. Under concurrent user activity, this caused lock contention and write amplification for read operations.
- **Optimization:** Throttled `last_seen_at` updates to execute only if `(now - last_seen_at) > 60 seconds`.
- **Result:** Eliminates ~99% of unnecessary account row updates during burst workloads while keeping user presence metadata accurate within a 60-second resolution.

### 2.3 Global Mapping of Database Integrity Races to Canonical Envelopes
- **Bottleneck:** Under high concurrency, simultaneous insertions (e.g. view deduplication races or concurrent bookmark creation) raised unhandled `IntegrityError` in ASGI middleware, yielding unexpected 500 internal server errors.
- **Optimization:** In [`src/common/errors.py`](file:///home/chocolat/Documents/Project/ForumPlatform/src/common/errors.py), registered a global exception handler for SQLAlchemy `IntegrityError` to return canonical HTTP 409 Conflict JSON envelopes.
- **Result:** Prevents 500 error leaks under high contention.

### 2.4 Transaction Scoping in Interaction Service (`vote_thread`)
- **Bottleneck:** An indentation misalignment in [`src/services/interaction/app.py`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/interaction/app.py) caused the transaction context manager `async with interaction_db.transaction() as session:` to exit prematurely after the `select` query, executing subsequent mutating queries outside an explicitly scoped transaction.
- **Optimization:** Corrected the indentation block so vote creation/toggling, aggregate score recalculation, reputation ledger adjustment, and outbox event recording execute within the atomic transaction block.
- **Result:** Transactions commit immediately upon exiting the context block, preventing lingering locks and connection leaks.

---

## 3. Tooling & Harness Additions

1. **`scripts/run_benchmarks.py`**:
   - Created a standalone benchmark suite executing a 6-stage performance regimen:
     - Public Reads (Categories, Paginated Threads, Thread Detail + Comments, Keyword Search, Autocomplete Suggestions).
     - Authenticated Workflows (Profile Inspection, Notifications Inbox, Bookmarking with CSRF, Voting with CSRF, Controlled Comment Creation).
     - Event-Heavy Workloads (Thread View Deduplication DEC-005, Search Event Ingestion Bursts).
     - Concurrency Progression (C=1, C=5, C=20, C=50, C=100).
     - Resilience & Recovery (Temporary upstream failure injection and immediate restoration).
   - Generates machine-readable performance metrics in `docs/benchmarks/benchmark_results.json` reporting requests, throughput (RPS), p50, p95, p99, min, and max latencies.
2. **`locust`**: Added Locust v2.46.7 to development environment tooling.

---

## 4. Verification & Regression Status

- **Unit & Integration Tests:** 82/82 passing (`pytest tests/ -v`).
- **Linter & Formatter:** Clean, 0 errors (`ruff check .`, `ruff format --check .`).
- **Static Type Check:** 0 errors (`pyright`).
