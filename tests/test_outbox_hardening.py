"""Integration tests validating delivery ambiguity, consumer idempotency, and lease race conditions.

Tests:
1. Producer dispatches event, consumer commits idempotency record and side effect,
   but HTTP response is lost/timed out.
2. Producer retries identical event: consumer detects idempotency record and returns 200 OK
   without duplicating business side effect.
3. Stale lease race condition: Worker A claims event, takes longer than lease_timeout to dispatch;
   Worker B reclaims stale event and delivers it. Worker A's late delivery cannot overwrite Worker B.
4. Permanent 4xx error (e.g. 400 Bad Request, 422 Unprocessable) immediately moves event to DEAD_LETTER
   without wasteful endless retries.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from src.common.database import DatabaseSessionManager
from src.common.outbox import OutboxStatus
from src.common.outbox_worker import OutboxWorker
from src.services.content.app import app as content_app
from src.services.content.models import Category, ContentOutboxEvent, Thread
from src.services.content.models import ConsumedEvent as ContentConsumedEvent
from src.services.notification.app import app as notification_app
from src.services.notification.database import notification_db
from src.services.notification.models import ConsumedEvent, NotificationItem
from src.services.search.app import app as search_app
from src.services.search.models import ConsumedEvent as SearchConsumedEvent
from src.services.search.models import SearchDocument

POSTGRES_CONTENT_URL = (
    "postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_content"
)
POSTGRES_INTERACTION_URL = (
    "postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_interactions"
)
POSTGRES_SEARCH_URL = (
    "postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_search"
)


@pytest.fixture
async def content_db():
    db = DatabaseSessionManager(
        database_url=POSTGRES_CONTENT_URL,
        pool_size=5,
        max_overflow=2,
    )
    yield db
    await db.close()


@pytest.fixture
async def search_test_db():
    db = DatabaseSessionManager(
        database_url=POSTGRES_SEARCH_URL,
        pool_size=5,
        max_overflow=2,
    )
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_lost_response_consumer_idempotency_notification():
    """Phase B: Notification consumer commits event; lost response leads to retry without duplicate."""
    event_id = uuid.uuid4()
    recipient_id = uuid.uuid4()
    author_id = uuid.uuid4()

    payload = {
        "event_id": str(event_id),
        "event_type": "CommentCreated",
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": "content-service",
        "data": {
            "comment_id": str(uuid.uuid4()),
            "thread_id": str(uuid.uuid4()),
            "thread_title": "Idempotency Test Thread",
            "thread_author_id": str(recipient_id),
            "comment_author_id": str(author_id),
            "parent_comment_author_id": None,
            "comment_snippet": "This is a test comment.",
        },
    }

    # Use httpx.AsyncClient with in-process ASGI app to simulate exact internal endpoint
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=notification_app), base_url="http://test"
    ) as client:
        headers = {"X-Internal-Service-Key": "default-internal-service-secret-key-32chars"}

        # Attempt 1: Consumer processes event and creates notification
        resp1 = await client.post("/internal/events", json=payload, headers=headers)
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "processed"
        assert resp1.json()["notifications_created"] == 1

        # Simulate lost response on producer side: Producer retries identical event_id
        # Attempt 2: Consumer must recognize (event_id, recipient_id) and return safe processed status with 0 new items
        resp2 = await client.post("/internal/events", json=payload, headers=headers)
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "processed"
        assert resp2.json()["notifications_created"] == 0

    # Verify notification records in DB: exactly 1 NotificationItem and 1 ConsumedEvent exist
    async with notification_db.session() as session:
        consumed_stmt = select(ConsumedEvent).where(ConsumedEvent.event_id == event_id)
        consumed = (await session.execute(consumed_stmt)).scalars().all()
        assert len(consumed) == 1

        notif_stmt = select(NotificationItem).where(NotificationItem.recipient_id == recipient_id)
        notifs = (await session.execute(notif_stmt)).scalars().all()
        assert len(notifs) == 1
        assert notifs[0].title == "New comment on your thread"


@pytest.mark.asyncio
async def test_lost_response_consumer_idempotency_search():
    """Phase B: Search consumer commits event; lost response leads to retry without duplicate doc or version corruption."""
    event_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    author_id = uuid.uuid4()
    category_id = uuid.uuid4()

    payload = {
        "event_id": str(event_id),
        "event_type": "ThreadCreated",
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": "content-service",
        "data": {
            "thread_id": str(thread_id),
            "author_id": str(author_id),
            "category_id": str(category_id),
            "category_name": "General",
            "title": "Idempotency Thread Title",
            "body": "Body content here",
            "tags": ["python"],
        },
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=search_app), base_url="http://test"
    ) as client:
        headers = {"X-Internal-Service-Key": "default-internal-service-secret-key-32chars"}

        # Attempt 1: Search indexes document
        resp1 = await client.post("/internal/events", json=payload, headers=headers)
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "processed"
        assert resp1.json()["document_updated"] is True

        # Simulate lost response -> Attempt 2 retry
        resp2 = await client.post("/internal/events", json=payload, headers=headers)
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "processed"
        assert resp2.json()["document_updated"] is False

    import src.services.search.app as search_app_mod

    # Verify search DB: exactly 1 SearchDocument exists for thread_id and exactly 1 SearchConsumedEvent exists
    async with search_app_mod.search_db.session() as session:
        consumed_stmt = select(SearchConsumedEvent).where(SearchConsumedEvent.event_id == event_id)
        consumed = (await session.execute(consumed_stmt)).scalars().all()
        assert len(consumed) == 1

        doc_stmt = select(SearchDocument).where(SearchDocument.source_id == thread_id)
        docs = (await session.execute(doc_stmt)).scalars().all()
        assert len(docs) == 1
        assert docs[0].title == "Idempotency Thread Title"


@pytest.mark.asyncio
async def test_stale_lease_overwrite_guard(content_db):
    """Phase C: Worker A's lease expires; Worker B reclaims and completes event. Worker A cannot overwrite."""
    event_id = uuid.uuid4()
    old_time = datetime.now(UTC) - timedelta(seconds=120)

    # Insert event
    async with content_db.transaction() as session:
        evt = ContentOutboxEvent(
            id=event_id,
            event_type="ThreadCreated",
            aggregate_type="Thread",
            aggregate_id=uuid.uuid4(),
            payload={"thread_id": str(uuid.uuid4()), "title": "Stale Lease Guard Test"},
            status=OutboxStatus.PROCESSING,
            locked_at=old_time,
            locked_by="worker-a-stale",
            attempts=1,
            occurred_at=old_time,
        )
        session.add(evt)

    worker_b = OutboxWorker(
        service_name="content-service",
        db_manager=content_db,
        model_class=ContentOutboxEvent,
        service_urls={"search-service": "http://search-service:8006"},
        batch_size=1,
        lock_timeout_seconds=30.0,
    )
    mock_resp = httpx.Response(
        status_code=200, request=httpx.Request("POST", "http://search-service:8006/internal/events")
    )
    worker_b._http_client = AsyncMock(spec=httpx.AsyncClient)
    worker_b._http_client.post.return_value = mock_resp

    # Worker B claims and delivers the stale event
    claimed = await worker_b.process_batch()
    assert claimed == 1

    # Verify event is delivered by worker B
    async with content_db.session() as session:
        stored = await session.get(ContentOutboxEvent, event_id)
        assert stored.status == OutboxStatus.DELIVERED
        assert stored.attempts == 2

    # Now simulate Worker A finally finishing its slow HTTP request and trying to update status to FAILED
    # Because Worker A's worker_id != Worker B's worker_id (and locked_by is now None / owned by B),
    # the lease guard should reject Worker A's update
    worker_a = OutboxWorker(
        service_name="content-service",
        db_manager=content_db,
        model_class=ContentOutboxEvent,
        service_urls={},
    )
    worker_a.worker_id = "worker-a-stale"

    updated = await worker_a._update_event_status(
        event_id=event_id,
        new_status=OutboxStatus.FAILED,
        last_error="Worker A delayed failure",
        attempts=2,
        expected_locked_by="worker-a-stale",
    )
    # The update MUST be rejected because Worker A's lease is stale and the event is already DELIVERED
    assert updated is False, (
        "Obsolete worker must not be permitted to update status of reclaimed event"
    )

    # Most importantly: the DB record MUST remain DELIVERED
    async with content_db.session() as session:
        final_event = await session.get(ContentOutboxEvent, event_id)
        # Note: if locked_by was already cleared to None on delivery, Worker A shouldn't overwrite delivered status
        assert final_event.status == OutboxStatus.DELIVERED


