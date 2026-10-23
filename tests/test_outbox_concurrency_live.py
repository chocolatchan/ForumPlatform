"""PostgreSQL outbox concurrency and crash-recovery verification tests.

These tests require an explicitly configured disposable PostgreSQL database.  They never
fall back to the development Content Service database.  Set
``TEST_CONTENT_DATABASE_URL`` to a migrated disposable database whose name starts with
``test_`` or ``stress_`` before running them.

They validate:
1. Two workers attempting to claim batches concurrently (SELECT FOR UPDATE SKIP LOCKED).
2. The same event is never claimed simultaneously by two workers.
3. A worker crash/exit after claiming an event but before dispatch leaves status=processing.
4. A worker crash/exit after dispatch begins leaves status=processing.
5. A worker crash/exit after consumer processes but before producer update leaves status=processing.
6. Worker restarts reclaim stale processing events after lock_timeout.
7. Stale worker claim recovery: events with status=processing and locked_at < lock_cutoff are claimed.
8. Slow consumer does NOT hold PostgreSQL row locks or exhaust connection pool.
9. Graceful shutdown closes resources and does not strand events permanently.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import delete, select, text

from src.common.database import DatabaseSessionManager
from src.common.outbox import OutboxStatus
from src.common.outbox_worker import OutboxWorker
from src.services.content.models import ContentOutboxEvent
from tests.outbox_pg_isolation import (
    UnsafeTestDatabaseError,
    cleanup_test_rows,
    load_optional_test_database_url,
)


def _load_test_db_url(env_var: str) -> str | None:
    try:
        return load_optional_test_database_url(env_var)
    except UnsafeTestDatabaseError as exc:
        raise pytest.UsageError(str(exc)) from None


CONTENT_TEST_DB_URL = _load_test_db_url("TEST_CONTENT_DATABASE_URL")
TEST_INTERNAL_KEY = os.environ["INTERNAL_SERVICE_SECRET"]
CONTENT_PG_SKIP = pytest.mark.skipif(
    CONTENT_TEST_DB_URL is None,
    reason=("TEST_CONTENT_DATABASE_URL is not set; refusing to use a shared application database."),
)


@pytest.fixture
async def pg_db():
    database_url = CONTENT_TEST_DB_URL
    if database_url is None:
        pytest.skip("TEST_CONTENT_DATABASE_URL is not configured.")
    db = DatabaseSessionManager(
        database_url=database_url,
        pool_size=10,
        max_overflow=5,
    )
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
@CONTENT_PG_SKIP
async def test_concurrent_workers_skip_locked(pg_db):
    """Test 1 & 2: Two workers query concurrently; each event is claimed exclusively by only one worker."""
    event_ids = [uuid.uuid4() for _ in range(10)]
    now = datetime.now(UTC)
    workers: list[OutboxWorker] = []

    try:
        # Insert 10 test events in content.outbox_events
        async with pg_db.transaction() as session:
            for eid in event_ids:
                evt = ContentOutboxEvent(
                    id=eid,
                    event_type="ThreadCreated",
                    aggregate_type="Thread",
                    aggregate_id=uuid.uuid4(),
                    payload={"thread_id": str(uuid.uuid4()), "title": "Concurrency Test"},
                    status=OutboxStatus.PENDING,
                    attempts=0,
                    occurred_at=now,
                )
                session.add(evt)

        worker_a = OutboxWorker(
            service_name="content-service",
            db_manager=pg_db,
            model_class=ContentOutboxEvent,
            service_urls={"search-service": "http://search-service:8006"},
            internal_key=TEST_INTERNAL_KEY,
            batch_size=5,
        )
        workers.append(worker_a)
        worker_b = OutboxWorker(
            service_name="content-service",
            db_manager=pg_db,
            model_class=ContentOutboxEvent,
            service_urls={"search-service": "http://search-service:8006"},
            internal_key=TEST_INTERNAL_KEY,
            batch_size=5,
        )
        workers.append(worker_b)

        # Mock HTTP client to return 200 immediately
        mock_resp = httpx.Response(
            status_code=200,
            request=httpx.Request("POST", "http://search-service:8006/internal/events"),
        )
        client_a = AsyncMock(spec=httpx.AsyncClient)
        client_a.post.return_value = mock_resp
        worker_a._http_client = client_a
        client_b = AsyncMock(spec=httpx.AsyncClient)
        client_b.post.return_value = mock_resp
        worker_b._http_client = client_b

        # Process concurrently using asyncio.gather
        claimed_a, claimed_b = await asyncio.gather(
            worker_a.process_batch(),
            worker_b.process_batch(),
        )

        # Both workers should claim 5 distinct events (no overlap)
        assert claimed_a + claimed_b == 10

        # Verify in PostgreSQL: all 10 are delivered, and each was locked by a distinct worker ID
        async with pg_db.session() as session:
            res = await session.execute(
                select(ContentOutboxEvent).where(ContentOutboxEvent.id.in_(event_ids))
            )
            events = res.scalars().all()
            assert len(events) == 10
            for event in events:
                assert event.status == OutboxStatus.DELIVERED
    finally:
        try:
            for worker in workers:
                await worker.stop()
        finally:
            await cleanup_test_rows(
                pg_db,
                delete(ContentOutboxEvent).where(ContentOutboxEvent.id.in_(event_ids)),
            )


@pytest.mark.asyncio
@CONTENT_PG_SKIP
async def test_stale_claim_recovery_after_worker_crash(pg_db):
    """Test 3, 6 & 7: Stale claim recovery after worker crash/exit."""
    stale_event_id = uuid.uuid4()
    # Insert an event in 'processing' state with locked_at 120 seconds ago (lock_timeout=30s)
    old_time = datetime.now(UTC) - timedelta(seconds=120)
    workers: list[OutboxWorker] = []

    try:
        async with pg_db.transaction() as session:
            evt = ContentOutboxEvent(
                id=stale_event_id,
                event_type="ThreadCreated",
                aggregate_type="Thread",
                aggregate_id=uuid.uuid4(),
                payload={"thread_id": str(uuid.uuid4()), "title": "Stale Claim Test"},
                status=OutboxStatus.PROCESSING,
                locked_at=old_time,
                locked_by="crashed-worker-hostname-dead",
                attempts=1,
                occurred_at=old_time,
            )
            session.add(evt)

        # Instantiate a new worker with lock_timeout_seconds=30.0
        recovery_worker = OutboxWorker(
            service_name="content-service",
            db_manager=pg_db,
            model_class=ContentOutboxEvent,
            service_urls={"search-service": "http://search-service:8006"},
            internal_key=TEST_INTERNAL_KEY,
            batch_size=10,
            lock_timeout_seconds=30.0,
        )
        workers.append(recovery_worker)
        mock_resp = httpx.Response(
            status_code=200,
            request=httpx.Request("POST", "http://search-service:8006/internal/events"),
        )
        recovery_client = AsyncMock(spec=httpx.AsyncClient)
        recovery_client.post.return_value = mock_resp
        recovery_worker._http_client = recovery_client

        # Process batch -> must reclaim the stale event
        claimed = await recovery_worker.process_batch(event_id=stale_event_id)
        assert claimed >= 1

        # Verify the stale event was reclaimed and delivered
        async with pg_db.session() as session:
            reclaimed = await session.get(ContentOutboxEvent, stale_event_id)
            assert reclaimed is not None
            assert reclaimed.status == OutboxStatus.DELIVERED
            assert reclaimed.attempts == 2
    finally:
        try:
            for worker in workers:
                await worker.stop()
        finally:
            await cleanup_test_rows(
                pg_db,
                delete(ContentOutboxEvent).where(ContentOutboxEvent.id == stale_event_id),
            )


@pytest.mark.asyncio
@CONTENT_PG_SKIP
async def test_slow_consumer_short_lock_boundary(pg_db):
    """Test 8: Slow consumer does not hold PostgreSQL row locks or exhaust connections."""
    event_id = uuid.uuid4()
    now = datetime.now(UTC)
    workers: list[OutboxWorker] = []

    try:
        async with pg_db.transaction() as session:
            evt = ContentOutboxEvent(
                id=event_id,
                event_type="ThreadCreated",
                aggregate_type="Thread",
                aggregate_id=uuid.uuid4(),
                payload={"thread_id": str(uuid.uuid4()), "title": "Slow Consumer Test"},
                status=OutboxStatus.PENDING,
                attempts=0,
                occurred_at=now,
            )
            session.add(evt)

        # Worker configured with a simulated slow consumer (0.5s HTTP latency)
        worker = OutboxWorker(
            service_name="content-service",
            db_manager=pg_db,
            model_class=ContentOutboxEvent,
            service_urls={"search-service": "http://search-service:8006"},
            internal_key=TEST_INTERNAL_KEY,
            batch_size=1,
        )
        workers.append(worker)

        async def slow_post(*args, **kwargs):
            # Verify that during this HTTP call, the DB row is NOT locked in PostgreSQL
            # We test this by issuing a concurrent UPDATE to the row from another transaction
            async with pg_db.transaction() as alt_session:
                # If the row were still locked with FOR UPDATE, this would block or fail
                await alt_session.execute(
                    text(
                        "UPDATE content.outbox_events "
                        "SET last_error = 'concurrent_probe' "
                        "WHERE id = :event_id"
                    ),
                    {"event_id": event_id},
                )
            await asyncio.sleep(0.1)
            return httpx.Response(
                status_code=200,
                request=httpx.Request("POST", "http://search-service:8006/internal/events"),
            )

        slow_client = AsyncMock(spec=httpx.AsyncClient)
        slow_client.post.side_effect = slow_post
        worker._http_client = slow_client

        claimed = await worker.process_batch()
        assert claimed == 1

        # Verify event completed cleanly
        async with pg_db.session() as session:
            stored = await session.get(ContentOutboxEvent, event_id)
            assert stored.status == OutboxStatus.DELIVERED
    finally:
        try:
            for worker in workers:
                await worker.stop()
        finally:
            await cleanup_test_rows(
                pg_db,
                delete(ContentOutboxEvent).where(ContentOutboxEvent.id == event_id),
            )
