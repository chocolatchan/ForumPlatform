# Operational Runbook: Observability & Monitoring

**Document Version:** 1.0.0  
**Scope:** API Gateway and 6 Microservices Prometheus Metrics Exposition  
**Target Environment:** Staging and Production  

---

## 1. Metrics Exposition Architecture

Every service exposes a standardized Prometheus text-format endpoint at `/metrics`:
- **API Gateway:** `http://localhost:8000/metrics`
- **User Service:** `http://localhost:8001/metrics`
- **Content Service:** `http://localhost:8002/metrics`
- **Interaction Service:** `http://localhost:8003/metrics`
- **Moderation Service:** `http://localhost:8004/metrics`
- **Notification Service:** `http://localhost:8005/metrics`
- **Search Service:** `http://localhost:8006/metrics`

The metrics layer is implemented in [`src/common/metrics.py`](file:///home/chocolat/Documents/Project/ForumPlatform/src/common/metrics.py) with zero external C-dependencies, ensuring thread-safe, low-overhead in-memory aggregation.

---

## 2. Metric Signals Inventory

### 2.1 HTTP Request Metrics
- `http_requests_total{service, method, status_code, status_class}`: Monotonically increasing counter of processed requests.
- `http_request_duration_seconds{service, method, status_code, status_class}`: Gauge tracking request latency.

### 2.2 Database Pool Metrics
- `database_pool_size{service}`: Total configured pool capacity.
- `database_pool_checked_in{service}`: Number of available/idle connections.
- `database_pool_checked_out{service}`: Number of active connections in use.
- `database_pool_overflow{service}`: Active overflow connections beyond pool size.

### 2.3 Transactional Outbox Subsystem Metrics
- `outbox_events_count{service, status}`: Current number of events in PostgreSQL outbox table broken down by `pending`, `processing`, `delivered`, `failed`, and `dead_letter`.
- `outbox_oldest_event_age_seconds{service}`: Age in seconds of the oldest uncompleted active or pending event.
- `outbox_worker_claimed_total{service}`: Total count of events claimed by worker.
- `outbox_worker_delivered_total{service}`: Total count of successfully dispatched events.
- `outbox_worker_failed_total{service}`: Total count of events that encountered transient errors.
- `outbox_worker_dead_letter_total{service}`: Total count of events moved to `dead_letter`.
- `outbox_worker_stale_reclaimed_total{service}`: Total count of stranded events reclaimed after lock timeout.
- `outbox_worker_last_delivery_latency_ms{service}`: Real-time duration of last event HTTP delivery.
- `outbox_worker_running{service}`: Gauge (1=running, 0=stopped) tracking worker task health.

---

## 3. High-Cardinality & Sanitization Rules

1. **Path Normalization:** Arbitrary request paths (e.g. `/api/v1/threads/123e4567-e89b-...`) are **never** included as metric label dimensions to prevent memory leaks in Prometheus.
2. **Sensitive Fields:** User IDs, usernames, passwords, tokens, cookies, and secrets are strictly excluded from metric names and labels.

---

## 4. Key Alerting Thresholds

| Alert Name | PromQL Expression / Condition | Severity | Action |
|:---|:---|:---:|:---|
| **HighDeadLetterCount** | `outbox_worker_dead_letter_total > 0` | Warning | Inspect failed event payload and consumer error log. |
| **OutboxDeliveryLag** | `outbox_oldest_event_age_seconds > 300` | Critical | Check downstream consumer health or network partition. |
| **WorkerStopped** | `outbox_worker_running == 0` | Critical | Inspect service error log for fatal uncaught exceptions. |
| **DatabasePoolExhaustion** | `database_pool_checked_out / database_pool_size > 0.85` | Warning | Check for slow queries or connection leaks. |
| **HighHTTPErrorRate** | `rate(http_requests_total{status_class="5xx"}[5m]) / rate(http_requests_total[5m]) > 0.05` | Critical | Investigate 5xx root cause on target service. |
