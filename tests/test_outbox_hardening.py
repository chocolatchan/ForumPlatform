"""Integration tests validating delivery ambiguity, consumer idempotency, and lease race conditions.

These tests require explicitly configured disposable PostgreSQL databases.  They never fall
back to the development Content, Notification, or Search databases.  Set
``TEST_CONTENT_DATABASE_URL``, ``TEST_NOTIFICATIONS_DATABASE_URL``, and
``TEST_SEARCH_DATABASE_URL`` to migrated disposable databases whose names start with
``test_`` or ``stress_`` before running them.

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

import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import delete, select

from src.common.database import DatabaseSessionManager
from src.common.internal_auth import INTERNAL_SERVICE_KEY_HEADER
from src.common.outbox import OutboxStatus
from src.common.outbox_worker import OutboxWorker
from src.services.content.app import app as content_app
from src.services.content.models import Category, ContentOutboxEvent, Thread
from src.services.content.models import ConsumedEvent as ContentConsumedEvent
from src.services.notification.app import app as notification_app
from src.services.notification.models import ConsumedEvent, EmailDelivery, NotificationItem
from src.services.search.app import app as search_app
from src.services.search.models import ConsumedEvent as SearchConsumedEvent
from src.services.search.models import SearchDocument
from tests.outbox_pg_isolation import (
    LIVE_DATABASE_NAMES,
    UnsafeTestDatabaseError,
    cleanup_test_rows,
    load_optional_test_database_url,
    validate_test_database_url,
)


def _load_test_db_url(env_var: str) -> str | None:
    try:
        return load_optional_test_database_url(env_var)
    except UnsafeTestDatabaseError as exc:
        raise pytest.UsageError(str(exc)) from None


CONTENT_TEST_DB_URL = _load_test_db_url("TEST_CONTENT_DATABASE_URL")
NOTIFICATION_TEST_DB_URL = _load_test_db_url("TEST_NOTIFICATIONS_DATABASE_URL")
SEARCH_TEST_DB_URL = _load_test_db_url("TEST_SEARCH_DATABASE_URL")
TEST_INTERNAL_KEY = os.environ["INTERNAL_SERVICE_SECRET"]
INTERNAL_AUTH_HEADERS = {INTERNAL_SERVICE_KEY_HEADER: TEST_INTERNAL_KEY}

CONTENT_PG_SKIP = pytest.mark.skipif(
    CONTENT_TEST_DB_URL is None,
    reason=("TEST_CONTENT_DATABASE_URL is not set; refusing to use a shared application database."),
)
NOTIFICATION_PG_SKIP = pytest.mark.skipif(
    NOTIFICATION_TEST_DB_URL is None,
    reason=(
        "TEST_NOTIFICATIONS_DATABASE_URL is not set; refusing to use a shared application database."
    ),
)
SEARCH_PG_SKIP = pytest.mark.skipif(
    SEARCH_TEST_DB_URL is None,
    reason=("TEST_SEARCH_DATABASE_URL is not set; refusing to use a shared application database."),
)


@pytest.fixture
async def content_db():
    database_url = CONTENT_TEST_DB_URL
    if database_url is None:
        pytest.skip("TEST_CONTENT_DATABASE_URL is not configured.")
    db = DatabaseSessionManager(
        database_url=database_url,
        pool_size=5,
        max_overflow=2,
    )
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
async def notification_test_db():
    database_url = NOTIFICATION_TEST_DB_URL
    if database_url is None:
        pytest.skip("TEST_NOTIFICATIONS_DATABASE_URL is not configured.")
    db = DatabaseSessionManager(
        database_url=database_url,
        pool_size=5,
        max_overflow=2,
    )
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
async def search_test_db():
    database_url = SEARCH_TEST_DB_URL
    if database_url is None:
        pytest.skip("TEST_SEARCH_DATABASE_URL is not configured.")
    db = DatabaseSessionManager(
        database_url=database_url,
        pool_size=5,
        max_overflow=2,
    )
    try:
        yield db
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Offline safety tests for the test-database isolation policy.
# ---------------------------------------------------------------------------


def test_outbox_test_database_policy_skips_missing_configuration():
    """Missing or blank configuration must not fall back to any shared database."""
    assert load_optional_test_database_url("TEST_CONTENT_DATABASE_URL", environ={}) is None
    assert (
        load_optional_test_database_url(
            "TEST_CONTENT_DATABASE_URL", environ={"TEST_CONTENT_DATABASE_URL": "   "}
        )
        is None
    )


@pytest.mark.parametrize(
    "raw_url",
    [
        "not-a-url",
        "sqlite+aiosqlite:///test_outbox.db",
        "postgresql+asyncpg://localhost:5432/",
        "postgresql+asyncpg://localhost:5432/forum_test_content",
        "postgresql+asyncpg://localhost:5432/test_",
    ],
)
def test_outbox_test_database_policy_rejects_malformed_or_unsafe_targets(raw_url):
    with pytest.raises(UnsafeTestDatabaseError):
        validate_test_database_url(raw_url, "TEST_CONTENT_DATABASE_URL")


@pytest.mark.parametrize("live_name", sorted(LIVE_DATABASE_NAMES))
def test_outbox_test_database_policy_rejects_live_application_databases(live_name):
    raw_url = f"postgresql+asyncpg://localhost:5432/{live_name}"
    with pytest.raises(UnsafeTestDatabaseError):
        validate_test_database_url(raw_url, "TEST_CONTENT_DATABASE_URL")


@pytest.mark.parametrize(
    ("raw_url", "expected"),
    [
        (
            "postgresql+asyncpg://test:test@localhost:5432/test_content_outbox",
            "postgresql+asyncpg://test:test@localhost:5432/test_content_outbox",
        ),
        (
            "postgresql://test:test@localhost:5432/stress_content_outbox",
            "postgresql+asyncpg://test:test@localhost:5432/stress_content_outbox",
        ),
    ],
)
def test_outbox_test_database_policy_accepts_disposable_targets(raw_url, expected):
    loaded = load_optional_test_database_url(
        "TEST_CONTENT_DATABASE_URL",
        environ={"TEST_CONTENT_DATABASE_URL": raw_url},
    )
    assert loaded == expected


class _RecordingSession:
    def __init__(self) -> None:
        self.statements: list[object] = []

    async def execute(self, statement: object) -> None:
        self.statements.append(statement)


class _RecordingTransaction:
    def __init__(self, session: _RecordingSession) -> None:
        self._session = session

    async def __aenter__(self) -> _RecordingSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _RecordingDatabase:
    def __init__(self) -> None:
        self.session = _RecordingSession()
        self.transaction_count = 0

    def transaction(self) -> _RecordingTransaction:
        self.transaction_count += 1
        return _RecordingTransaction(self.session)


@pytest.mark.asyncio
async def test_cleanup_helper_is_noop_without_explicit_statements():
    manager = _RecordingDatabase()
    await cleanup_test_rows(manager)
    assert manager.transaction_count == 0
    assert manager.session.statements == []


@pytest.mark.asyncio
async def test_cleanup_helper_executes_only_supplied_statements():
    manager = _RecordingDatabase()
    event_id = uuid.uuid4()
    statement = delete(ContentOutboxEvent).where(ContentOutboxEvent.id == event_id)

    await cleanup_test_rows(manager, statement)

    assert manager.transaction_count == 1
    assert manager.session.statements == [statement]


# ---------------------------------------------------------------------------
# PostgreSQL integration tests.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@NOTIFICATION_PG_SKIP
async def test_lost_response_consumer_idempotency_notification(notification_test_db, monkeypatch):
    """Phase B: Notification consumer commits event; lost response leads to retry without duplicate."""
    import src.services.notification.app as notification_app_module

    monkeypatch.setattr(notification_app_module, "notification_db", notification_test_db)

    event_id = uuid.uuid4()
    recipient_id = uuid.uuid4()
    author_id = uuid.uuid4()
    comment_id = uuid.uuid4()

    payload = {
        "event_id": str(event_id),
        "event_type": "CommentCreated",
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": "content-service",
        "data": {
            "comment_id": str(comment_id),
            "thread_id": str(uuid.uuid4()),
            "thread_title": "Idempotency Test Thread",
            "thread_author_id": str(recipient_id),
            "comment_author_id": str(author_id),
            "parent_comment_author_id": None,
            "comment_snippet": "This is a test comment.",
        },
    }

    try:
        # Use httpx.AsyncClient with in-process ASGI app to simulate exact internal endpoint
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=notification_app), base_url="http://test"
        ) as client:
            headers = INTERNAL_AUTH_HEADERS

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
        async with notification_test_db.session() as session:
            consumed_stmt = select(ConsumedEvent).where(ConsumedEvent.event_id == event_id)
            consumed = (await session.execute(consumed_stmt)).scalars().all()
            assert len(consumed) == 1

            notif_stmt = select(NotificationItem).where(
                NotificationItem.recipient_id == recipient_id
            )
            notifs = (await session.execute(notif_stmt)).scalars().all()
            assert len(notifs) == 1
            assert notifs[0].title == "New comment on your thread"
    finally:
        await cleanup_test_rows(
            notification_test_db,
            delete(EmailDelivery).where(EmailDelivery.event_id == event_id),
            delete(ConsumedEvent).where(ConsumedEvent.event_id == event_id),
            delete(NotificationItem).where(
                NotificationItem.recipient_id == recipient_id,
                NotificationItem.target_id == comment_id,
            ),
        )


@pytest.mark.asyncio
@SEARCH_PG_SKIP
async def test_lost_response_consumer_idempotency_search(search_test_db, monkeypatch):
    """Phase B: Search consumer commits event; lost response leads to retry without duplicate doc or version corruption."""
    import src.services.search.app as search_app_module

    monkeypatch.setattr(search_app_module, "search_db", search_test_db)

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

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=search_app), base_url="http://test"
        ) as client:
            headers = INTERNAL_AUTH_HEADERS

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

        # Verify search DB: exactly 1 SearchDocument exists for thread_id and exactly 1 SearchConsumedEvent exists
        async with search_test_db.session() as session:
            consumed_stmt = select(SearchConsumedEvent).where(
                SearchConsumedEvent.event_id == event_id
            )
            consumed = (await session.execute(consumed_stmt)).scalars().all()
            assert len(consumed) == 1

            doc_stmt = select(SearchDocument).where(SearchDocument.source_id == thread_id)
            docs = (await session.execute(doc_stmt)).scalars().all()
            assert len(docs) == 1
            assert docs[0].title == "Idempotency Thread Title"
    finally:
        await cleanup_test_rows(
            search_test_db,
            delete(SearchConsumedEvent).where(SearchConsumedEvent.event_id == event_id),
            delete(SearchDocument).where(
                SearchDocument.source_type == "thread",
                SearchDocument.source_id == thread_id,
            ),
        )


@pytest.mark.asyncio
@CONTENT_PG_SKIP
async def test_stale_lease_overwrite_guard(content_db):
    """Phase C: Worker A's lease expires; Worker B reclaims and completes event. Worker A cannot overwrite."""
    event_id = uuid.uuid4()
    old_time = datetime.now(UTC) - timedelta(seconds=120)
    workers: list[OutboxWorker] = []

    try:
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
            internal_key=TEST_INTERNAL_KEY,
            batch_size=1,
            lock_timeout_seconds=30.0,
        )
        workers.append(worker_b)
        mock_resp = httpx.Response(
            status_code=200,
            request=httpx.Request("POST", "http://search-service:8006/internal/events"),
        )
        worker_b_client = AsyncMock(spec=httpx.AsyncClient)
        worker_b_client.post.return_value = mock_resp
        worker_b._http_client = worker_b_client

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
            internal_key=TEST_INTERNAL_KEY,
        )
        workers.append(worker_a)
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
    finally:
        try:
            for worker in workers:
                await worker.stop()
        finally:
            await cleanup_test_rows(
                content_db,
                delete(ContentOutboxEvent).where(ContentOutboxEvent.id == event_id),
            )


