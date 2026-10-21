# Task 016 Operational Readiness Baseline Audit Report

**Date of Execution:** 2026-10-21  
**Target Architecture:** API Gateway + 6 FastAPI Services (`user`, `content`, `interaction`, `moderation`, `notification`, `search`) + PostgreSQL 16 + Redis 7 + Mailpit  
**Deployment State:** 10 Docker Compose containers healthy, 6 logical PostgreSQL databases, Alembic migrations verified at head.  
**Baseline Test Result:** 94/94 passed in 13.79s (Python 3.14.7, pytest-9.1.1).

---

## 1. Inventory of Current Baseline Architecture

### 1.1 Containers & Network Topology
- `forum_postgres` (`postgres:16-alpine`, port 5432): Hosts 6 logical databases: `forum_users`, `forum_content`, `forum_interactions`, `forum_moderation`, `forum_notifications`, `forum_search`.
- `forum_redis` (`redis:7-alpine`, port 6379): Shared caching and rate limiting state.
- `forum_mailpit` (`axllent/mailpit:v1.31.6`, ports 1025, 8025): SMTP capture for testing.
- `forum_api_gateway` (port 8000): Ingress proxy, CSRF/cookie routing, path stripping, and header sanitization.
- Six microservices:
  - `forum_user_service` (port 8001)
  - `forum_content_service` (port 8002)
  - `forum_interaction_service` (port 8003)
  - `forum_moderation_service` (port 8004)
  - `forum_notification_service` (port 8005)
  - `forum_search_service` (port 8006)

### 1.2 Current Migration Heads
- `forum_users`: `users_0002 (head)`
- `forum_content`: `content_0003 (head)`
- `forum_interactions`: `interactions_0002 (head)`
- `forum_moderation`: `moderation_0003 (head)`
- `forum_notifications`: `notifications_0002 (head)`
- `forum_search`: `search_0002 (head)`

### 1.3 Outbox Worker Subsystem
- Autonomous background polling worker (`OutboxWorker`) instantiated per event-producing service (`user`, `content`, `interaction`, `moderation`).
- Uses `SELECT ... FOR UPDATE SKIP LOCKED` on PostgreSQL to claim disjoint batches of events.
- Atomic state transitions: `PENDING` -> `PROCESSING` -> `DELIVERED` or `FAILED` (with exponential backoff and jitter) or `DEAD_LETTER`.
- Stale claim recovery: Reclaims stranded `PROCESSING` events after `lock_timeout_seconds`.
- Stale lease guard: Rejects obsolete worker updates if the claim was stolen or already marked `DELIVERED`.
- Consumer idempotency: `consumed_events` tables in `notifications`, `search`, and `content` prevent duplicate side effects.

---

## 2. Gaps & Risks Identified Before Task 016

1. **Observability & Operational Metrics (Phase B):**
   - Health endpoints (`/health/live`, `/health/ready`) only provide coarse boolean indicators without detailed pool statistics or metrics.
   - No standardized Prometheus metrics endpoint (`/metrics`) exists across the Gateway or services.
   - Outbox worker metrics (`processed_count`, `success_count`, `failure_count`, `dead_letter_count`) are currently stored in memory inside `self.metrics` but not exposed over an operational HTTP endpoint.
   - Pending/failed event age and queue depth are not surfaced to monitoring systems.
   - High-cardinality risks: Must ensure metrics endpoints do not emit user IDs, session tokens, or event IDs.

2. **Secrets & Staging Environment Configuration (Phase C):**
   - `BaseServiceSettings` and `UserSettings` currently allow default development secrets (`"default-internal-service-secret-key-32chars"`, `"default-dev-csrf-secret"`, `"forum_dev_password"`) when `ENVIRONMENT != "production"`.
   - Staging environment (`ENVIRONMENT="staging"`) must be strictly configured to reject default development secrets and fail-fast if mandatory keys are missing or insecure (<32 characters).
   - `.env.example` contains hardcoded credentials that need clean separation into template placeholders.

3. **Database Backup & Recovery (Phase D):**
   - No automated, native backup and restore script exists for the 6 logical PostgreSQL databases.
   - Manual `pg_dump` commands risk password leakage if not executed via standard environment file / container invocation.
   - Lack of an isolated restore verification drill demonstrating that dumps can be restored into fresh databases and pass Alembic/smoke validations without impacting live data.

4. **Graceful Shutdown & Worker Lifecycle (Phase E):**
   - Outbox worker loop cancellation suppresses `asyncio.CancelledError`, but does not guarantee drain of in-flight dispatches or surface shutdown metrics.
   - Repeated startup/shutdown lifespans in tests must be proven not to leak duplicate background tasks.

5. **Staging Runbooks & Deployment Guidelines (Phase F):**
   - Operational runbooks for staging deployments, rollback procedures, backup/restore schedules, and incident response are missing.

---

## 3. Planned Implementation Steps

1. **Phase B: Metrics & Observability Subsystem (`src/common/metrics.py`):**
   - Implement lightweight, zero-dependency Prometheus-compatible metrics registry and middleware.
   - Expose standard `/metrics` endpoint on the API Gateway and all six microservices.
   - Track HTTP request count by method, status code category, duration histogram/quantiles.
   - Track database pool utilization (`pool_size`, `checked_in`, `checked_out`, `overflow`).
   - Track outbox status breakdown (`pending`, `processing`, `delivered`, `failed`, `dead_letter`), oldest pending age, and delivery retry count.
2. **Phase C: Staging Configuration Hardening:**
   - Extend `BaseServiceSettings` with strict staging/production validators that reject insecure keys, weak internal service keys, and test SMTP hosts.
   - Provide clean `.env.example` and `.env.staging.example`.
3. **Phase D: Database Backup & Restore Automation (`scripts/backup/`):**
   - Implement `backup_databases.sh` and `restore_databases.sh` using containerized `pg_dump` / `pg_restore`.
   - Implement automated backup & isolated restore verification test in `tests/test_backup_restore.py`.
4. **Phase E: Graceful Shutdown & Failure-Injection Hardening:**
   - Enhance `OutboxWorker.stop()` with drain timeout and lifecycle state checks.
   - Add failure injection tests covering shutdown during active dispatch, repeated lifespan cycles, and stale claim recovery.
5. **Phase F & G: Runbooks, Acceptance Drill & Quality Gates:**
   - Produce all 4 operational runbooks (`backup_restore_runbook.md`, `staging_deployment_runbook.md`, `observability_runbook.md`, `incident_recovery_runbook.md`).
   - Run acceptance drill, verify quality gates, and produce `task_016_operational_readiness_report.md`.
