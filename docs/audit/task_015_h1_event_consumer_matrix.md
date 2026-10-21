# Task 015-H1 Event-Consumer Routing Matrix & Idempotency Audit

**Date of Execution:** 2026-10-21  
**Target Architecture:** 6 FastAPI microservices (`user`, `content`, `interaction`, `moderation`, `notification`, `search`) + API Gateway + PostgreSQL 16 + Redis 7  
**Contract Definition:** Asynchronous Transactional Outbox Pattern with at-least-once HTTP delivery over `/internal/events`

---

## 1. Complete Event-Consumer Matrix

| # | Event Type | Producer Service | Target Consumer(s) | Delivery Endpoint | Idempotency Key / Mechanism | Primary Business Side Effect | Duplicate Replay Safety | Verified Evidence |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| 1 | `UserRegistered` | `user-service` | `notification-service` | `/internal/events` | `UniqueConstraint("event_id", "recipient_id")` on `notifications.consumed_events` | Creates welcome `NotificationItem` for newly verified user account. | **SAFE**: Returns 200 OK (`notifications_created: 0`), existing notification unmodified. | `tests/test_outbox_hardening.py`, `tests/test_notification_service.py` |
| 2 | `CommentCreated` | `content-service` | `notification-service` | `/internal/events` | `UniqueConstraint("event_id", "recipient_id")` on `notifications.consumed_events` | Alerts thread author and/or parent comment author. | **SAFE**: Returns 200 OK (`notifications_created: 0`). Atomic transaction creates notification + idempotency row. | `tests/test_outbox_hardening.py` (`test_lost_response_consumer_idempotency_notification`) |
| 3 | `BestAnswerChanged` | `content-service` | `notification-service` | `/internal/events` | `UniqueConstraint("event_id", "recipient_id")` on `notifications.consumed_events` | Alerts author of comment selected as best answer. | **SAFE**: Duplicate event detected, 0 new notifications created. | `tests/test_notification_service.py` |
| 4 | `VoteChanged` | `interaction-service` | `notification-service` | `/internal/events` | `UniqueConstraint("event_id", "recipient_id")` on `notifications.consumed_events` | Alerts content author on positive score milestones (e.g. 5, 10, 25). | **SAFE**: Replay produces no redundant notification records. | `tests/test_notification_service.py` |
| 5 | `ThreadViewCountIncremented` | `interaction-service` | `content-service`, `search-service` | `/internal/events` | **Producer Deduplication:** Interaction Service 30-min window (`interactions.thread_views`).<br>**Content Consumer Idempotency:** `UniqueConstraint("event_id")` on `content.consumed_events`.<br>**Search Consumer Idempotency:** `UniqueConstraint("event_id")` on `search.consumed_events`. | **Content:** Atomically increments `content.threads.view_count` by 1 and inserts into `content.consumed_events` in the same transaction.<br>**Search:** Updates `view_count` on `SearchDocument` and inserts into `search.consumed_events`. | **SAFE**: Producer deduplicates initial views within 30m window per session. If network drops response, duplicate delivery returns 200 OK (`duplicate: True`) and `view_count` is NOT double-incremented. | `tests/test_outbox_hardening.py` (`test_lost_response_consumer_idempotency_content_view_count`), Gateway DEC-005 verification |
| 6 | `ThreadCreated` | `content-service` | `search-service`, `notification-service` | `/internal/events` | `UniqueConstraint("event_id")` on `search.consumed_events` | Indexes thread title, body, tags into `search.documents`. | **SAFE**: Returns 200 OK (`document_updated: false`). Document remains intact. | `tests/test_outbox_hardening.py` (`test_lost_response_consumer_idempotency_search`) |
| 7 | `ThreadUpdated` | `content-service` | `search-service` | `/internal/events` | `UniqueConstraint("event_id")` on `search.consumed_events` | Updates title, body, and tags of `SearchDocument`. | **SAFE**: Duplicate event skipped atomically. | `tests/test_search_service.py` |
| 8 | `ThreadDeleted` | `content-service` | `search-service` | `/internal/events` | `UniqueConstraint("event_id")` on `search.consumed_events` | Sets `is_deleted = True` or removes document projection. | **SAFE**: Idempotent deletion. | `tests/test_search_service.py` |
| 9 | `ThreadLockToggled` | `content-service` | `search-service` | `/internal/events` | `UniqueConstraint("event_id")` on `search.consumed_events` | Synchronizes `is_locked` boolean on search document. | **SAFE**: Duplicate event skipped. | `tests/test_search_service.py` |
| 10 | `ThreadPinToggled` | `content-service` | `search-service` | `/internal/events` | `UniqueConstraint("event_id")` on `search.consumed_events` | Synchronizes `is_pinned` boolean on search document. | **SAFE**: Duplicate event skipped. | `tests/test_search_service.py` |
| 11 | `ModerationActionApplied` | `moderation-service` | `search-service`, `notification-service` | `/internal/events` | **Search:** `search.consumed_events`<br>**Notification:** `notifications.consumed_events` | **Search:** Hides soft-deleted content from search queries.<br>**Notification:** Sends moderation action notice to violator. | **SAFE**: Replay creates 0 new notifications and preserves search visibility state. | `tests/test_search_service.py`, `tests/test_moderation_service.py` |

---

## 2. Shared Outbox Multi-Consumer Analysis

### Topology
Certain events emit to more than one consumer:
- `ThreadCreated` -> `search-service` AND `notification-service`
- `ThreadViewCountIncremented` -> `content-service` AND `search-service`
- `ModerationActionApplied` -> `search-service` AND `notification-service`

### Behavior Under Partial Downstream Failure
1. If Consumer 1 succeeds (200 OK) but Consumer 2 fails (e.g. 503 Service Unavailable), the producer marks the event `failed` and schedules a retry for the next eligible interval.
2. Upon retry, the worker dispatches to both consumers.
3. Consumer 1 uses its `consumed_events` table to recognize that `event_id` was already processed and immediately returns 200 OK with `status: processed` and zero duplicate side-effects.
4. Consumer 2 processes the event fresh.
5. If both succeed, the producer marks the outbox event `delivered`.

**Conclusion:** The shared outbox model guarantees at-least-once delivery with zero duplicate side effects across all multi-consumer routes. Introducing per-consumer child tables is not required for correctness.