@pytest.mark.asyncio
@CONTENT_PG_SKIP
async def test_permanent_4xx_transitions_immediately_to_dead_letter(content_db):
    """Phase D: Permanent client error (e.g. HTTP 400/422) transitions directly to DEAD_LETTER without retries."""
    event_id = uuid.uuid4()
    now = datetime.now(UTC)
    workers: list[OutboxWorker] = []

    try:
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
            internal_key=TEST_INTERNAL_KEY,
            batch_size=1,
            max_attempts=5,
        )
        workers.append(worker)
        # Simulate consumer responding with 422 Unprocessable Content
        mock_resp = httpx.Response(
            status_code=422,
            text='{"error": {"code": "VALIDATION_ERROR", "message": "Field required"}}',
            request=httpx.Request("POST", "http://search-service:8006/internal/events"),
        )
        worker_client = AsyncMock(spec=httpx.AsyncClient)
        worker_client.post.return_value = mock_resp
        worker._http_client = worker_client

        claimed = await worker.process_batch(event_id=event_id)
        assert claimed == 1

        # Verify event moved immediately to DEAD_LETTER on attempt 1
        async with content_db.session() as session:
            stored = await session.get(ContentOutboxEvent, event_id)
            assert stored.status == OutboxStatus.DEAD_LETTER
            assert stored.attempts == 1
            assert stored.next_retry_at is None
            assert "HTTP 422" in stored.last_error
    finally:
        try:
            for worker in workers:
                await worker.stop()
        finally:
            await cleanup_test_rows(
                content_db,
                delete(ContentOutboxEvent).where(ContentOutboxEvent.id == event_id),
            )