@pytest.mark.asyncio
async def test_permanent_4xx_transitions_immediately_to_dead_letter(content_db):
    """Phase D: Permanent client error (e.g. HTTP 400/422) transitions directly to DEAD_LETTER without retries."""
    event_id = uuid.uuid4()
    now = datetime.now(UTC)

    async with content_db.transaction() as session:
        evt = ContentOutboxEvent(
            id=event_id,
            event_type="ThreadCreated",
            aggregate_type="Thread",
            aggregate_id=uuid.uuid4(),
            payload={"invalid_payload": "missing thread_id"},
            status=OutboxStatus.PENDING,
            attempts=0,
            occurred_at=now,
        )
        session.add(evt)

    worker = OutboxWorker(
        service_name="content-service",
        db_manager=content_db,
        model_class=ContentOutboxEvent,
        service_urls={"search-service": "http://search-service:8006"},
        batch_size=1,
        max_attempts=5,
    )
    # Simulate consumer responding with 422 Unprocessable Content
    mock_resp = httpx.Response(
        status_code=422,
        text='{"error": {"code": "VALIDATION_ERROR", "message": "Field required"}}',
        request=httpx.Request("POST", "http://search-service:8006/internal/events"),
    )
    worker._http_client = AsyncMock(spec=httpx.AsyncClient)
    worker._http_client.post.return_value = mock_resp

    claimed = await worker.process_batch(event_id=event_id)
    assert claimed == 1

    # Verify event moved immediately to DEAD_LETTER on attempt 1
    async with content_db.session() as session:
        stored = await session.get(ContentOutboxEvent, event_id)
        assert stored.status == OutboxStatus.DEAD_LETTER
        assert stored.attempts == 1
        assert stored.next_retry_at is None
        assert "HTTP 422" in stored.last_error


