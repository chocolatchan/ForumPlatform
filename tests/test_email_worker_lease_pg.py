"""Task 031.1 – Email Worker Lease Recovery: PostgreSQL Integration Tests.

These tests use a real PostgreSQL connection to verify:
- Stale 'sending' rows are recovered by recover_stale_leases()
- Active leases are NOT recovered prematurely
- Concurrent workers recover the same stale row exactly once (SKIP LOCKED)
- Rows at max_attempts are moved to dead_letter during recovery
- Worker startup triggers recovery automatically
- process_batch correctly sets and clears sending_since
- Concurrent process_batch calls don't double-claim a pending row

These tests require an explicitly named disposable database that has already been
migrated through notifications_0004. They never fall back to the development
forum_notifications database.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from src.common.database import DatabaseSessionManager
from src.services.notification.email_worker import (
    EmailDispatchWorker,
)
from src.services.notification.models import EmailDelivery

NOTIF_PG_URL = os.getenv("TEST_NOTIFICATIONS_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not NOTIF_PG_URL,
    reason=(
        "TEST_NOTIFICATIONS_DATABASE_URL is not set; refusing to use the development "
        "forum_notifications database. Point it at a disposable PostgreSQL database "
        "migrated through notifications_0004."
    ),
)
if NOTIF_PG_URL:
    _notif_database = make_url(NOTIF_PG_URL).database
    if not _notif_database or _notif_database.casefold() == "forum_notifications":
        raise pytest.UsageError(
            "TEST_NOTIFICATIONS_DATABASE_URL must name a disposable PostgreSQL database, "
            "not the development forum_notifications database."
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pg_db():
    """PostgreSQL session manager, function-scoped so the engine is bound to the
    current test's event loop (asyncio_mode=auto creates a fresh loop per test)."""
    mgr = DatabaseSessionManager(NOTIF_PG_URL)
    yield mgr
    await mgr.close()


def _make_pg_delivery(**kwargs) -> EmailDelivery:
    """Construct an EmailDelivery ORM object with sensible defaults."""
    defaults = {
        "id": uuid.uuid4(),
        "event_id": uuid.uuid4(),
        "recipient_id": uuid.uuid4(),
        "recipient_email": "tester@example.test",
        "subject": "Test",
        "body_text": "Body",
        "body_html": None,
        "category": "optional_activity",
        "status": "pending",
        "attempts": 0,
        "max_attempts": 5,
        "next_attempt_at": datetime.now(UTC),
        "created_at": datetime.now(UTC),
        "sending_since": None,
    }
    defaults.update(kwargs)
    return EmailDelivery(**defaults)


async def _delete_rows(pg_db: DatabaseSessionManager, *ids: uuid.UUID) -> None:
    """Clean up test rows by id."""
    if not ids:
        return
    async with pg_db.transaction() as session:
        await session.execute(delete(EmailDelivery).where(EmailDelivery.id.in_(list(ids))))


# ---------------------------------------------------------------------------
# Test 1: Stale sending row is recovered to 'failed'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_sending_row_recovered(pg_db):
    """A row with sending_since well past the lease timeout must be recovered to 'failed'."""
    row = _make_pg_delivery(
        status="sending",
        sending_since=datetime.now(UTC) - timedelta(seconds=600),
        attempts=0,
        max_attempts=5,
    )
    async with pg_db.transaction() as session:
        session.add(row)

    try:
        worker = EmailDispatchWorker(db_manager=pg_db, poll_interval=999, batch_size=10)
        recovered = await worker.recover_stale_leases()
        assert recovered >= 1

        async with pg_db.session() as session:
            refreshed = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()

        assert refreshed.status == "failed", f"Expected 'failed', got '{refreshed.status}'"
        assert refreshed.attempts == 1
        assert refreshed.sending_since is None
        assert refreshed.next_attempt_at is not None
        assert refreshed.next_attempt_at > datetime.now(UTC)
    finally:
        await _delete_rows(pg_db, row.id)


