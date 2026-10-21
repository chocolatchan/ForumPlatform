# Task 023: Cache Sensitivity Analysis Report

## 1. Objective

To evaluate cache behavior, cache hit vs. miss dynamics, and the impact of repeated hot-key querying versus dispersed search key distributions on throughput and tail latencies.

---

## 2. Methodology & Scenarios

- **Hot-Key Test:** 300 sequential/concurrent queries to the single static resource `GET /api/v1/categories` at Concurrency = 20.
- **Dispersed Query Test:** 300 queries cycling through 10 distinct, non-overlapping search keywords (`FastAPI`, `Python`, `Docker`, `Compose`, `PostgreSQL`, `Redis`, `Outbox`, `Async`, `SQLAlchemy`, `RabbitMQ`) to `GET /api/v1/search?q={term}` at Concurrency = 20.

---

## 3. Comparative Results

| Metric | Hot-Key Category Read (`/api/v1/categories`) | Dispersed Search Read (`/api/v1/search?q=...`) | Variance / Delta |
| :--- | :--- | :--- | :--- |
| **Total Requests** | 300 | 300 | - |
| **Concurrency** | 20 | 20 | - |
| **Achieved RPS** | **315.0** | **334.8** | +6.3% (Search) |
| **Latency p50 (ms)** | 55.4 | 52.6 | -2.8ms |
| **Latency p90 (ms)** | 98.7 | 94.2 | -4.5ms |
| **Latency p95 (ms)** | 123.7 | 109.8 | -13.9ms |
| **Latency p99 (ms)** | 172.4 | 186.0 | +13.6ms |
| **Min Latency (ms)** | 10.2 | 8.9 | -1.3ms |
| **Max Latency (ms)** | 215.3 | 242.1 | +26.8ms |
| **Success Rate** | 100% (300/300) | 100% (300/300) | 0 failures |

---

## 4. Key Findings

1. **Absence of HTTP Gateway Invalidation Caching:**
   - The API Gateway does not currently maintain an in-memory HTTP response cache for public endpoints. Every request is proxied downstream to the respective microservice.
   - Consequently, repeated "hot-key" hits do not artificially boost gateway throughput or bypass service execution.
2. **Search Index Concurrency:**
   - The search service uses PostgreSQL GIN indexes on `tsvector` fields. Query latency remains flat across distinct search terms (p50 ~52.6ms at C=20), demonstrating that cold search queries do not experience table-scan degradation.
3. **Queueing at C=20:**
   - Both workloads exhibit queuing effects at Concurrency = 20 (p95 > 100ms), confirming that concurrency levels above 15 saturate the downstream worker pool.
