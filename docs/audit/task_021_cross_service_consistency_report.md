# Task 021: Cross-Service Consistency & Outbox Reliability Report

**Date:** 2026-10-21  
**Scope:** Transactional outbox event routing, producer/consumer mapping, idempotency guarantees, dead-letter queue recovery, and failure injection validation.

---

## 1. Domain Event Routing & Topology Matrix

The Forum Platform utilizes a strictly isolated database-per-service pattern with transactional outboxes. All cross-service events are persisted in `outbox_events` tables within the producer's logical database in the same database transaction as the business mutation, guaranteeing at-least-once delivery.

| Event Type | Producer Service | Consumer Service(s) | Business Impact / Consistency Target |
| :--- | :--- | :--- | :--- |
| `thread.created` | Content Service | Search Service, Notification Service | Search indexing (tsvector) & mentions notification |
| `thread.updated` | Content Service | Search Service | Search index refresh |
| `thread.deleted` | Content Service | Search Service, Interaction Service | Search index soft-deletion & cleanup |
| `comment.created` | Content Service | Notification Service, Interaction Service | Author comment notification & thread counter increment |
| `comment.deleted` | Content Service | Notification Service, Interaction Service | Thread counter decrement |
| `vote.cast` | Interaction Service | Content Service, Notification Service | Post score recalculation & author notification |
| `vote.retracted` | Interaction Service | Content Service | Post score recalculation |
| `report.created` | Moderation Service | Notification Service | Moderator queue alert |
| `report.resolved` | Moderation Service | Content Service, Notification Service | Content hide/lock enforcement & reporter feedback |
| `user.registered` | User Service | Notification Service | Welcome notification / metrics |
| `user.status_changed` | User Service | Content, Moderation, Interaction | Session invalidation & activity restriction |

---

## 2. Idempotency & Consumer De-duplication

### 2.1 Mechanics
Every consumer database contains an `inbox_events` or `processed_events` table indexed by `(event_id, consumer_name)`.
- When an event is dispatched by the Outbox Worker:
  1. The consumer executes an atomic `INSERT ... ON CONFLICT (event_id, consumer_name) DO NOTHING`.
  2. If the row already exists, the consumer immediately acknowledges with `200 OK` (Idempotent Hit) and skips business processing.
  3. If newly inserted, the consumer processes the payload within a local transaction and updates its local state.

### 2.2 Empirical Verification
- **Verified in `tests/test_outbox_hardening.py::test_consumer_idempotency_duplicate_events`:**
  - Multiple identical `thread.created` events dispatched concurrently.
  - Asserted exactly one search index record created; duplicate deliveries acknowledged cleanly with zero duplicate records created.

---

## 3. Worker Concurrency, Leases, and Dead-Letter Queue (DLQ)

### 3.1 Lease Claiming & Concurrency Protection
- **Implementation in `src/common/outbox.py`:**
  - Employs `SELECT ... FOR UPDATE SKIP LOCKED` combined with `lease_expires_at` leases (default: 300 seconds).
  - Multiple worker instances can run in parallel without competing or duplicate-processing the same event batch.
- **Verified in `tests/test_outbox_concurrency_live.py`:**
  - 3 concurrent worker processes ran against a shared database outbox.
  - Zero race conditions, deadlocks, or duplicate event claims detected.

### 3.2 Failure Injection & DLQ Transition
- **Implementation:**
  - Retries use exponential backoff up to `max_retries` (default: 5).
  - Once retry count exceeds `max_retries`, the event status transitions to `dead_letter`, capturing `last_error` and timestamp for administrative inspection.
- **Verified in `tests/test_outbox_hardening.py`:**
  - Network timeout simulation: event retried with incremented retry count.
  - Poison-pill simulation: event exceeded retry threshold and cleanly moved to `dead_letter` without halting worker loop.
  - Manual retry endpoint re-queued dead-lettered events back to `pending`.

---

## 4. Foreign Key & Isolation Verification

### 4.1 Cross-Service Foreign Key Audit
A full schema audit was executed across all 6 logical PostgreSQL databases:
- `forum_users`
- `forum_content`
- `forum_interactions`
- `forum_moderation`
- `forum_notifications`
- `forum_search`

**Finding:**
All foreign keys are strictly local to each service's own database schema. Cross-service entity identifiers (`user_id`, `thread_id`, `comment_id`, `report_id`) are stored as plain UUIDs or integers without database-level constraints. Consistency is maintained asynchronously via domain events.

---

## 5. Conclusion
- Cross-service event pipeline guarantees **at-least-once delivery** and **effective once-and-only-once processing** via consumer idempotency.
- Concurrency, lease management, and dead-letter queues are thoroughly verified.
- Status: **PASS**.
