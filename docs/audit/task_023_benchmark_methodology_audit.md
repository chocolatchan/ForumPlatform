# Task 023: Benchmark Methodology Audit & Integrity Assessment

## 1. Executive Summary

This audit assesses the historical performance benchmark harness (`scripts/run_benchmarks.py`, `scripts/run_docker_benchmarks.py`) and historical benchmark results (`docs/audit/benchmark_results.json`) generated during earlier phases (Task 014 / Task 015).

**Audit Verdict on Historical Benchmarks:** **INVALID AND NON-REPRODUCIBLE FOR PRODUCTION SIZING**

The historical benchmark script bypassed the actual microservice network, API Gateway routing, session cookies in Redis, PostgreSQL transactions, and the distributed outbox pattern by invoking FastAPI endpoints directly in-process against an in-memory or SQLite database (`sqlite+aiosqlite`).

Consequently, historical throughput numbers (e.g., claiming thousands of RPS) reflected single-process Python CPU loops rather than actual multi-container network, gateway proxy, connection pooling, and database disk I/O.

---

## 2. Methodology & Harness Flaw Analysis

| Dimension | Historical Harness (`scripts/run_benchmarks.py`) | Audited Task 023 Harness (`scripts/bench_harness.py`) | Integrity Impact |
| :--- | :--- | :--- | :--- |
| **Network Path** | Direct in-process ASGI calls via `httpx.ASGITransport(app=...)`. Bypassed TCP stack, network sockets, and reverse proxy. | Out-of-process HTTP/1.1 calls targeting API Gateway over real TCP sockets (`http://localhost:8000`). | High. Network serialization, reverse proxy hop, and socket overhead were omitted historically. |
| **Target Architecture** | Single monolithic process running simulated app routes. | Full 10-container Docker Compose topology: API Gateway, 6 independent microservices, PostgreSQL 16 Alpine, Redis 7 Alpine, Mailpit. | High. Process isolation, inter-service HTTP hops, and distributed state were not tested. |
| **Database Backend** | In-memory SQLite (`sqlite+aiosqlite:///:memory:`). Single thread concurrency locks, no disk WAL, no TCP connection pool. | PostgreSQL 16 Alpine over Docker bridge network with individual service databases (`forum_users`, `forum_content`, `forum_interactions`, etc.) and real connection pools (`pool_size=5`, `max_overflow=10`). | Critical. Database contention, connection checkout, schema isolation, and lock contention were invisible. |
| **Session & Auth** | Mocked user headers or local dictionaries; bypassed Redis session store. | Real opaque 64-character session tokens stored in Redis (`forum_session`), validated by API Gateway on every authenticated request with session-bound HMAC CSRF tokens. | High. Redis roundtrips and cryptographic CSRF validation overhead were ignored. |
| **Outbox & Events** | No asynchronous event worker or background broker polling. | Real transactional outbox table (`content.outbox_events`), background publisher loop, and downstream indexing into `search.search_index`. | Critical. Background event processing overhead and database write contention were missed. |
| **Error Accounting** | Silently dropped HTTP 4xx/5xx responses or counted them as successful if the HTTP transport did not throw an exception. | Lossless raw JSONL trace recording every request, exact HTTP status codes, latency percentiles (p50, p90, p95, p99, min, max), and strict error categorization. | Critical. Latency degradation and failure rates were hidden. |

---

## 3. Discovered Measurement Biases

1. **Warm-up Absence in Historical Runs:** Historical scripts measured cold starts without separate warm-up phases, conflating JIT/import overhead with steady-state response times.
2. **Load Generator Co-location & Starvation:** When running load tests on a development machine alongside Docker containers, aggressive unthrottled loops can starve the gateway or microservices of CPU cycles, creating artificial client-side queue delays. The Task 023 harness introduces concurrency-limited pools and explicit rate control.
3. **Hot-Key Cache Distortion:** Querying a single static endpoint (e.g., categories) repeatedly yields 100% cache hits in reverse proxies or application memory, inflating RPS figures up to 2–3x compared to dispersed query patterns.

---

## 4. Remediation & Validation Standards Established in Task 023

1. **Lossless Request Tracing:** Every benchmark run produces a `raw_requests.jsonl` recording `timestamp`, `method`, `path`, `status_code`, `latency_ms`, and `error`.
2. **Multi-Run Repetition:** Baseline workloads are executed across at least 3 distinct runs to compute variance and confidence intervals.
3. **Cryptographic Artifact Integrity:** All benchmark artifacts (`raw_requests.jsonl`, `summary.json`, `manifest.json`) are hashed with SHA-256 and committed into versioned directories under `docs/audit/performance/task_023/<run_id>/`.
4. **Hardware & Environment Manifest:** System specifications (Linux kernel, CPU architecture, core count, memory) and pre/post container memory/CPU metrics are preserved in every manifest.