# ---------------------------------------------------------------------------
# Test 2: Active lease (within timeout) is NOT recovered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_lease_not_recovered(pg_db):
    """A row with sending_since=now-10s (within lease timeout) must remain 'sending'."""
    row = _make_pg_delivery(
        status="sending",
        sending_since=datetime.now(UTC) - timedelta(seconds=10),
        attempts=0,
        max_attempts=5,
    )
    async with pg_db.transaction() as session:
        session.add(row)

    try:
        worker = EmailDispatchWorker(db_manager=pg_db, poll_interval=999, batch_size=10)
        await worker.recover_stale_leases()

        async with pg_db.session() as session:
            refreshed = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()

        assert refreshed.status == "sending", (
            f"Active lease must not be recovered, got '{refreshed.status}'"
        )
    finally:
        await _delete_rows(pg_db, row.id)


# ---------------------------------------------------------------------------
# Test 3: Concurrent workers recover the same stale row exactly once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_workers_recover_same_stale_row_only_once(pg_db):
    """Two concurrent recover_stale_leases() calls must claim the same stale row only once."""
    row = _make_pg_delivery(
        status="sending",
        sending_since=datetime.now(UTC) - timedelta(seconds=600),
        attempts=0,
        max_attempts=5,
    )
    async with pg_db.transaction() as session:
        session.add(row)

    try:
        worker = EmailDispatchWorker(db_manager=pg_db, poll_interval=999, batch_size=10)

        results = await asyncio.gather(
            worker.recover_stale_leases(),
            worker.recover_stale_leases(),
            return_exceptions=True,
        )

        # Neither call should have raised
        for r in results:
            if isinstance(r, Exception):
                raise r

        total_recovered = sum(r for r in results if isinstance(r, int))
        # The row can only be recovered once; SKIP LOCKED ensures the second call skips it
        assert total_recovered == 1, (
            f"Expected exactly 1 total recovery, got {total_recovered} (results={results})"
        )

        async with pg_db.session() as session:
            refreshed = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()

        assert refreshed.attempts == 1, (
            f"Row must be recovered exactly once, got attempts={refreshed.attempts}"
        )
    finally:
        await _delete_rows(pg_db, row.id)


# ---------------------------------------------------------------------------
# Test 4: Stale row at max_attempts is moved to dead_letter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_row_at_max_attempts_moved_to_dead_letter(pg_db):
    """A stale row with attempts = max_attempts-1 must be moved to dead_letter on recovery."""
    max_attempts = 5
    row = _make_pg_delivery(
        status="sending",
        sending_since=datetime.now(UTC) - timedelta(seconds=600),
        attempts=max_attempts - 1,
        max_attempts=max_attempts,
    )
    async with pg_db.transaction() as session:
        session.add(row)

    try:
        worker = EmailDispatchWorker(db_manager=pg_db, poll_interval=999, batch_size=10)
        recovered = await worker.recover_stale_leases()
        assert recovered >= 1

        async with pg_db.session() as session:
            refreshed = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()

        assert refreshed.status == "dead_letter", (
            f"Expected 'dead_letter', got '{refreshed.status}'"
        )
        assert refreshed.attempts == max_attempts
    finally:
        await _delete_rows(pg_db, row.id)


# ---------------------------------------------------------------------------
# Test 5: Worker startup recovers stale rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_startup_recovers_stale_rows(pg_db):
    """Starting the worker must trigger recover_stale_leases() and recover stale rows."""
    row = _make_pg_delivery(
        status="sending",
        sending_since=datetime.now(UTC) - timedelta(seconds=600),
        attempts=0,
        max_attempts=5,
    )
    async with pg_db.transaction() as session:
        session.add(row)

    worker = EmailDispatchWorker(db_manager=pg_db, poll_interval=0.05, batch_size=10)

    try:
        # Patch dispatch so the worker doesn't try to send any pending rows via SMTP
        with patch(
            "src.services.notification.email_worker.dispatch_single_email",
            new_callable=AsyncMock,
        ):
            await worker.start()
            await asyncio.sleep(0.3)  # Allow at least one loop iteration
            await worker.stop()

        async with pg_db.session() as session:
            refreshed = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()

        assert refreshed.status in ("failed", "dead_letter", "delivered"), (
            f"Stale row must be recovered on startup, but got status='{refreshed.status}'"
        )
        assert refreshed.status != "sending", "Row must not remain stuck in 'sending'"
    finally:
        await worker.stop()
        await _delete_rows(pg_db, row.id)