@pytest.mark.asyncio
async def test_lost_response_consumer_idempotency_content_view_count(content_db):
    """Section 3 & DEC-005: Content Service ThreadViewCountIncremented idempotency via consumed_events.

    Simulates:
    1. Interaction Service produces ThreadViewCountIncremented event.
    2. Content Service ingests event: increments view_count from 0 to 1 and records ConsumedEvent.
    3. Simulated lost response on network causes duplicate delivery.
    4. Second ingestion returns duplicate: True, and view_count remains 1 (NOT 2).
    """
    category_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    author_id = uuid.uuid4()
    event_id = uuid.uuid4()

    # Seed category and thread in PostgreSQL content database
    async with content_db.transaction() as session:
        cat = Category(
            id=category_id,
            name="Idempotency Cat",
            slug=f"idem-cat-{uuid.uuid4().hex[:8]}",
            description="Category for testing",
            created_by=author_id,
        )
        session.add(cat)
        th = Thread(
            id=thread_id,
            category_id=category_id,
            author_id=author_id,
            title="Idempotent View Count Thread",
            body="Valid thread body with more than 20 characters length.",
            view_count=0,
        )
        session.add(th)

    payload = {
        "event_id": str(event_id),
        "event_type": "ThreadViewCountIncremented",
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": "interaction-service",
        "data": {
            "thread_id": str(thread_id),
            "increment": 1,
        },
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=content_app), base_url="http://test"
    ) as client:
        headers = {"X-Internal-Service-Key": "default-internal-service-secret-key-32chars"}

        # First delivery attempt: Successfully increments view count
        resp1 = await client.post("/internal/events", json=payload, headers=headers)
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "processed"
        assert resp1.json()["increment"] == 1
        assert resp1.json().get("duplicate") is not True

        # Second delivery attempt (simulating retry after dropped response): Must be idempotent
        resp2 = await client.post("/internal/events", json=payload, headers=headers)
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "processed"
        assert resp2.json()["increment"] == 0
        assert resp2.json().get("duplicate") is True

    # Verify directly in DB: thread.view_count == 1 and exactly 1 ConsumedEvent record
    async with content_db.session() as session:
        refreshed_thread = await session.get(Thread, thread_id)
        assert refreshed_thread is not None
        assert refreshed_thread.view_count == 1

        consumed_stmt = select(ContentConsumedEvent).where(
            ContentConsumedEvent.event_id == event_id
        )
        consumed_records = (await session.execute(consumed_stmt)).scalars().all()
        assert len(consumed_records) == 1
        assert consumed_records[0].event_type == "ThreadViewCountIncremented"


