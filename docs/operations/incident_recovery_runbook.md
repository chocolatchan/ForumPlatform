# Operational Runbook: Incident Response & Disaster Recovery

**Document Version:** 1.0.0  
**Scope:** Service Outages, Outbox Dead-Letter Triage, and Crash Recovery  
**Target Environment:** Staging and Production  

---

## 1. Downstream Service Outage & Automatic Recovery

### Symptoms
- Gateway returns 502/503 for routes pointing to the unavailable service.
- Upstream outbox workers log:
  `[<producer> -> <consumer>] Network exception delivering event ...`
- `outbox_worker_failed_total` increments; outbox events transition to `status="failed"`.

### Recovery Mechanism
1. Outbox events are not lost; they are retained in PostgreSQL outbox tables.
2. The producer automatically recalculates exponential backoff with jitter:
   ```python
   delay = min(60.0, base_delay * (2 ** (attempt - 1)) * jitter)
   ```
3. Once the downstream service recovers and passes `/health/ready`, the outbox worker automatically resumes delivery on the next retry timestamp.
4. Downstream consumers employ idempotent ingestion (`consumed_events` table) to prevent duplicate processing if a response was lost during the outage.

---

## 2. Worker Crash or Hard Kill Recovery

### Symptoms
- An event was claimed (`status="processing"`, `locked_by="<worker_id>"`), but the container was killed before delivery completed.

### Automatic Lease Recovery
1. The lease timeout is configured via `lock_timeout_seconds` (default: 60s).
2. Any running or restarted worker queries for stale claims:
   ```sql
   SELECT * FROM outbox_events
   WHERE status = 'processing' AND locked_at < (NOW() - INTERVAL '60 seconds')
   FOR UPDATE SKIP LOCKED;
   ```
3. The restarted worker claims the stranded event, increments `stale_reclaimed_count`, and proceeds with delivery.

---

## 3. Dead-Letter Queue (DLQ) Triage

### Triage Procedure
When `outbox_worker_dead_letter_total > 0`:
1. Connect to the affected service database:
   ```bash
   docker compose exec postgres psql -U forum_admin -d forum_content -c "SELECT id, event_type, attempts, last_error FROM content.outbox_events WHERE status = 'dead_letter';"
   ```
2. Inspect `last_error` column. Typical reasons:
   - Schema validation failure on consumer (e.g. HTTP 422).
   - Malformed payload or missing required field.
   - Max retry attempts exceeded (5 attempts).
3. If payload issue is resolved or consumer is patched, re-queue events for delivery:
   ```sql
   UPDATE content.outbox_events
   SET status = 'pending', attempts = 0, next_retry_at = NOW()
   WHERE id = '<EVENT_UUID>';
   ```
4. Verify worker picks up the event via `/metrics`:
   ```bash
   curl -s http://localhost:8002/metrics | grep "outbox_worker_delivered_total"
   ```

---

## 4. Secret Rotation Procedure

### 4.1 Internal Service Key Rotation (`INTERNAL_SERVICE_SECRET`)
The internal service secret authenticates direct service-to-service calls over `/internal/events`.
1. Generate new 32+ character random secret:
   ```bash
   openssl rand -hex 32
   ```
2. Update `INTERNAL_SERVICE_SECRET` in `.env`.
3. To achieve zero-downtime rotation, deploy consumers first (supporting both current and next keys if multi-key auth is configured) or perform a rolling restart:
   ```bash
   docker compose up -d
   ```
4. Outbox workers will automatically reconnect and use the updated secret. Any temporary 401s during the restart window will retry and clear.