@pytest.mark.asyncio
@CONTENT_PG_SKIP
async def test_lost_response_consumer_idempotency_content_view_count(content_db, monkeypatch):
    """Section 3 & DEC-005: Content Service ThreadViewCountIncremented idempotency via consumed_events.

    Simulates:
    1. Interaction Service produces ThreadViewCountIncremented event.
    2. Content Service ingests event: increments view_count from 0 to 1 and records ConsumedEvent.
    3. Simulated lost response on network causes duplicate delivery.
    4. Second ingestion returns duplicate: True, and view_count remains 1 (NOT 2).
    """
    import src.services.content.app as content_app_module

    monkeypatch.setattr(content_app_module, "content_db", content_db)

    category_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    author_id = uuid.uuid4()
    event_id = uuid.uuid4()

    try:
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
            headers = INTERNAL_AUTH_HEADERS

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
    finally:
        await cleanup_test_rows(
            content_db,
            delete(ContentConsumedEvent).where(ContentConsumedEvent.event_id == event_id),
            delete(Thread).where(Thread.id == thread_id),
            delete(Category).where(Category.id == category_id),
        )


@pytest.mark.asyncio
@CONTENT_PG_SKIP
@SEARCH_PG_SKIP
async def test_lost_response_controlled_producer_dispatch(content_db, search_test_db, monkeypatch):
    """Section 2: Controlled producer-to-consumer dispatch with lost HTTP response injection.

    Simulates:
    1. Producer (OutboxWorker) claims event and dispatches via custom in-process ASGI transport.
    2. Consumer (Search Service app) processes event and commits DB state.
    3. Transport raises httpx.ConnectTimeout to simulate lost response before returning to producer.
    4. OutboxWorker records FAILED (not DEAD_LETTER) and schedules retry.
    5. On retry, consumer detects idempotency record and returns 200 OK.
    6. Producer successfully updates status to DELIVERED without duplicating state.
    """
    import src.services.content.app as content_app_module
    import src.services.search.app as search_app_module

    monkeypatch.setattr(content_app_module, "content_db", content_db)
    monkeypatch.setattr(search_app_module, "search_db", search_test_db)

    event_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    author_id = uuid.uuid4()
    category_id = uuid.uuid4()
    now = datetime.now(UTC)
    workers: list[OutboxWorker] = []

    try:
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

        # 2. Setup custom transport wrapping the in-process Search ASGI app to inject a
        # lost response on the first call.  No live service or application database is used.
        call_count = 0
        search_transport = httpx.ASGITransport(app=search_app)

        class FaultInjectingTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                nonlocal call_count
                call_count += 1
                response = await search_transport.handle_async_request(request)
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
            service_urls={"search-service": "http://test"},
            internal_key=TEST_INTERNAL_KEY,
            batch_size=1,
            max_attempts=3,
            base_backoff_seconds=0.01,
        )
        workers.append(worker)
        worker._http_client = httpx.AsyncClient(
            transport=FaultInjectingTransport(),
            base_url="http://test",
            headers=INTERNAL_AUTH_HEADERS,
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
            consumed_stmt = select(SearchConsumedEvent).where(
                SearchConsumedEvent.event_id == event_id
            )
            consumed = (await session.execute(consumed_stmt)).scalars().all()
            assert len(consumed) == 1

            doc_stmt = select(SearchDocument).where(SearchDocument.source_id == thread_id)
            docs = (await session.execute(doc_stmt)).scalars().all()
            assert len(docs) == 1
            assert docs[0].title == "Lost Response Dispatch Thread"
    finally:
        try:
            for worker in workers:
                await worker.stop()
        finally:
            try:
                await cleanup_test_rows(
                    content_db,
                    delete(ContentOutboxEvent).where(ContentOutboxEvent.id == event_id),
                )
            finally:
                await cleanup_test_rows(
                    search_test_db,
                    delete(SearchConsumedEvent).where(SearchConsumedEvent.event_id == event_id),
                    delete(SearchDocument).where(
                        SearchDocument.source_type == "thread",
                        SearchDocument.source_id == thread_id,
                    ),
                )
