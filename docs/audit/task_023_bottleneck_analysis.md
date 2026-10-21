# Task 023: Bottleneck Analysis & Profiling

## 1. Overview & Resource Accounting

During the multi-stage benchmark execution (Baselines WL1–WL6, Cache Sensitivity, Saturation Ramp up to C=200, and 1,000 Simulated Concurrent Users), resource utilization across all 10 containers was systematically profiled.

### Peak Host & Container Utilization:
- **Host Specs:** 4 physical cores, 16 GB RAM (Linux kernel 6.6)
- **Host CPU Peak:** 28.4% total host CPU during maximum throughput peaks (~520 RPS).
- **Host Memory Peak:** System-wide RAM consumption by all 10 containers remained under **650 MiB** (out of 15.28 GiB total host memory, $\approx 4.2\%$).
- **No container exceeded memory limits or experienced memory growth over time.**

---

## 2. Component-by-Component Bottleneck Ranking

| Rank | Component / Layer | Primary Limitation Observed | Evidence & Metrics | Remediation / Recommendation |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **PostgreSQL Connection Pool Sizing** | Contention when concurrency exceeds pool ceiling (`pool_size=5, max_overflow=10`). | At $C \ge 25$, latency climbs steeply from 33.9ms to 136.0ms while CPU utilization remains low ($< 5\%$). | Deploy **PgBouncer** or scale connection pool ceiling per service container for production. |
| **2** | **Reverse Proxy In-Flight Socket Queuing** | API Gateway connection pool limit (`httpx.AsyncClient` limits) under burst concurrency. | Single client disconnect (`RemoteProtocolError`) under 1,000-user burst arrival; p95 latency tail elongation. | Configure reverse proxy connection keep-alive timeouts and HTTP/2 or HTTP/3 multiplexing. |
| **3** | **Single Uvicorn Worker per Container** | CPU core affinity limitation. Each microservice container runs 1 Uvicorn worker process. | Maximum throughput plateaus at ~510–530 RPS despite host having idle CPU capacity. | Scale Uvicorn worker count (`uvicorn --workers 4`) or run multiple container replicas behind a load balancer. |
| **4** | **Transactional Outbox Worker Batch Interval** | Polling loop frequency determines end-to-end event indexing latency. | Outbox worker polls every 1.0s, yielding a maximum single-worker throughput of ~50–100 events/sec. | Implement PostgreSQL `LISTEN / NOTIFY` or Kafka/RabbitMQ broker for near-zero latency notification. |

---

## 3. Profiling Evidence & Flame Graph Observations

1. **Uvicorn & ASGI Dispatch:**
   - JSON serialization via Pydantic (`pydantic.BaseModel`) accounts for $\approx 35\%$ of total Python CPU time on read endpoints.
   - Database model to API response mapping is the primary CPU consumer inside `forum_content_service` and `forum_user_service`.
2. **PostgreSQL Execution Times:**
   - Database queries themselves remain exceptionally fast ($< 3\text{ms}$ execution time for `SELECT * FROM content.threads ORDER BY created_at DESC LIMIT 10` using indexes).
   - The majority of request latency at high concurrency is **wait time in queue**, not SQL execution time.
