# Task 022-R1: Event Consumer Idempotency & Reliability Matrix

**Date:** 2026-10-21  
**Scope:** Exhaustive audit of all 11 domain event types and their 15 consumer pairs across Content, Search, and Notification services. Verification of atomic deduplication, concurrent duplicate handling, lost HTTP responses, and live PostgreSQL test coverage.

---

## 1. Event Routing & Consumer Idempotency Matrix

The Forum Platform implements an **at-least-once delivery** model via Transactional Outbox tables (`users.outbox_events`, `content.outbox_events`, `interactions.outbox_events`, `moderation.outbox_events`). **Exactly-once delivery is NOT claimed.** Consumer idempotency is enforced by persistent deduplication records in `consumed_events` tables.

| # | Event Type | Producer Service | Consumer Service | Deduplication Key & Unique Constraint | Atomic Commit (Side Effect + Deduplication) | Verified in Real PostgreSQL? | Verification Test / Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | `ThreadCreated` | Content Service | Search Service | `search.consumed_events(event_id)` (Unique on `event_id`) | **Yes** (Both committed in single DB transaction) | **Yes** (Real PG container) | `tests/test_outbox_hardening.py::test_lost_response_consumer_idempotency_search` |
| 2 | `ThreadCreated` | Content Service | Notification Service | `notifications.consumed_events(event_id, recipient_id)` | **Yes** (Handled in `EventHandlerRegistry.process_event`) | **Yes** (Real PG container) | `tests/test_notification_service.py` (Ingestion tests) |
| 3 | `ThreadUpdated` | Content Service | Search Service | `search.consumed_events(event_id)` (Unique on `event_id`) | **Yes** (Search document update + consumed record) | **Yes** (Real PG container) | `tests/test_search_service.py` |
| 4 | `ThreadDeleted` | Content Service | Search Service | `search.consumed_events(event_id)` (Unique on `event_id`) | **Yes** (Search document soft-delete + consumed record) | **Yes** (Real PG container) | `tests/test_search_service.py` |
| 5 | `ThreadLockToggled` | Content Service | Search Service | `search.consumed_events(event_id)` (Unique on `event_id`) | **Yes** (Lock flag update + consumed record) | **Yes** (Real PG container) | `tests/test_search_service.py` |
| 6 | `ThreadPinToggled` | Content Service | Search Service | `search.consumed_events(event_id)` (Unique on `event_id`) | **Yes** (Pin flag update + consumed record) | **Yes** (Real PG container) | `tests/test_search_service.py` |
| 7 | `CommentCreated` | Content Service | Notification Service | `notifications.consumed_events(event_id, recipient_id)` | **Yes** (Notification item + consumed record) | **Yes** (Real PG container) | `tests/test_outbox_hardening.py::test_lost_response_consumer_idempotency_notification` |
| 8 | `BestAnswerChanged` | Content Service | Notification Service | `notifications.consumed_events(event_id, recipient_id)` | **Yes** (Notification item + consumed record) | **Yes** (Real PG container) | `tests/test_notification_service.py::test_event_processing_and_idempotency` |
| 9 | `VoteChanged` | Interaction Service | Notification Service | `notifications.consumed_events(event_id, recipient_id)` | **Yes** (Recorded as `processed` or `skipped_by_preference`) | **Yes** (Real PG container) | `tests/test_notification_service.py::test_event_processing_and_idempotency` |
| 10| `ThreadViewCountIncremented` | Interaction Service | Content Service | `content.consumed_events(event_id)` (Unique on `event_id`) | **Yes** (Thread view count increment + consumed record) | **Yes** (Real PG container) | `tests/test_outbox_hardening.py::test_lost_response_consumer_idempotency_content_view_count` |
| 11| `ThreadViewCountIncremented` | Interaction Service | Search Service | `search.consumed_events(event_id)` (Unique on `event_id`) | **Yes** (Search document view count update + consumed record) | **Yes** (Real PG container) | `tests/test_search_service.py` |
| 12| `ModerationActionApplied` | Moderation Service | Search Service | `search.consumed_events(event_id)` (Unique on `event_id`) | **Yes** (Search visibility update + consumed record) | **Yes** (Real PG container) | `tests/test_search_service.py` |
| 13| `ModerationActionApplied` | Moderation Service | Notification Service | `notifications.consumed_events(event_id, recipient_id)` | **Yes** (Notification item + consumed record) | **Yes** (Real PG container) | `tests/test_notification_service.py::test_event_processing_and_idempotency` |
| 14| `UserRegistered` | User Service | Notification Service | `notifications.consumed_events(event_id, recipient_id)` | **Yes** (Welcome notification + consumed record) | **Yes** (Real PG container) | `tests/test_notification_service.py` |
| 15| `UserRegistered` (Self) | User Service | User Service | In-memory / Email Sink (Mailpit) | N/A (Outbox event triggers async email dispatch) | **Yes** (Real Mailpit) | `tests/test_email_delivery.py` |

---

## 2. Fault Injection & Reliability Analysis

### 2.1 Concurrent Duplicate Delivery
- **Mechanism:** When multiple delivery workers or concurrent network retries deliver the exact same `event_id` simultaneously, the first transaction acquires the table row and commits both the domain mutation and the `consumed_events` entry.
- **Conflict Handling:** Subsequent concurrent deliveries trigger `UniqueConstraint` on `(event_id)` or `(event_id, recipient_id)`:
  - In `content-service`: Returns `200 OK` with `{"status": "processed", "duplicate": true, "increment": 0}`.
  - In `search-service`: Catches duplicate, rolls back transaction, and returns `200 OK` (`duplicate: true`).
  - In `notification-service`: Skips existing `(event_id, recipient_id)` pairs, creates 0 notifications, and returns `200 OK`.

### 2.2 Lost-Response Retry Verification
- **Empirical Proof:** In [`tests/test_outbox_hardening.py`](file:///home/chocolat/Documents/Project/ForumPlatform/tests/test_outbox_hardening.py):
  1. Producer sends event to consumer.
  2. Consumer executes side effect and inserts `consumed_events`.
  3. A custom `FaultInjectingTransport` simulates dropped connection / `httpx.ConnectTimeout` after consumer commits.
  4. Producer marks attempt as `FAILED` (attempts=1).
  5. On retry, consumer detects existing `consumed_events` record, returns `duplicate: true`, and producer advances status to `DELIVERED`.
  6. Database invariants confirm **zero duplicate records** or counter over-counting.

### 2.3 DLQ Transition & Requeue
- Outbox workers retry retryable network / 5xx / 408 / 429 errors up to `max_attempts` (default: 5) using randomized exponential backoff:
  $$\text{delay} = \min(60.0, 2.0 \times 2^{\text{attempt}-1} \times \text{jitter}_{[0.5, 1.5]})$$
- Permanent client errors (e.g. 400 Bad Request, schema corruption) or events exceeding max retries immediately transition to `DEAD_LETTER` with `last_error` capturing the failure reason.
- Administrative requeue updates status from `dead_letter` back to `pending`, resetting `attempts = 0`. Idempotent consumers guarantee that requeuing previously delivered events causes zero duplicate side effects.
