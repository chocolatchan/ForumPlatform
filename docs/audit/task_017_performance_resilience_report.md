# Task 017: Performance & Resilience Validation Report

**Date**: 2026-10-21  
**Project**: Forum Platform Microservices  
**Host Environment**: Linux dyuxworkstation (Intel i7-13620H, 16 vCPUs, 15.28 GiB RAM), Docker Engine 29.8.2  
**Evaluator**: Antigravity Autonomous Agent  
**Result**: **PASS**

---

## 1. Live Docker Benchmark Results

The benchmark harness was executed directly against the Docker Compose API Gateway on port 8000 and PostgreSQL 16 on port 5432 using `scripts/run_docker_benchmarks.py`.

### 1.1. Core Scenario Summary

| Workload Scenario | Requests | Concurrency | Duration (s) | Throughput (RPS) | p50 Latency (ms) | p95 Latency (ms) | p99 Latency (ms) | Success Rate |
|---|---|---|---|---|---|---|---|---|
| **Public Reads: Categories** | 200 | 10 | 0.96s | 208.6 RPS | 43.3 ms | 80.4 ms | 102.8 ms | 100% (200/200) |
| **Public Reads: Threads** | 200 | 10 | 1.06s | 187.8 RPS | 47.3 ms | 79.9 ms | 100.4 ms | 100% (200/200) |
| **Search: Keyword Query** | 150 | 10 | 0.78s | 192.5 RPS | 47.3 ms | 80.4 ms | 99.1 ms | 100% (150/150) |
| **Search: Suggestions** | 150 | 10 | 0.71s | 210.8 RPS | 44.5 ms | 64.7 ms | 73.2 ms | 100% (150/150) |
| **Profile Access (Unauth)** | 150 | 10 | 0.82s | 182.8 RPS | 50.1 ms | 87.4 ms | 101.2 ms | 100% (401 Canonical) |

### 1.2. Concurrency Progression Scaling

| Concurrency (C) | Total Requests | Throughput (RPS) | p50 Latency (ms) | p95 Latency (ms) | p99 Latency (ms) | Success Rate |
|---|---|---|---|---|---|---|
| **C = 1** | 150 | 91.1 RPS | 10.6 ms | 13.2 ms | 14.5 ms | 100% |
| **C = 5** | 150 | 189.5 RPS | 25.0 ms | 35.8 ms | 43.5 ms | 100% |
| **C = 20** | 150 | 152.3 RPS | 117.1 ms | 217.6 ms | 377.6 ms | 100% |
| **C = 50** | 150 | 192.1 RPS | 178.8 ms | 500.1 ms | 719.8 ms | 100% |

- **Observations**: Read operations sustain ~190–210 RPS with p50 latencies comfortably under 50 ms at moderate concurrency (C=10). Under elevated concurrency (C=50), zero requests fail (0 dropouts or 5xx errors).

---

## 2. Fault Injection & Recovery Validation

### 2.1. Upstream Service Failure Simulation
- **Fault**: Temporarily stopped `forum_notification_service` container.
- **Gateway Behavior**:
  - Outgoing HTTP request to `/api/v1/notifications` returned canonical `HTTP 503 Service Unavailable` with `UPSTREAM_UNAVAILABLE` code.
  - Gateway health endpoint (`/health/live`) remained healthy (`200 OK`).
  - Unrelated services (Categories, Threads, Search, Users) continued processing requests with zero degradation.
- **Recovery**:
  - Container restarted (`docker start forum_notification_service`).
  - Health checks transitioned to `healthy` within 10 seconds.
  - Gateway immediately resumed normal routing to `/api/v1/notifications` with 200 OK responses.

### 2.2. Outbox Worker Fault Recovery
- **Fault & Recovery**: Tested in `tests/test_outbox_concurrency_live.py::test_stale_claim_recovery_after_worker_crash`.
- **Behavior**: An outbox event left in `PROCESSING` state by a simulated crashed worker was automatically reclaimed after `lock_timeout_seconds` elapsed and delivered successfully to target consumers without human intervention.

---

## 3. Performance & Resilience Conclusion

The Forum Platform exhibits predictable throughput and sub-50ms p50 read latencies under normal load, isolates single-service upstream failures cleanly via the API Gateway, and recovers autonomously from worker interruptions.
