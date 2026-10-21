# Task 023: Reproducible Baseline Benchmark Report

## 1. Overview & Test Environment

A reproducible performance baseline was executed using the audited benchmark harness against the live 10-container Docker Compose deployment.

- **Run ID:** `baseline_20261021_122538`
- **Target URL:** `http://localhost:8000` (API Gateway)
- **Host Architecture:** Linux 6.6.137+rpt-rpi-2712, aarch64, 4 physical cores, 16 GB RAM
- **Docker Compose Containers:**
  - `forum_api_gateway` (Port 8000)
  - `forum_user_service` (Port 8001)
  - `forum_content_service` (Port 8002)
  - `forum_interaction_service` (Port 8003)
  - `forum_moderation_service` (Port 8004)
  - `forum_notification_service` (Port 8005)
  - `forum_search_service` (Port 8006)
  - `forum_postgres` (PostgreSQL 16 Alpine, Port 5432)
  - `forum_redis` (Redis 7 Alpine, Port 6379)
  - `forum_mailpit` (Port 8025)

---

## 2. Workload Definitions & Measurement Results

Each baseline workload was executed across 3 distinct runs following a 50-request warm-up.

### Summary Results Table

| Workload ID | Description | Concurrency | Total Requests per Run | Average Achieved RPS | Latency p50 (ms) | Latency p95 (ms) | Latency p99 (ms) | Success Rate |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **WL1** | Public Categories Read (`GET /api/v1/categories`) | 10 | 200 | **622.0** | 14.5 | 24.6 | 29.2 | 100.0% |
| **WL2** | Public Threads Paginated Read (`GET /api/v1/threads?limit=20`) | 10 | 200 | **508.2** | 17.8 | 32.3 | 46.0 | 100.0% |
| **WL3** | Keyword Search (`GET /api/v1/search?q=...`) | 10 | 200 | **565.9** | 16.3 | 25.9 | 33.2 | 100.0% |
| **WL4** | Authenticated Profile Read (`GET /api/v1/profile` with session) | 10 | 200 | **468.2** | 19.7 | 30.4 | 36.9 | 100.0% |
| **WL5** | Thread Creation Mutation (`POST /api/v1/threads` + Outbox Event) | 5 | 50 | **208.2** | 21.9 | 33.0 | 37.0 | 100.0% |
| **WL6** | Mixed Realistic Workflow (70% read, 20% profile, 10% write) | 15 | 200 | **406.6** | 33.6 | 57.0 | 74.8 | 100.0% |

---

## 3. Workload Analysis & Observations

1. **Read Throughput:**
   - Simple cache-friendly reads (WL1) deliver the highest raw throughput at ~622 RPS with sub-25ms p95 latencies.
   - Paginated database thread listings (WL2) drop to ~508 RPS due to SQL joining and multi-row serialization, but maintain stable p50 around 17.8ms.
   - Search queries (WL3) achieve ~566 RPS, demonstrating responsive full-text indexing via PostgreSQL tsvector queries.
2. **Authentication Overhead (WL4):**
   - Authenticated profile requests achieve ~468 RPS compared to 622 RPS on unauthenticated reads. The ~25% throughput delta accounts for:
     - API Gateway extracting the `forum_session` cookie.
     - Redis lookup verifying session state.
     - Reverse-proxy header injection (`X-User-ID`, `X-User-Role`).
     - User service querying database for user profile details.
3. **Transactional Outbox Write Mutation (WL5):**
   - Mutating thread creations achieve ~208 RPS at Concurrency=5.
   - Each request executes an atomic database transaction inserting both the `content.threads` record and a `content.outbox_events` message.
   - The asynchronous background outbox worker automatically claims, delivers, and moves events to `delivered` status within milliseconds without stalling user writes.
4. **Mixed Journey (WL6):**
   - Combining reads, profile inspections, and thread writes produces a balanced throughput of ~406.6 RPS with p95 latency under 60ms.

---

## 4. Evidence Traceability

- Raw request trace: `docs/audit/performance/task_023/baseline_20261021_122538/raw_requests.jsonl`
- Run summary: `docs/audit/performance/task_023/baseline_20261021_122538/summary.json`
- Hardware & container manifest: `docs/audit/performance/task_023/baseline_20261021_122538/manifest.json`
- SHA-256 Checksums: `docs/audit/performance/task_023/baseline_20261021_122538/checksums.sha256`
