"""Task 031 – Email Dispatch Worker Test Suite.

Coverage:
1. EmailDispatchWorker.process_batch status transitions (pending→delivered, pending→failed→dead_letter).
2. Allowlist enforcement: recipient not in allowlist → dead_letter without SMTP call.
3. Exponential backoff calculation bounds and jitter.
4. NotificationEmailSink (memory backend) records messages and supports filtering / clearing.
5. Event handler queues EmailDelivery for mandatory_security category regardless of email_enabled pref.
6. Event handler skips EmailDelivery for optional_activity when email_enabled=False.
7. Duplicate event idempotency: second invocation with same event_id is a no-op.
8. Integration smoke test: UserRegistered event → EmailDelivery row inserted with status='pending'.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

import src.services.notification.database as notif_db_module
from src.common.database import DatabaseSessionManager
from src.services.notification.config import NotificationSettings, notification_settings
from src.services.notification.email_worker import (
    EmailDispatchWorker,
    EmailWorkerDeliveryError,
    NotificationEmailSink,
    calculate_email_backoff,
    dispatch_single_email,
    notification_email_sink,
)
from src.services.notification.models import (
    Base as NotificationBase,
    ConsumedEvent,
    EmailDelivery,
    NotificationItem,
    NotificationPreference,
)
from src.services.notification.schemas import ConsumedEventPayload

NOTIF_TEST_DB_URL = "sqlite+aiosqlite:////tmp/test_forum_task031_email_worker.db"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def notif_db():
    """Shared in-memory SQLite database for the full module scope."""
    import os

    db_path = "/tmp/test_forum_task031_email_worker.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    mgr = DatabaseSessionManager(NOTIF_TEST_DB_URL)
    async with mgr.engine.begin() as conn:
        # SQLite doesn't support schemas; patch the schema out temporarily
        for t in NotificationBase.metadata.tables.values():
            t.schema = None
        await conn.run_sync(NotificationBase.metadata.create_all)

    yield mgr

    await mgr.close()
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest_asyncio.fixture(autouse=True)
async def reset_sink():
    """Clear the global notification email sink before each test."""
    notification_email_sink.clear()
    yield
    notification_email_sink.clear()


def _make_delivery(
    *,
    recipient_email: str = "tester@example.test",
    status: str = "pending",
    attempts: int = 0,
    max_attempts: int = 5,
    next_attempt_at: datetime | None = None,
    category: str = "optional_activity",
) -> EmailDelivery:
    """Helper: construct an EmailDelivery ORM object without persisting it."""
    return EmailDelivery(
        id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        recipient_id=uuid.uuid4(),
        recipient_email=recipient_email,
        subject="Test Subject",
        body_text="Test body text.",
        body_html="<p>Test body html.</p>",
        category=category,
        status=status,
        attempts=attempts,
        max_attempts=max_attempts,
        next_attempt_at=next_attempt_at or datetime.now(UTC),
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Section 1: Backoff calculation
# ---------------------------------------------------------------------------


class TestCalculateEmailBackoff:
    def test_first_attempt_returns_near_base_delay(self):
        """Attempt 1 should produce a value around base_delay (2s) with jitter."""
        for _ in range(20):
            result = calculate_email_backoff(attempt=1, base_delay=2.0, max_delay=60.0)
            assert 1.0 <= result <= 3.0, f"Unexpected backoff for attempt 1: {result}"

    def test_increases_with_attempts(self):
        """Median backoff should grow with attempt number."""
        # Average over many samples to reduce jitter noise
        avg = lambda n: sum(calculate_email_backoff(n) for _ in range(50)) / 50
        assert avg(2) > avg(1)
        assert avg(3) > avg(2)
        assert avg(4) > avg(3)

    def test_capped_at_max_delay(self):
        """Very high attempt count should never exceed max_delay."""
        for attempt in (10, 20, 50):
            result = calculate_email_backoff(attempt=attempt, base_delay=2.0, max_delay=60.0)
            assert result <= 60.0, f"Backoff {result} exceeded max_delay at attempt {attempt}"

    def test_always_positive(self):
        for attempt in range(1, 8):
            result = calculate_email_backoff(attempt)
            assert result > 0


# ---------------------------------------------------------------------------
# Section 2: NotificationEmailSink (memory backend)
# ---------------------------------------------------------------------------


class TestNotificationEmailSink:
    def test_deliver_and_retrieve(self):
        sink = NotificationEmailSink()
        sink.deliver(
            recipient="a@b.com",
            subject="Hello",
            body_text="Text",
            body_html="<b>HTML</b>",
            category="optional_activity",
            timestamp=datetime.now(UTC),
        )
        msgs = sink.get_messages()
        assert len(msgs) == 1
        assert msgs[0].recipient == "a@b.com"
        assert msgs[0].subject == "Hello"

    def test_filter_by_recipient(self):
        sink = NotificationEmailSink()
        for addr in ("alice@test.com", "bob@test.com", "alice@test.com"):
            sink.deliver(addr, "S", "B", None, "optional_activity", datetime.now(UTC))
        assert len(sink.get_messages("alice@test.com")) == 2
        assert len(sink.get_messages("bob@test.com")) == 1

    def test_filter_case_insensitive(self):
        sink = NotificationEmailSink()
        sink.deliver("User@Example.COM", "S", "B", None, "optional_activity", datetime.now(UTC))
        assert len(sink.get_messages("user@example.com")) == 1

    def test_clear(self):
        sink = NotificationEmailSink()
        sink.deliver("x@y.com", "S", "B", None, "optional_activity", datetime.now(UTC))
        sink.clear()
        assert sink.get_messages() == []


# ---------------------------------------------------------------------------
# Section 3: dispatch_single_email
# ---------------------------------------------------------------------------


class TestDispatchSingleEmail:
    @pytest.mark.asyncio
    async def test_memory_backend_delivers_to_sink(self):
        """Memory backend should record to the global sink without SMTP."""
        delivery = _make_delivery(recipient_email="tester@example.test")

        # Patch the settings object on the email_worker module (not the Pydantic instance attribute)
        with patch("src.services.notification.email_worker.notification_settings") as mock_cfg:
            mock_cfg.email_delivery_backend = "memory"
            mock_cfg.is_recipient_allowed.return_value = True
            await dispatch_single_email(delivery)

        msgs = notification_email_sink.get_messages("tester@example.test")
        assert len(msgs) == 1
        assert msgs[0].subject == "Test Subject"

    @pytest.mark.asyncio
    async def test_allowlist_rejection_raises_error(self):
        """Recipient not in allowlist must raise EmailWorkerDeliveryError with is_recipient_rejected=True."""
        delivery = _make_delivery(recipient_email="stranger@evil.com")

        with patch("src.services.notification.email_worker.notification_settings") as mock_cfg:
            mock_cfg.email_delivery_backend = "memory"
            mock_cfg.is_recipient_allowed.return_value = False
            with pytest.raises(EmailWorkerDeliveryError) as exc_info:
                await dispatch_single_email(delivery)

        assert exc_info.value.is_recipient_rejected is True
        assert exc_info.value.recipient == "stranger@evil.com"

    @pytest.mark.asyncio
    async def test_unknown_backend_raises_value_error(self):
        delivery = _make_delivery(recipient_email="tester@example.test")

        with patch("src.services.notification.email_worker.notification_settings") as mock_cfg:
            mock_cfg.email_delivery_backend = "invalid_backend"
            mock_cfg.is_recipient_allowed.return_value = True
            with pytest.raises(ValueError, match="Unknown email delivery backend"):
                await dispatch_single_email(delivery)


# ---------------------------------------------------------------------------
# Section 4: EmailDispatchWorker.process_batch via DB (SQLite)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_batch_delivers_pending_email(notif_db):
    """process_batch should claim a pending delivery and mark it delivered (memory backend)."""
    delivery = _make_delivery(recipient_email="tester@example.test", status="pending")

    async with notif_db.transaction() as session:
        session.add(delivery)

    worker = EmailDispatchWorker(db_manager=notif_db, poll_interval=999, batch_size=10)

    with patch("src.services.notification.email_worker.notification_settings") as mock_cfg:
            mock_cfg.email_delivery_backend = "memory"
            mock_cfg.is_recipient_allowed.return_value = True
            processed = await worker.process_batch()

    assert processed >= 1

    async with notif_db.session() as session:
        row = (await session.execute(select(EmailDelivery).where(EmailDelivery.id == delivery.id))).scalar_one()
        assert row.status == "delivered"
        assert row.attempts == 1
        assert row.delivered_at is not None
        assert row.last_error is None


@pytest.mark.asyncio
async def test_process_batch_marks_failed_on_delivery_error(notif_db):
    """process_batch should mark delivery as 'failed' and schedule a retry on transient error."""
    delivery = _make_delivery(recipient_email="tester@example.test", status="pending", attempts=0, max_attempts=5)

    async with notif_db.transaction() as session:
        session.add(delivery)

    worker = EmailDispatchWorker(db_manager=notif_db, poll_interval=999, batch_size=10)

    with patch(
        "src.services.notification.email_worker.dispatch_single_email",
        new_callable=AsyncMock,
        side_effect=EmailWorkerDeliveryError("SMTP timeout", recipient="tester@example.test", is_recipient_rejected=False),
    ):
        await worker.process_batch()

    async with notif_db.session() as session:
        row = (await session.execute(select(EmailDelivery).where(EmailDelivery.id == delivery.id))).scalar_one()
        assert row.status == "failed"
        assert row.attempts == 1
        # SQLite returns naive datetimes; strip tz for comparison
        next_at = row.next_attempt_at.replace(tzinfo=None) if row.next_attempt_at.tzinfo else row.next_attempt_at
        assert next_at > datetime.now(UTC).replace(tzinfo=None)
        assert "SMTP timeout" in (row.last_error or "")


@pytest.mark.asyncio
async def test_process_batch_dead_letter_on_recipient_rejected(notif_db):
    """Allowlist rejection (is_recipient_rejected=True) must immediately move to dead_letter."""
    delivery = _make_delivery(recipient_email="blocked@evil.com", status="pending", max_attempts=5)

    async with notif_db.transaction() as session:
        session.add(delivery)

    worker = EmailDispatchWorker(db_manager=notif_db, poll_interval=999, batch_size=10)

    with patch(
        "src.services.notification.email_worker.dispatch_single_email",
        new_callable=AsyncMock,
        side_effect=EmailWorkerDeliveryError("Allowlist rejected", recipient="blocked@evil.com", is_recipient_rejected=True),
    ):
        await worker.process_batch()

    async with notif_db.session() as session:
        row = (await session.execute(select(EmailDelivery).where(EmailDelivery.id == delivery.id))).scalar_one()
        assert row.status == "dead_letter"
        assert row.attempts == 1


@pytest.mark.asyncio
async def test_process_batch_dead_letter_on_max_attempts_reached(notif_db):
    """When attempts reach max_attempts on failure, delivery must move to dead_letter."""
    delivery = _make_delivery(
        recipient_email="tester@example.test",
        status="pending",
        attempts=4,  # one more will hit max_attempts=5
        max_attempts=5,
    )

    async with notif_db.transaction() as session:
        session.add(delivery)

    worker = EmailDispatchWorker(db_manager=notif_db, poll_interval=999, batch_size=10)

    with patch(
        "src.services.notification.email_worker.dispatch_single_email",
        new_callable=AsyncMock,
        side_effect=EmailWorkerDeliveryError("Connection refused", recipient="tester@example.test", is_recipient_rejected=False),
    ):
        await worker.process_batch()

    async with notif_db.session() as session:
        row = (await session.execute(select(EmailDelivery).where(EmailDelivery.id == delivery.id))).scalar_one()
        assert row.status == "dead_letter"
        assert row.attempts == 5


@pytest.mark.asyncio
async def test_process_batch_skips_future_next_attempt_at(notif_db):
    """Deliveries with next_attempt_at in the future must NOT be claimed."""
    delivery = _make_delivery(
        recipient_email="tester@example.test",
        status="failed",
        attempts=1,
        next_attempt_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async with notif_db.transaction() as session:
        session.add(delivery)

    worker = EmailDispatchWorker(db_manager=notif_db, poll_interval=999, batch_size=10)

    with patch("src.services.notification.email_worker.notification_settings") as mock_cfg:
        mock_cfg.email_delivery_backend = "memory"
        mock_cfg.is_recipient_allowed.return_value = True
        await worker.process_batch()

    # Should not process this specific delivery (though it may process earlier ones)
    async with notif_db.session() as session:
        row = (await session.execute(select(EmailDelivery).where(EmailDelivery.id == delivery.id))).scalar_one()
        # The row should remain failed (not claimed)
        assert row.status == "failed"


@pytest.mark.asyncio
async def test_process_batch_returns_zero_when_empty(notif_db):
    """process_batch on an empty or future-only queue returns 0."""
    worker = EmailDispatchWorker(db_manager=notif_db, poll_interval=999, batch_size=10)

    # Patch dispatch to succeed for any accidentally picked rows
    with patch(
        "src.services.notification.email_worker.dispatch_single_email",
        new_callable=AsyncMock,
    ):
        # Consume any remaining pending rows first
        await worker.process_batch()
        # Then check that a second call finds nothing
        result = await worker.process_batch()

    assert result == 0


# ---------------------------------------------------------------------------
# Section 5: EmailDispatchWorker lifecycle (start / stop)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_start_stop_lifecycle(notif_db):
    """Worker should start, run one poll cycle, then stop cleanly."""
    import asyncio

    worker = EmailDispatchWorker(db_manager=notif_db, poll_interval=0.05, batch_size=1)

    with patch(
        "src.services.notification.email_worker.dispatch_single_email",
        new_callable=AsyncMock,
    ):
        await worker.start()
        assert worker._running is True
        assert worker._task is not None

        await asyncio.sleep(0.12)  # allow a couple of poll cycles

        await worker.stop()
        assert worker._running is False
        assert worker._task is None


@pytest.mark.asyncio
async def test_worker_start_idempotent(notif_db):
    """Calling start() twice must not create duplicate tasks."""
    worker = EmailDispatchWorker(db_manager=notif_db, poll_interval=999, batch_size=1)
    with patch("src.services.notification.email_worker.dispatch_single_email", new_callable=AsyncMock):
        await worker.start()
        task1 = worker._task
        await worker.start()
        task2 = worker._task
        assert task1 is task2
        await worker.stop()


# ---------------------------------------------------------------------------
# Section 6: Event handler integration – EmailDelivery queuing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_registered_queues_email_delivery(notif_db):
    """UserRegistered event must queue an EmailDelivery with category='mandatory_security'."""
    from src.services.notification.events import EventHandlerRegistry

    user_id = uuid.uuid4()
    event_id = uuid.uuid4()

    payload = ConsumedEventPayload(
        event_id=event_id,
        event_type="UserRegistered",
        data={
            "user_id": str(user_id),
            "username": "testuser",
            "email": "tester@example.test",
        },
    )

    # Override DB manager used by tests to our SQLite instance
    original_db = notif_db_module.notification_db
    notif_db_module.notification_db = notif_db

    try:
        async with notif_db.transaction() as session:
            created = await EventHandlerRegistry.process_event(session, payload)
    finally:
        notif_db_module.notification_db = original_db

    # NotificationItem should be created (in_app_enabled defaults to True)
    assert len(created) == 1

    # EmailDelivery should exist for this event
    async with notif_db.session() as session:
        row = (await session.execute(
            select(EmailDelivery).where(EmailDelivery.event_id == event_id)
        )).scalar_one_or_none()

    assert row is not None, "EmailDelivery row was not inserted for UserRegistered event"
    assert row.recipient_email == "tester@example.test"
    assert row.status == "pending"
    assert row.attempts == 0
    assert row.category == "mandatory_security"


@pytest.mark.asyncio
async def test_email_not_queued_when_email_disabled_for_optional_activity(notif_db):
    """Activity email (comment) must NOT be queued when user has email_enabled=False."""
    from src.services.notification.events import EventHandlerRegistry

    user_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    event_id = uuid.uuid4()

    # Pre-create preference with email_enabled=False
    async with notif_db.transaction() as session:
        pref = NotificationPreference(user_id=user_id, in_app_enabled=True, email_enabled=False)
        session.add(pref)

    payload = ConsumedEventPayload(
        event_id=event_id,
        event_type="CommentCreated",
        data={
            "author_id": str(actor_id),
            "recipient_id": str(user_id),
            "comment_id": str(uuid.uuid4()),
            "body_preview": "Just wanted to say hi!",
        },
    )

    original_db = notif_db_module.notification_db
    notif_db_module.notification_db = notif_db

    try:
        async with notif_db.transaction() as session:
            await EventHandlerRegistry.process_event(session, payload)
    finally:
        notif_db_module.notification_db = original_db

    async with notif_db.session() as session:
        row = (await session.execute(
            select(EmailDelivery).where(EmailDelivery.event_id == event_id)
        )).scalar_one_or_none()

    assert row is None, "EmailDelivery must not be queued when email_enabled=False for optional_activity"


@pytest.mark.asyncio
async def test_moderation_email_queued_regardless_of_preference(notif_db):
    """Mandatory security (moderation ban) email must be queued even when email_enabled=False.

    The render_moderation_email produces category='mandatory_security', which bypasses
    the email_enabled preference gate.
    """
    from src.services.notification.events import EventHandlerRegistry

    user_id = uuid.uuid4()
    event_id = uuid.uuid4()

    # User has email disabled
    async with notif_db.transaction() as session:
        pref = NotificationPreference(user_id=user_id, in_app_enabled=True, email_enabled=False)
        session.add(pref)

    payload = ConsumedEventPayload(
        event_id=event_id,
        event_type="ModerationActionApplied",
        data={
            "action": "ban",
            "target_type": "account",
            "target_id": str(user_id),
            "affected_user_id": str(user_id),
            "reason": "Repeated violations",
        },
    )

    original_db = notif_db_module.notification_db
    notif_db_module.notification_db = notif_db

    # Mock _fetch_recipient_email to return a known address
    with patch.object(
        EventHandlerRegistry,
        "_fetch_recipient_email",
        new_callable=AsyncMock,
        return_value="tester@example.test",
    ):
        try:
            async with notif_db.transaction() as session:
                await EventHandlerRegistry.process_event(session, payload)
        finally:
            notif_db_module.notification_db = original_db

    async with notif_db.session() as session:
        row = (await session.execute(
            select(EmailDelivery).where(EmailDelivery.event_id == event_id)
        )).scalar_one_or_none()

    assert row is not None, "Mandatory security email must be queued even when email_enabled=False"
    assert row.category == "mandatory_security"


@pytest.mark.asyncio
async def test_duplicate_event_is_idempotent(notif_db):
    """Processing the same event_id twice for the same recipient must be a no-op."""
    from src.services.notification.events import EventHandlerRegistry

    user_id = uuid.uuid4()
    event_id = uuid.uuid4()

    payload = ConsumedEventPayload(
        event_id=event_id,
        event_type="UserRegistered",
        data={
            "user_id": str(user_id),
            "username": "dupeuser",
            "email": "tester@example.test",
        },
    )

    original_db = notif_db_module.notification_db
    notif_db_module.notification_db = notif_db

    try:
        async with notif_db.transaction() as session:
            created1 = await EventHandlerRegistry.process_event(session, payload)

        async with notif_db.transaction() as session:
            created2 = await EventHandlerRegistry.process_event(session, payload)
    finally:
        notif_db_module.notification_db = original_db

    assert len(created1) == 1
    assert len(created2) == 0, "Duplicate event must be deduplicated via ConsumedEvent"

    async with notif_db.session() as session:
        rows = (await session.execute(
            select(EmailDelivery).where(EmailDelivery.event_id == event_id)
        )).scalars().all()

    assert len(rows) == 1, "Only one EmailDelivery row should exist after duplicate events"
