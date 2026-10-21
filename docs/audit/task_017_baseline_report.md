# Task 017: Baseline Report — Staging Release Candidate Validation

**Date**: 2026-10-21  
**Project**: Forum Platform Microservices  
**Host**: `dyuxworkstation` (Linux 7.2.9-1-cachyos, x86_64, 16 vCPUs, 15.28 GiB RAM)  
**Evaluator**: Antigravity Autonomous Agent  
**Status**: **BASELINE ESTABLISHED**

---

## 1. System & Environment Inventory

### 1.1. Tooling & Runtimes
- **Python**: 3.14.7
- **Pytest**: 9.1.1
- **Docker Engine**: 29.8.2 (build `7fc2dff9bc`)
- **Docker Compose**: 5.6.0
- **PostgreSQL**: 16-Alpine (PostgreSQL 16.8)
- **Redis**: 7-Alpine (Redis 7.2)
- **Mailpit**: v1.31.6

### 1.2. Source Code & Git State
- **Branch**: `master`
- **Commit**: `050667ffbf5175ea2c61518555b766e3872019b5`
- **Working Tree State**: Clean working tree with Task 016 additions:
  - Metric exposition middleware (`src/common/metrics.py`)
  - Connection pool tracking (`src/common/database.py`)
  - Autonomous outbox drain and duplicate protection (`src/common/outbox_worker.py`)
  - Multi-database backup & restore suite (`scripts/backup/`)
  - Operational runbooks (`docs/operations/`)
  - Automated staging operational readiness tests (`tests/test_staging_operational_readiness.py`, `tests/test_backup_restore.py`)

### 1.3. Active Docker Compose Service Topology
10/10 containers active and healthy:
1. `forum_api_gateway` (`gateway`): port `8000` (healthy)
2. `forum_user_service` (`user-service`): port `8001` (healthy)
3. `forum_content_service` (`content-service`): port `8002` (healthy)
4. `forum_interaction_service` (`interaction-service`): port `8003` (healthy)
5. `forum_moderation_service` (`moderation-service`): port `8004` (healthy)
6. `forum_notification_service` (`notification-service`): port `8005` (healthy)
7. `forum_search_service` (`search-service`): port `8006` (healthy)
8. `forum_postgres` (`postgres`): port `5432` (healthy)
9. `forum_redis` (`redis`): port `6379` (healthy)
10. `forum_mailpit` (`mailpit`): port `8025` (web UI) / `1025` (SMTP) (healthy)

### 1.4. Database Isolation & Migration Heads
All 6 logical PostgreSQL databases exist, maintain isolated schemas, and are synchronized to their latest Alembic migration heads:
- `forum_users` (schema `users`): `users_0002` (head)
- `forum_content` (schema `content`): `content_0003` (head)
- `forum_interactions` (schema `interactions`): `interactions_0002` (head)
- `forum_moderation` (schema `moderation`): `moderation_0003` (head)
- `forum_notifications` (schema `notifications`): `notifications_0002` (head)
- `forum_search` (schema `search`): `search_0002` (head)

---

## 2. Invariant & Isolation Assessment
- Service data isolation is preserved across all microservices (DEC-001 & DEC-002).
- At-least-once event delivery semantics are maintained with outbox tables, idempotent consumer tracking, and bounded backoff (DEC-003).
- Opaque server-side session authentication with signed CSRF double-submit protection is enforced (DEC-004).
- View event deduplication is handled by Interaction Service, while aggregate display count is owned by Content Service (DEC-005).
- All backup drills use isolated prefixes (`restore_drill_*`) to guarantee zero risk of developer or staging data corruption.