@pytest.mark.asyncio
async def test_lost_response_controlled_producer_dispatch(content_db, search_test_db):
    """Section 2: Controlled producer-to-consumer dispatch with lost HTTP response injection.

    Simulates:
    1. Producer (OutboxWorker) claims event and dispatches via custom transport.
    2. Consumer (Search Service container) processes event and commits DB state.
    3. Transport raises httpx.ConnectTimeout to simulate lost response before returning to producer.
    4. OutboxWorker records FAILED (not DEAD_LETTER) and schedules retry.
    5. On retry, consumer detects idempotency record and returns 200 OK.
    6. Producer successfully updates status to DELIVERED without duplicating state.
    """
    event_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    author_id = uuid.uuid4()
    category_id = uuid.uuid4()
    now = datetime.now(UTC)

    # 1. Insert pending ThreadCreated event into content outbox
    async with content_db.transaction() as session:
        evt = ContentOutboxEvent(
            id=event_id,
            event_type="ThreadCreated",
            aggregate_type="Thread",
            aggregate_id=thread_id,
            payload={
                "thread_id": str(thread_id),
                "author_id": str(author_id),
                "category_id": str(category_id),
                "category_name": "General",
                "title": "Lost Response Dispatch Thread",
                "body": "Valid body content with sufficient length.",
                "tags": ["outbox", "reliability"],
            },
            status=OutboxStatus.PENDING,
            attempts=0,
            occurred_at=now,
        )
        session.add(evt)

    # 2. Setup custom transport wrapping real HTTP client to inject lost response on first call
    call_count = 0
    real_transport = httpx.AsyncHTTPTransport()

    class FaultInjectingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Execute request against running search container
            response = await real_transport.handle_async_request(request)
            if call_count == 1:
                # Consumer committed the event, but response is lost/timed out to producer
                raise httpx.ConnectTimeout(
                    "Simulated lost response / connection dropped after commit"
                )
            return response

    worker = OutboxWorker(
        service_name="content-service",
        db_manager=content_db,
        model_class=ContentOutboxEvent,
        service_urls={"search-service": "http://localhost:8006"},
        batch_size=1,
        max_attempts=3,
        base_backoff_seconds=0.01,
    )
    worker._http_client = httpx.AsyncClient(
        transport=FaultInjectingTransport(),
        base_url="http://localhost:8006",
        headers={"X-Internal-Service-Key": "default-internal-service-secret-key-32chars"},
    )

    # First dispatch attempt: Consumer commits, but producer gets ConnectTimeout
    claimed1 = await worker.process_batch(event_id=event_id)
    assert claimed1 == 1

    # Verify producer recorded status=FAILED with attempts=1
    async with content_db.session() as session:
        evt1 = await session.get(ContentOutboxEvent, event_id)
        assert evt1 is not None
        assert evt1.status == OutboxStatus.FAILED
        assert evt1.attempts == 1
        assert "Simulated lost response" in (evt1.last_error or "")
        # Reset next_retry_at so second batch immediately picks it up
        evt1.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    # Second dispatch attempt: Producer retries, consumer returns duplicate: True / updated: False
    claimed2 = await worker.process_batch(event_id=event_id)
    assert claimed2 == 1

    # Verify producer recorded status=DELIVERED with attempts=2
    async with content_db.session() as session:
        evt2 = await session.get(ContentOutboxEvent, event_id)
        assert evt2 is not None
        assert evt2.status == OutboxStatus.DELIVERED
        assert evt2.attempts == 2

    # Verify search DB has exactly 1 SearchDocument and 1 SearchConsumedEvent
    async with search_test_db.session() as session:
        consumed_stmt = select(SearchConsumedEvent).where(SearchConsumedEvent.event_id == event_id)
        consumed = (await session.execute(consumed_stmt)).scalars().all()
        assert len(consumed) == 1

        doc_stmt = select(SearchDocument).where(SearchDocument.source_id == thread_id)
        docs = (await session.execute(doc_stmt)).scalars().all()
        assert len(docs) == 1
        assert docs[0].title == "Lost Response Dispatch Thread"

    await worker._http_client.aclose()