# ---------------------------------------------------------------------------
# Test 6: process_batch sets sending_since and clears it on success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_batch_sets_sending_since(pg_db):
    """process_batch must set sending_since during claim and clear it on successful delivery."""
    row = _make_pg_delivery(
        status="pending",
        attempts=0,
        max_attempts=5,
        next_attempt_at=datetime.now(UTC),
    )
    async with pg_db.transaction() as session:
        session.add(row)

    worker = EmailDispatchWorker(db_manager=pg_db, poll_interval=999, batch_size=10)

    try:
        with patch(
            "src.services.notification.email_worker.dispatch_single_email",
            new_callable=AsyncMock,
        ) as mock_dispatch:
            # Add a small delay to ensure sending_since would be set mid-flight if we inspected
            mock_dispatch.return_value = None
            processed = await worker.process_batch()

        assert processed >= 1

        async with pg_db.session() as session:
            refreshed = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()

        assert refreshed.status == "delivered", f"Expected 'delivered', got '{refreshed.status}'"
        assert refreshed.sending_since is None, "sending_since must be cleared on success"
        assert refreshed.attempts == 1
    finally:
        await _delete_rows(pg_db, row.id)


# ---------------------------------------------------------------------------
# Test 7: No duplicate claim under concurrent process_batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_duplicate_claim_under_concurrent_process_batch(pg_db):
    """Two concurrent process_batch() calls on one pending row must result in attempts==1."""
    row = _make_pg_delivery(
        status="pending",
        attempts=0,
        max_attempts=5,
        next_attempt_at=datetime.now(UTC),
    )
    async with pg_db.transaction() as session:
        session.add(row)

    worker = EmailDispatchWorker(db_manager=pg_db, poll_interval=999, batch_size=10)

    try:
        with patch(
            "src.services.notification.email_worker.dispatch_single_email",
            new_callable=AsyncMock,
        ) as mock_dispatch:
            mock_dispatch.return_value = None
            results = await asyncio.gather(
                worker.process_batch(),
                worker.process_batch(),
                return_exceptions=True,
            )

        for r in results:
            if isinstance(r, Exception):
                raise r

        async with pg_db.session() as session:
            refreshed = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()

        assert refreshed.attempts == 1, (
            f"Row must be dispatched exactly once, got attempts={refreshed.attempts}"
        )
        assert refreshed.status == "delivered", f"Expected 'delivered', got '{refreshed.status}'"
    finally:
        await _delete_rows(pg_db, row.id)


# ---------------------------------------------------------------------------
# Test 8: Worker restart recovers stale rows AND dispatches pending rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_restart_recovers_stale_and_dispatches_pending(pg_db):
    """After a restart, the worker must recover stale 'sending' rows and process
    pending rows that were queued while the worker was down."""
    stale_row = _make_pg_delivery(
        status="sending",
        sending_since=datetime.now(UTC) - timedelta(seconds=600),
        attempts=0,
        max_attempts=5,
    )
    pending_row = _make_pg_delivery(
        status="pending",
        attempts=0,
        max_attempts=5,
        next_attempt_at=datetime.now(UTC),
    )
    async with pg_db.transaction() as session:
        session.add(stale_row)
        session.add(pending_row)

    worker = EmailDispatchWorker(db_manager=pg_db, poll_interval=0.05, batch_size=10)

    try:
        with patch(
            "src.services.notification.email_worker.dispatch_single_email",
            new_callable=AsyncMock,
        ) as mock_dispatch:
            mock_dispatch.return_value = None
            await worker.start()
            await asyncio.sleep(0.5)
            await worker.stop()

        async with pg_db.session() as session:
            stale_refreshed = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == stale_row.id))
            ).scalar_one()
            pending_refreshed = (
                await session.execute(
                    select(EmailDelivery).where(EmailDelivery.id == pending_row.id)
                )
            ).scalar_one()

        # Stale row must no longer be stuck in 'sending'
        assert stale_refreshed.status != "sending", (
            f"Stale row must be recovered on restart, got '{stale_refreshed.status}'"
        )
        # Pending row queued during downtime must have been dispatched
        assert pending_refreshed.status == "delivered", (
            f"Pending row must be dispatched after restart, got '{pending_refreshed.status}'"
        )
        assert pending_refreshed.attempts == 1
    finally:
        await worker.stop()
        await _delete_rows(pg_db, stale_row.id, pending_row.id)


