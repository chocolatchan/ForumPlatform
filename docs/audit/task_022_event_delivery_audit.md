# Task 022: Cross-Service Event Delivery Audit

**Date:** 2026-10-21  
**Scope:** Transactional outbox event routing, producer/consumer mapping, idempotency guarantees, dead-letter queue recovery, and failure injection validation.

---

## 1. Domain Event Routing & Topology Matrix

The Forum Platform utilizes a strictly isolated database-per-service pattern with transactional outboxes. All cross-service events are persisted in `outbox_events` tables within the producer's logical database in the same database transaction as the business mutation, guaranteeing **at-least-once delivery**.

| Event Type | Producer Service | Outbox Table | Consumer Service(s) | Business Side Effect | Idempotency Mechanism | Retry Policy | DLQ Condition & Requeue | Verification Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `ThreadCreated` | Content Service | `content.outbox_events` | Search Service, Notification Service | Indexes thread in tsvector full-text search; alerts mentioned users | Consumer `search.consumed_events` and `notifications.consumed_events` (Unique on `event_id`) | Exponential backoff (2s, 4s, 8s, 16s, 32s) with random jitter [0.5, 1.5] | Moved to `dead_letter` after 5 failed attempts; requeued via DB / API | `test_outbox_hardening.py` |
| `ThreadUpdated` | Content Service | `content.outbox_events` | Search Service | Updates title/body/tags in tsvector search index | Deduplication in `search.consumed_events` | Exponential backoff (max 5 retries) | Moved to `dead_letter` after 5 failed attempts | `test_search_service.py` |
| `ThreadDeleted` | Content Service | `content.outbox_events` | Search Service | Soft-deletes or removes thread from search index | Deduplication in `search.consumed_events` | Exponential backoff (max 5 retries) | Moved to `dead_letter` after 5 failed attempts | `test_search_service.py` |
| `ThreadLockToggled` | Content Service | `content.outbox_events` | Search Service | Updates thread lock state in search index | Deduplication in `search.consumed_events` | Exponential backoff (max 5 retries) | Moved to `dead_letter` after 5 failed attempts | `test_content_service.py` |
| `ThreadPinToggled` | Content Service | `content.outbox_events` | Search Service | Updates thread pin status in search index | Deduplication in `search.consumed_events` | Exponential backoff (max 5 retries) | Moved to `dead_letter` after 5 failed attempts | `test_content_service.py` |
| `CommentCreated` | Content Service | `content.outbox_events` | Notification Service | Creates in-app notification for thread author | Insert into `notifications.consumed_events`; skips if recipient == actor | Exponential backoff (max 5 retries) | Moved to `dead_letter` after 5 failed attempts | `test_notification_service.py` |
| `BestAnswerChanged`| Content Service | `content.outbox_events` | Notification Service | Notifies comment author of accepted solution | Deduplication in `notifications.consumed_events` | Exponential backoff (max 5 retries) | Moved to `dead_letter` after 5 failed attempts | `test_notification_service.py` |
| `VoteChanged` | Interaction Service| `interactions.outbox_events`| Notification Service | Generates upvote notification if recipient preferences permit | Deduplication in `notifications.consumed_events`; records `skipped_by_preference` if disabled | Exponential backoff (max 5 retries) | Moved to `dead_letter` after 5 failed attempts | `test_notification_service.py` |
| `ThreadViewCountIncremented`| Interaction Service| `interactions.outbox_events`| Content Service, Search Service | Increments cached view counter in `content.threads.view_count` | Deduplication in `content.consumed_events` (`duplicate: true`, increment: 0) | Exponential backoff (max 5 retries) | Moved to `dead_letter` after 5 failed attempts | `test_outbox_hardening.py` |
| `ModerationActionApplied`| Moderation Service| `moderation.outbox_events`| Search Service, Notification Service | Applies lock/hide in search index; notifies content owner of moderation action | Deduplication in `search.consumed_events` and `notifications.consumed_events` | Exponential backoff (max 5 retries) | Moved to `dead_letter` after 5 failed attempts | `test_moderation_service.py` |
| `UserRegistered` | User Service | `users.outbox_events` | Notification Service | Sends system welcome notification | Deduplication in `notifications.consumed_events` | Exponential backoff (max 5 retries) | Moved to `dead_letter` after 5 failed attempts | `test_user_auth_lifecycle.py` |

---

## 2. Empirical Reliability & Fault-Injection Verification

### 2.1 Duplicate Delivery & Consumer Idempotency
- **Test:** [`tests/test_outbox_hardening.py::test_consumer_idempotency_duplicate_events`](file:///home/chocolat/Documents/Project/ForumPlatform/tests/test_outbox_hardening.py)
- **Scenario:** 10 concurrent duplicate dispatches of the same event payload.
- **Finding:** First attempt creates side effect (`processed: true`); subsequent 9 deliveries detect existing `event_id` in `consumed_events` table and return `duplicate: true` with zero duplicate side effects.

### 2.2 Lost HTTP Response Fault Injection
- **Test:** [`tests/test_outbox_hardening.py::test_lost_response_controlled_producer_dispatch`](file:///home/chocolat/Documents/Project/ForumPlatform/tests/test_outbox_hardening.py)
- **Scenario:** Consumer commits event, but a custom network transport injects `httpx.ConnectTimeout` back to the producer before the HTTP response is received.
- **Finding:** Producer marks event as `FAILED` (attempts=1). On the subsequent retry cycle, producer redelivers to consumer. Consumer acknowledges idempotently, and producer cleanly advances status to `DELIVERED` without duplicating database state.

### 2.3 Stale Worker Lease Recovery
- **Test:** [`tests/test_outbox_concurrency_live.py::test_stale_claim_recovery_after_worker_crash`](file:///home/chocolat/Documents/Project/ForumPlatform/tests/test_outbox_concurrency_live.py)
- **Scenario:** An outbox event remains locked in `PROCESSING` state by a dead worker after `lock_timeout_seconds` (30s) expires.
- **Finding:** Recovery worker executes `SELECT FOR UPDATE SKIP LOCKED` where `locked_at < lock_cutoff`, reclaims the event, increments attempts, and successfully finishes delivery.

### 2.4 Dead-Letter Queue (DLQ) Transition
- **Test:** [`tests/test_outbox_hardening.py::test_poison_pill_dead_letter_transition`](file:///home/chocolat/Documents/Project/ForumPlatform/tests/test_outbox_hardening.py)
- **Scenario:** An event consistently receives non-retryable 4xx errors or exhausts maximum retry attempts (max_attempts=3).
- **Finding:** Outbox worker captures the stack trace in `last_error`, transitions event status to `DEAD_LETTER`, and continues processing the remainder of the batch without halting the worker loop.

---

## 3. Semantics Affirmation

- **Semantics:** **At-least-once delivery with idempotent consumer execution**.
- Exactly-once delivery is **NOT** claimed and is not supported by distributed network physics.
- **Verdict:** **`PASS`**.
