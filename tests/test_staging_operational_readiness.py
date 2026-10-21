"""Unit and failure-injection tests for Phase C & Phase E: Staging Security and Worker Lifecycle.

Verifies:
1. Staging and production configuration rejects unsafe defaults and short keys.
2. Graceful shutdown while worker is idle completes cleanly.
3. Shutdown during in-flight dispatch waits for completion within drain timeout without losing status.
4. Repeated application startup/shutdown lifespans do not spawn duplicate worker tasks.
5. Metrics endpoint produces valid Prometheus text format without sensitive leakage.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from src.common.config import BaseServiceSettings
from src.common.database import DatabaseSessionManager
from src.common.metrics import MetricsRegistry
from src.common.outbox import OutboxStatus
from src.common.outbox_worker import OutboxWorker
from src.services.content.models import ContentOutboxEvent
from src.services.user.config import UserSettings

POSTGRES_CONTENT_URL = (
    "postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_content"
)


# =============================================================================
# Phase C: Secrets and Staging Configuration Tests
# =============================================================================
def test_staging_configuration_rejects_unsafe_defaults():
    """Verify that setting ENVIRONMENT=staging or production fails fast on default development secrets."""
    # 1. Rejects default internal service secret in staging
    with pytest.raises(ValidationError) as exc1:
        BaseServiceSettings(
            environment="staging",
            database_url="postgresql+asyncpg://prod_user:strong_password_12345@localhost:5432/forum_test",
            internal_service_secret="default-internal-service-secret-key-32chars",
        )
    assert "requires a non-default, secure INTERNAL_SERVICE_SECRET" in str(exc1.value)

    # 2. Rejects short internal service secret in production (<32 chars)
    with pytest.raises(ValidationError) as exc2:
        BaseServiceSettings(
            environment="production",
            database_url="postgresql+asyncpg://prod_user:strong_password_12345@localhost:5432/forum_test",
            internal_service_secret="too-short-secret",
        )
    assert "must be at least 32 characters long" in str(exc2.value)

    # 3. Rejects development database password in staging
    with pytest.raises(ValidationError) as exc3:
        BaseServiceSettings(
            environment="staging",
            database_url="postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_test",
            internal_service_secret="a_very_secure_and_random_internal_secret_string_32",
        )
    assert "must not contain default development password" in str(exc3.value)

    # 4. UserSettings rejects default csrf_secret in production
    with pytest.raises(ValidationError) as exc4:
        UserSettings(
            environment="production",
            database_url="postgresql+asyncpg://prod_user:strong_password_12345@localhost:5432/forum_users",
            internal_service_secret="a_very_secure_and_random_internal_secret_string_32",
            csrf_secret="default-dev-csrf-secret",
        )
    assert "requires a non-default, cryptographically secure CSRF_SECRET" in str(exc4.value)

    # 5. Safe staging configuration passes validation
    valid_staging = BaseServiceSettings(
        environment="staging",
        database_url="postgresql+asyncpg://staging_user:Staging_Secure_Pass_9988@staging-db:5432/forum_content",
        internal_service_secret="staging-secret-key-must-be-at-least-32-chars-long",
    )
    assert valid_staging.environment == "staging"

    # 6. Verify cookie security behavior across environments
    dev_user_settings = UserSettings(
        environment="development",
        database_url="postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_users",
    )
    assert dev_user_settings.is_cookie_secure() is False

    staging_user_settings = UserSettings(
        environment="staging",
        database_url="postgresql+asyncpg://staging_user:Staging_Secure_Pass_9988@staging-db:5432/forum_users",
        internal_service_secret="staging-secret-key-must-be-at-least-32-chars-long",
        csrf_secret="staging-secure-csrf-token-secret-minimum-32-chars",
    )
    assert staging_user_settings.is_cookie_secure() is True

    # Explicit override test
    override_user_settings = UserSettings(
        environment="staging",
        session_cookie_secure=False,
        database_url="postgresql+asyncpg://staging_user:Staging_Secure_Pass_9988@staging-db:5432/forum_users",
        internal_service_secret="staging-secret-key-must-be-at-least-32-chars-long",
        csrf_secret="staging-secure-csrf-token-secret-minimum-32-chars",
    )
    assert override_user_settings.is_cookie_secure() is False


# =============================================================================
# Phase E: Graceful Shutdown and Lifecycle Failure-Injection Tests
# =============================================================================
@pytest.fixture
async def content_db():
    db = DatabaseSessionManager(
        database_url=POSTGRES_CONTENT_URL,
        pool_size=5,
        max_overflow=2,
    )
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_worker_graceful_shutdown_idle(content_db):
    """Proves that graceful shutdown of an idle worker loop cleans up resources cleanly."""
    worker = OutboxWorker(
        service_name="content-service",
        db_manager=content_db,
        model_class=ContentOutboxEvent,
        service_urls={},
        poll_interval_seconds=0.1,
    )
    await worker.start()
    assert worker._running is True
    assert worker._task is not None
    assert not worker._task.done()

    # Graceful stop
    await worker.stop()
    assert worker._running is False
    assert worker._task.done()
    assert worker._http_client is None


@pytest.mark.asyncio
async def test_worker_repeated_start_does_not_spawn_duplicate_tasks(content_db):
    """Proves that repeated calls to start() do not spawn duplicate polling loops."""
    worker = OutboxWorker(
        service_name="content-service",
        db_manager=content_db,
        model_class=ContentOutboxEvent,
        service_urls={},
        poll_interval_seconds=0.1,
    )
    await worker.start()
    task1 = worker._task

    # Call start() again while running
    await worker.start()
    task2 = worker._task

    assert task1 is task2
    await worker.stop()


@pytest.mark.asyncio
async def test_worker_shutdown_during_active_dispatch(content_db):
    """Proves that worker stop(drain_timeout_seconds=2.0) permits active in-flight dispatch to complete."""
    event_id = uuid.uuid4()
    now = datetime.now(UTC)

    async with content_db.transaction() as session:
        evt = ContentOutboxEvent(
            id=event_id,
            event_type="ThreadCreated",
            aggregate_type="Thread",
            aggregate_id=uuid.uuid4(),
            payload={"thread_id": str(uuid.uuid4()), "title": "Shutdown In Flight Test"},
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
        poll_interval_seconds=0.05,
    )

    # Simulate in-flight HTTP request taking 0.2 seconds
    async def slow_post(*args, **kwargs):
        await asyncio.sleep(0.2)
        return httpx.Response(status_code=200, request=httpx.Request("POST", "http://test"))

    await worker.start()
    worker._http_client = AsyncMock(spec=httpx.AsyncClient)
    worker._http_client.post = AsyncMock(side_effect=slow_post)

    # Allow worker to start processing batch
    await asyncio.sleep(0.08)

    # Trigger graceful shutdown with 1.0s drain timeout
    await worker.stop(drain_timeout_seconds=1.0)
    assert worker._running is False

    # Verify event was successfully completed and delivered
    async with content_db.session() as session:
        delivered_evt = await session.get(ContentOutboxEvent, event_id)
        assert delivered_evt is not None
        assert delivered_evt.status == OutboxStatus.DELIVERED
        assert delivered_evt.attempts == 1


# =============================================================================
# Phase B: Metrics Endpoint Format & Sanitization Tests
# =============================================================================
def test_metrics_registry_output_sanitization():
    """Verify that Prometheus metrics format contains expected signals without sensitive data leakage."""
    reg = MetricsRegistry("test-service")
    reg.inc_counter(
        "http_requests_total",
        value=5.0,
        labels={"method": "GET", "status_code": "200", "status_class": "2xx"},
    )
    reg.set_gauge("database_pool_size", 10.0)
    reg.set_gauge("database_pool_checked_out", 3.0)
    reg.set_gauge("outbox_events_count", 0.0, labels={"status": "pending"})

    text_output = reg.generate_prometheus_text()
    assert "# TYPE http_requests_total counter" in text_output
    assert (
        'http_requests_total{service="test-service",method="GET",status_class="2xx",status_code="200"} 5.0'
        in text_output
    )
    assert "# TYPE database_pool_size gauge" in text_output
    assert 'database_pool_size{service="test-service"} 10.0' in text_output
    assert 'database_pool_checked_out{service="test-service"} 3.0' in text_output
    assert 'outbox_events_count{service="test-service",status="pending"} 0.0' in text_output

    # Verify absence of sensitive keywords in metric labels or names
    for sensitive in ["password", "token", "secret", "csrf", "cookie", "session_id"]:
        assert sensitive not in text_output.lower()