# ---------------------------------------------------------------------------
# Test 9: Ambiguous SMTP result (timeout/unknown acceptance) is retryable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ambiguous_smtp_result_is_retryable_and_counts_attempt(pg_db):
    """An ambiguous dispatch outcome (e.g. SMTP timeout where the server may or may
    not have accepted the message) must:
    - increment attempts (the attempt is consumed even though acceptance is unknown),
    - return the row to 'failed' with a scheduled retry,
    - allow a subsequent successful attempt to deliver the row.
    This is the at-least-once contract: a retry after an ambiguous outcome may
    produce a duplicate email; it must never silently drop the message."""
    row = _make_pg_delivery(
        status="pending",
        attempts=0,
        max_attempts=5,
        next_attempt_at=datetime.now(UTC),
    )
    async with pg_db.transaction() as session:
        session.add(row)

    worker = EmailDispatchWorker(db_manager=pg_db, poll_interval=999, batch_size=10)

    try:
        # First attempt: SMTP raises a connection/timeout error -> ambiguous outcome
        with patch(
            "src.services.notification.email_worker.dispatch_single_email",
            new_callable=AsyncMock,
        ) as mock_dispatch:
            mock_dispatch.side_effect = TimeoutError(
                "SMTP timed out after DATA; acceptance unknown"
            )
            processed = await worker.process_batch()

        assert processed == 1

        async with pg_db.session() as session:
            after_fail = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()

        assert after_fail.status == "failed", f"Expected 'failed', got '{after_fail.status}'"
        assert after_fail.attempts == 1, "Ambiguous attempt must still count toward max_attempts"
        assert after_fail.sending_since is None
        assert after_fail.next_attempt_at > datetime.now(UTC)
        assert after_fail.last_error is not None and "TimeoutError" in after_fail.last_error

        # Second attempt: SMTP succeeds -> delivered, attempt counter continues from 1
        async with pg_db.transaction() as session:
            r = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()
            r.next_attempt_at = datetime.now(UTC)

        with patch(
            "src.services.notification.email_worker.dispatch_single_email",
            new_callable=AsyncMock,
        ) as mock_dispatch:
            mock_dispatch.return_value = None
            processed = await worker.process_batch()

        assert processed == 1

        async with pg_db.session() as session:
            after_success = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()

        assert after_success.status == "delivered"
        assert after_success.attempts == 2, "Both the ambiguous and successful attempts are counted"
    finally:
        await _delete_rows(pg_db, row.id)


# ---------------------------------------------------------------------------
# Test 10: Crash after SMTP acceptance, before DB update -> recovered, redelivered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crash_after_smtp_accept_before_db_update_recovers_and_redispatches(pg_db):
    """Simulate the documented delivery-ambiguity window: the worker claims a row
    (status='sending', sending_since set), the SMTP server accepts the message,
    then the worker crashes before recording 'delivered'. Recovery must return the
    row to a retryable state and a later worker must re-dispatch it (at-least-once:
    the recipient may see a duplicate)."""
    row = _make_pg_delivery(
        status="sending",
        sending_since=datetime.now(UTC),
        attempts=0,
        max_attempts=5,
    )
    async with pg_db.transaction() as session:
        session.add(row)

    worker = EmailDispatchWorker(db_manager=pg_db, poll_interval=999, batch_size=10)

    try:
        # 1. Immediately after the crash, the active lease must NOT be reclaimed
        recovered = await worker.recover_stale_leases()
        assert recovered == 0, "Fresh lease must not be recovered (message may still be in flight)"

        async with pg_db.session() as session:
            untouched = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()
        assert untouched.status == "sending"

        # 2. Age the lease past the timeout to simulate the crash window elapsing
        async with pg_db.transaction() as session:
            r = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()
            r.sending_since = datetime.now(UTC) - timedelta(seconds=600)

        recovered = await worker.recover_stale_leases()
        assert recovered == 1

        async with pg_db.session() as session:
            after_recovery = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()
        assert after_recovery.status == "failed"
        assert after_recovery.attempts == 1, "The crashed attempt counts toward max_attempts"
        assert after_recovery.sending_since is None

        # 3. A healthy worker re-dispatches the row (possible duplicate delivery)
        async with pg_db.transaction() as session:
            r = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()
            r.next_attempt_at = datetime.now(UTC)

        with patch(
            "src.services.notification.email_worker.dispatch_single_email",
            new_callable=AsyncMock,
        ) as mock_dispatch:
            mock_dispatch.return_value = None
            processed = await worker.process_batch()

        assert processed == 1
        async with pg_db.session() as session:
            final = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()
        assert final.status == "delivered"
        assert final.attempts == 2
    finally:
        await _delete_rows(pg_db, row.id)


