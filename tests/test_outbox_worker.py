"""Unit and integration tests for the autonomous transactional OutboxWorker."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import declarative_base

from src.common.database import DatabaseSessionManager
from src.common.outbox import OutboxEventMixin, OutboxStatus
from src.common.outbox_worker import OutboxWorker, calculate_backoff

Base = declarative_base()


class SampleOutbox(Base, OutboxEventMixin):
    __tablename__ = "sample_outbox"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type = Column(String(100), nullable=False)
    aggregate_type = Column(String(100), nullable=False)
    aggregate_id = Column(UUID(as_uuid=True), nullable=False)
    payload = Column(JSONB, nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


@pytest.fixture
async def test_db():
    db = DatabaseSessionManager(
        database_url="sqlite+aiosqlite:///:memory:",
        pool_size=5,
        max_overflow=0,
    )
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield db
    await db.close()


def test_calculate_backoff():
    # Base = 1.0, Jitter = 0.5. At attempt 1: 1.0 * 2^0 = 1.0 +/- 0.5 -> [0.5, 1.5]
    for attempt in range(1, 6):
        delay = calculate_backoff(attempt, base_delay=1.0, max_delay=60.0)
        assert 0.0 < delay <= 60.0

    # Max delay clamp
    delay_large = calculate_backoff(20, base_delay=1.0, max_delay=60.0)
    assert delay_large <= 60.0


@pytest.mark.asyncio
async def test_outbox_worker_deliver_success(test_db):
    # Insert a sample event
    event_id = uuid.uuid4()
    async with test_db.session() as session:
        event = SampleOutbox(
            id=event_id,
            event_type="ThreadCreated",
            aggregate_type="Thread",
            aggregate_id=uuid.uuid4(),
            payload={"thread_id": str(uuid.uuid4()), "title": "Test Title"},
            status=OutboxStatus.PENDING,
        )
        session.add(event)
        await session.commit()

    # Mock HTTP client to return 200 OK
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_resp = httpx.Response(
        status_code=200, request=httpx.Request("POST", "http://search-service:8006/internal/events")
    )
    mock_client.post.return_value = mock_resp

    worker = OutboxWorker(
        service_name="content-service",
        db_manager=test_db,
        model_class=SampleOutbox,
        service_urls={
            "search-service": "http://search-service:8006",
            "notification-service": "http://notification-service:8005",
        },
        max_attempts=3,
        poll_interval_seconds=0.1,
    )
    worker._http_client = mock_client

    claimed = await worker.process_batch()
    assert claimed == 1

    # Verify event is delivered
    async with test_db.session() as session:
        stored = await session.get(SampleOutbox, event_id)
        assert stored is not None
        assert stored.status == OutboxStatus.DELIVERED
        assert stored.locked_by is None
        assert stored.last_error is None


@pytest.mark.asyncio
async def test_outbox_worker_retry_backoff_and_dead_letter(test_db):
    event_id = uuid.uuid4()
    async with test_db.session() as session:
        event = SampleOutbox(
            id=event_id,
            event_type="VoteChanged",
            aggregate_type="Vote",
            aggregate_id=uuid.uuid4(),
            payload={"target_id": str(uuid.uuid4())},
            status=OutboxStatus.PENDING,
            attempts=0,
        )
        session.add(event)
        await session.commit()

    # Mock HTTP client to simulate upstream 503 error
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_resp = httpx.Response(
        status_code=503,
        text="Service Unavailable",
        request=httpx.Request("POST", "http://notification-service:8005/internal/events"),
    )
    mock_client.post.return_value = mock_resp

    worker = OutboxWorker(
        service_name="interaction-service",
        db_manager=test_db,
        model_class=SampleOutbox,
        service_urls={"notification-service": "http://notification-service:8005"},
        max_attempts=2,
        base_backoff_seconds=0.5,
        poll_interval_seconds=0.1,
    )
    worker._http_client = mock_client

    # Attempt 1 -> Status: FAILED, Attempts: 1
    claimed = await worker.process_batch()
    assert claimed == 1
    async with test_db.session() as session:
        stored = await session.get(SampleOutbox, event_id)
        assert stored.status == OutboxStatus.FAILED
        assert stored.attempts == 1
        assert stored.next_retry_at is not None
        assert "Service Unavailable" in stored.last_error

        # Manually advance next_retry_at to past so it can be retried immediately
        stored.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    # Attempt 2 -> Reaches max_retries (2) -> Status: DEAD_LETTER
    claimed2 = await worker.process_batch()
    assert claimed2 == 1
    async with test_db.session() as session:
        stored = await session.get(SampleOutbox, event_id)
        assert stored.status == OutboxStatus.DEAD_LETTER
        assert stored.attempts == 2
        assert stored.next_retry_at is None
        assert "DEAD_LETTER" in stored.last_error or "Service Unavailable" in stored.last_error