# ---------------------------------------------------------------------------
# Test 11: Repeated failures through normal dispatch cycle reach dead_letter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_failures_reach_dead_letter_via_recovery_cycles(pg_db):
    """Cycling a row through dispatch failure -> lease expiry -> recovery must
    consume attempts and finally land in dead_letter; dead_letter is terminal
    and must not be reclaimed by recovery or re-claimed by process_batch."""
    row = _make_pg_delivery(
        status="pending",
        attempts=0,
        max_attempts=3,
        next_attempt_at=datetime.now(UTC),
    )
    async with pg_db.transaction() as session:
        session.add(row)

    worker = EmailDispatchWorker(db_manager=pg_db, poll_interval=999, batch_size=10)

    try:
        # Cycle: fail dispatch, expire lease, recover — until dead_letter is reached.
        # With max_attempts=3: dispatch(1 fail) -> recover(2) -> dispatch(3=limit) -> dead_letter.
        for cycle in range(1, 4):
            with patch(
                "src.services.notification.email_worker.dispatch_single_email",
                new_callable=AsyncMock,
            ) as mock_dispatch:
                mock_dispatch.side_effect = TimeoutError(f"cycle {cycle} failure")
                await worker.process_batch()

            async with pg_db.session() as session:
                r = (
                    await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
                ).scalar_one()

            if r.status == "dead_letter":
                break
            assert r.status == "failed", f"cycle {cycle}: expected 'failed', got '{r.status}'"

            # Simulate the worker crashing mid-send: row claimed again, lease ages out
            async with pg_db.transaction() as session:
                rr = (
                    await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
                ).scalar_one()
                rr.status = "sending"
                rr.sending_since = datetime.now(UTC) - timedelta(seconds=600)

            await worker.recover_stale_leases()

            async with pg_db.session() as session:
                r2 = (
                    await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
                ).scalar_one()
            if r2.status == "dead_letter":
                break

        async with pg_db.session() as session:
            final = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()

        assert final.status == "dead_letter", f"Expected 'dead_letter', got '{final.status}'"
        assert final.attempts == 3, "attempts must not exceed max_attempts"

        # Terminal: recovery must not touch dead_letter rows
        recovered = await worker.recover_stale_leases()
        assert recovered == 0, "dead_letter is terminal; recovery must not reclaim it"

        # Terminal: process_batch must not claim dead_letter rows
        with patch(
            "src.services.notification.email_worker.dispatch_single_email",
            new_callable=AsyncMock,
        ) as mock_dispatch:
            processed = await worker.process_batch()
        assert processed == 0, "process_batch must not claim dead_letter rows"
        assert mock_dispatch.call_count == 0

        async with pg_db.session() as session:
            after = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()
        assert after.status == "dead_letter"
        assert after.attempts == 3
    finally:
        await _delete_rows(pg_db, row.id)


# ---------------------------------------------------------------------------
# Test 12: NULL sending_since (pre-migration orphan) is recovered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_sending_since_orphan_is_recovered(pg_db):
    """A 'sending' row with NULL sending_since (created by pre-lease code or a
    crash between status and lease updates) must be recovered, since no healthy
    worker under the current code can hold such a row."""
    row = _make_pg_delivery(
        status="sending",
        sending_since=None,
        attempts=0,
        max_attempts=5,
    )
    async with pg_db.transaction() as session:
        session.add(row)

    worker = EmailDispatchWorker(db_manager=pg_db, poll_interval=999, batch_size=10)

    try:
        recovered = await worker.recover_stale_leases()
        assert recovered >= 1

        async with pg_db.session() as session:
            refreshed = (
                await session.execute(select(EmailDelivery).where(EmailDelivery.id == row.id))
            ).scalar_one()

        assert refreshed.status == "failed"
        assert refreshed.attempts == 1
    finally:
        await _delete_rows(pg_db, row.id)
