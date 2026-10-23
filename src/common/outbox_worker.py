"""Autonomous Transactional Outbox Event Delivery Worker.

Provides at-least-once, resilient background delivery of domain events from outbox tables
to internal event consumers across microservice boundaries.
Features:
- Bounded concurrency with PostgreSQL SELECT FOR UPDATE SKIP LOCKED
- Short database lock boundaries (remote HTTP call executed outside DB transaction)
- Exponential backoff with randomized jitter
- Max attempt threshold before moving to dead_letter status
- Structured observability (metrics, logs, pending counts)
- Clean graceful shutdown
"""

import asyncio
import contextlib
import logging
import random
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, or_, select

from src.common.database import DatabaseSessionManager
from src.common.internal_auth import INTERNAL_SERVICE_KEY_HEADER
from src.common.outbox import OutboxStatus

logger = logging.getLogger("outbox_worker")


@dataclass
class EventDestination:
    """Target internal consumer URL and service name."""

    service_name: str
    target_url: str


# Canonical routing matrix mapping event types to target consumer services
EVENT_ROUTING_TABLE: dict[str, list[str]] = {
    "ThreadCreated": ["search-service", "notification-service"],
    "ThreadUpdated": ["search-service"],
    "ThreadDeleted": ["search-service"],
    "ThreadLockToggled": ["search-service"],
    "ThreadPinToggled": ["search-service"],
    "CommentCreated": ["notification-service"],
    "BestAnswerChanged": ["notification-service"],
    "VoteChanged": ["notification-service"],
    "ThreadViewCountIncremented": ["content-service", "search-service"],
    "ModerationActionApplied": ["search-service", "notification-service"],
    "UserRegistered": ["notification-service"],
}

# Retryable HTTP status codes: 408 (Request Timeout) and 429 (Too Many Requests).
# 5xx server errors and network connection errors are also transient/retryable.
RETRYABLE_HTTP_STATUS_CODES: frozenset[int] = frozenset({408, 429})


def calculate_backoff(attempt: int, base_delay: float = 2.0, max_delay: float = 60.0) -> float:
    """Calculate exponential backoff with randomized jitter in [0.5, 1.5]."""
    delay = min(max_delay, base_delay * (2 ** max(0, attempt - 1)))
    jitter = random.uniform(0.5, 1.5)
    return min(max_delay, delay * jitter)


class OutboxWorker:
    """Autonomous polling worker that claims, delivers, and updates outbox events."""

    def __init__(
        self,
        service_name: str,
        db_manager: DatabaseSessionManager,
        model_class: Any,
        service_urls: dict[str, str],
        internal_key: str,
        batch_size: int = 20,
        poll_interval_seconds: float = 1.0,
        max_attempts: int = 5,
        base_backoff_seconds: float = 2.0,
        max_backoff_seconds: float = 60.0,
        lock_timeout_seconds: float = 60.0,
    ) -> None:
        self.service_name = service_name
        self.db = db_manager
        self.model = model_class
        self.service_urls = service_urls
        if not internal_key or not internal_key.strip():
            raise ValueError("internal_key must be a non-empty internal service credential.")
        self.internal_key = internal_key
        self.batch_size = batch_size
        self.poll_interval = poll_interval_seconds
        self.max_attempts = max_attempts
        self.base_backoff = base_backoff_seconds
        self.max_backoff = max_backoff_seconds
        self.lock_timeout = lock_timeout_seconds

        self.worker_id = f"{service_name}-{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self._running = False
        self._task: asyncio.Task | None = None
        self._http_client: httpx.AsyncClient | None = None

        # Observability metrics
        self.metrics = {
            "claimed_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "dead_letter_count": 0,
            "stale_reclaimed_count": 0,
            "last_delivery_latency_ms": 0.0,
        }

    async def get_queue_metrics(self) -> dict[str, Any]:
        """Query real-time database outbox queue breakdown and oldest event age."""
        now = datetime.now(UTC)
        counts = {
            OutboxStatus.PENDING: 0,
            OutboxStatus.PROCESSING: 0,
            OutboxStatus.DELIVERED: 0,
            OutboxStatus.FAILED: 0,
            OutboxStatus.DEAD_LETTER: 0,
        }
        oldest_age_seconds = 0.0

        try:
            async with self.db.session() as session:
                # 1. Group counts by status
                stmt = select(self.model.status, func.count(self.model.id)).group_by(
                    self.model.status
                )
                rows = (await session.execute(stmt)).all()
                for status_name, cnt in rows:
                    if status_name in counts:
                        counts[status_name] = cnt

                # 2. Oldest active/pending event age
                oldest_stmt = (
                    select(self.model.occurred_at)
                    .where(
                        self.model.status.in_(
                            [OutboxStatus.PENDING, OutboxStatus.FAILED, OutboxStatus.PROCESSING]
                        )
                    )
                    .order_by(self.model.occurred_at.asc())
                    .limit(1)
                )
                oldest_res = (await session.execute(oldest_stmt)).scalar_one_or_none()
                if oldest_res is not None:
                    if oldest_res.tzinfo is None:
                        oldest_res = oldest_res.replace(tzinfo=UTC)
                    oldest_age_seconds = max(0.0, (now - oldest_res).total_seconds())
        except Exception as exc:
            logger.debug("[%s] Failed to fetch outbox queue metrics: %s", self.service_name, exc)

        return {
            "status_counts": counts,
            "oldest_age_seconds": oldest_age_seconds,
            "worker_metrics": dict(self.metrics),
            "is_running": self._running,
        }

    async def start(self) -> None:
        """Start worker loop in background task."""
        if self._running or (self._task and not self._task.done()):
            logger.warning(
                "[%s] Worker already running; skipping duplicate start.", self.service_name
            )
            return
        self._running = True
        self._http_client = httpx.AsyncClient(
            timeout=10.0,
            headers={
                INTERNAL_SERVICE_KEY_HEADER: self.internal_key,
                "User-Agent": f"OutboxWorker/{self.service_name}",
            },
        )
        self._task = asyncio.create_task(
            self._run_loop(), name=f"outbox-worker-{self.service_name}"
        )
        logger.info(
            "[%s] Outbox delivery worker started (ID: %s)", self.service_name, self.worker_id
        )

    async def stop(self, drain_timeout_seconds: float = 3.0) -> None:
        """Gracefully stop worker loop, allowing in-flight batch delivery to finish."""
        self._running = False
        if self._task and not self._task.done():
            # Allow in-flight batch dispatch to finish within drain_timeout_seconds
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=drain_timeout_seconds)
            except TimeoutError:
                logger.warning(
                    "[%s] Worker task did not complete within %.1fs shutdown timeout; cancelling.",
                    self.service_name,
                    drain_timeout_seconds,
                )
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.error("[%s] Error during worker shutdown: %s", self.service_name, exc)

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        logger.info("[%s] Outbox delivery worker stopped cleanly.", self.service_name)

    async def _run_loop(self) -> None:
        """Continuous polling loop."""
        while self._running:
            try:
                delivered_count = await self.process_batch()
                if delivered_count == 0:
                    await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    "[%s] Unexpected error in outbox worker loop: %s",
                    self.service_name,
                    exc,
                    exc_info=True,
                )
                await asyncio.sleep(self.poll_interval)

    async def process_batch(self, event_id: uuid.UUID | None = None) -> int:
        """Poll, claim, deliver, and complete a batch of pending events.

        Returns the number of events claimed and processed.
        """
        now = datetime.now(UTC)
        lock_cutoff = now - timedelta(seconds=self.lock_timeout)

        # 1. Claim pending events using SELECT FOR UPDATE SKIP LOCKED
        claimed_events: list[dict[str, Any]] = []

        async with self.db.transaction() as session:
            # Reclaim stale processing events or fetch pending ready for retry
            filters = [
                or_(
                    self.model.status == OutboxStatus.PENDING,
                    (self.model.status == OutboxStatus.FAILED) & (self.model.next_retry_at <= now),
                    (self.model.status == OutboxStatus.PROCESSING)
                    & (self.model.locked_at < lock_cutoff),
                )
            ]
            if event_id is not None:
                filters.append(self.model.id == event_id)

            claim_query = (
                select(self.model)
                .where(*filters)
                .order_by(self.model.occurred_at.asc())
                .limit(self.batch_size)
            )

            # Use FOR UPDATE SKIP LOCKED if supported (PostgreSQL)
            is_postgres = "postgresql" in str(self.db.engine.url)
            if is_postgres:
                claim_query = claim_query.with_for_update(skip_locked=True)

            result = await session.execute(claim_query)
            rows = result.scalars().all()

            for event in rows:
                if event.status == OutboxStatus.PROCESSING:
                    self.metrics["stale_reclaimed_count"] += 1
                event.status = OutboxStatus.PROCESSING
                event.locked_at = now
                event.locked_by = self.worker_id
                claimed_events.append(
                    {
                        "id": event.id,
                        "event_type": event.event_type,
                        "aggregate_type": event.aggregate_type,
                        "aggregate_id": str(event.aggregate_id),
                        "payload": event.payload,
                        "occurred_at": event.occurred_at.isoformat()
                        if event.occurred_at
                        else now.isoformat(),
                        "attempts": event.attempts,
                    }
                )

        if not claimed_events:
            return 0

        self.metrics["claimed_count"] += len(claimed_events)

        # 2. Deliver claimed events outside DB transaction lock
        for event_data in claimed_events:
            await self._deliver_single_event(event_data)

        return len(claimed_events)

    async def _deliver_single_event(self, event_data: dict[str, Any]) -> None:
        """Deliver event to all registered target consumers and update state."""
        event_id = event_data["id"]
        event_type = event_data["event_type"]
        target_services = EVENT_ROUTING_TABLE.get(event_type, [])

        if not target_services:
            # No registered consumers for this event type -> mark delivered immediately
            await self._update_event_status(
                event_id, OutboxStatus.DELIVERED, None, expected_locked_by=self.worker_id
            )
            return

        all_success = True
        last_error = None
        last_status_code: int | None = None

        assert self._http_client is not None

        for target_svc in target_services:
            target_base_url = self.service_urls.get(target_svc)
            if not target_base_url:
                continue

            target_url = f"{target_base_url.rstrip('/')}/internal/events"
            outbox_payload = {
                "event_id": str(event_id),
                "event_type": event_type,
                "occurred_at": event_data["occurred_at"],
                "producer": self.service_name,
                "data": event_data["payload"],
            }

            try:
                t0 = time.perf_counter()
                resp = await self._http_client.post(target_url, json=outbox_payload)
                dt_ms = (time.perf_counter() - t0) * 1000.0

                if resp.status_code >= 400:
                    all_success = False
                    last_status_code = resp.status_code
                    last_error = f"HTTP {resp.status_code} from {target_svc}: {resp.text[:200]}"
                    logger.warning(
                        "[%s -> %s] Delivery of event %s failed (%s, %.1fms)",
                        self.service_name,
                        target_svc,
                        event_id,
                        last_error,
                        dt_ms,
                    )
                else:
                    self.metrics["last_delivery_latency_ms"] = dt_ms
                    logger.debug(
                        "[%s -> %s] Successfully delivered event %s in %.1fms",
                        self.service_name,
                        target_svc,
                        event_id,
                        dt_ms,
                    )
            except Exception as exc:
                all_success = False
                last_error = f"Connection error to {target_svc}: {str(exc)}"
                logger.warning(
                    "[%s -> %s] Network exception delivering event %s: %s",
                    self.service_name,
                    target_svc,
                    event_id,
                    exc,
                )

        # 3. Commit delivery status in a separate short transaction
        current_attempts = event_data["attempts"] + 1

        # Check if error is permanent:
        # Numeric HTTP status codes: 4xx except 408 (Request Timeout) and 429 (Too Many Requests).
        # 5xx and network exceptions are transient and retryable.
        is_permanent_error = (
            not all_success
            and last_status_code is not None
            and 400 <= last_status_code < 500
            and last_status_code not in RETRYABLE_HTTP_STATUS_CODES
        )

        if all_success:
            updated = await self._update_event_status(
                event_id,
                OutboxStatus.DELIVERED,
                None,
                attempts=current_attempts,
                expected_locked_by=self.worker_id,
            )
            if updated:
                self.metrics["success_count"] += 1
            else:
                logger.warning(
                    "[%s] Stale lease detected for event %s. State was updated by another worker.",
                    self.service_name,
                    event_id,
                )
        else:
            self.metrics["failure_count"] += 1
            if current_attempts >= self.max_attempts or is_permanent_error:
                # Permanent failure or max attempts reached -> DEAD_LETTER
                updated = await self._update_event_status(
                    event_id,
                    OutboxStatus.DEAD_LETTER,
                    last_error,
                    attempts=current_attempts,
                    expected_locked_by=self.worker_id,
                )
                if updated:
                    self.metrics["dead_letter_count"] += 1
                    logger.error(
                        "[%s] Event %s moved to DEAD_LETTER (attempts=%d, permanent=%s): %s",
                        self.service_name,
                        event_id,
                        current_attempts,
                        is_permanent_error,
                        last_error,
                    )
            else:
                # Calculate exponential backoff with jitter
                backoff_seconds = calculate_backoff(
                    current_attempts, base_delay=self.base_backoff, max_delay=self.max_backoff
                )
                next_retry = datetime.now(UTC) + timedelta(seconds=backoff_seconds)

                await self._update_event_status(
                    event_id,
                    OutboxStatus.FAILED,
                    last_error,
                    attempts=current_attempts,
                    next_retry_at=next_retry,
                    expected_locked_by=self.worker_id,
                )

    async def _update_event_status(
        self,
        event_id: uuid.UUID,
        new_status: str,
        last_error: str | None,
        attempts: int | None = None,
        next_retry_at: datetime | None = None,
        expected_locked_by: str | None = None,
    ) -> bool:
        """Update event outcome in database safely.

        If expected_locked_by is specified, the update only succeeds if the event is
        still owned by this worker (preventing stale lease overwrites).
        Returns True if the event was updated, False if lease was stolen/lost.
        """
        now = datetime.now(UTC)
        async with self.db.transaction() as session:
            stmt = select(self.model).where(self.model.id == event_id)
            event = (await session.execute(stmt)).scalar_one_or_none()
            if not event:
                return False

            # Never allow overwriting an already DELIVERED event
            if event.status == OutboxStatus.DELIVERED:
                logger.warning(
                    "[%s] Event %s is already DELIVERED; ignoring late update to %s",
                    self.service_name,
                    event_id,
                    new_status,
                )
                return False

            # Stale lease guard: if expected_locked_by is given, do not overwrite if another worker reclaimed it
            if expected_locked_by and event.locked_by != expected_locked_by:
                logger.warning(
                    "[%s] Lease conflict for event %s: current locked_by='%s' != expected='%s'",
                    self.service_name,
                    event_id,
                    event.locked_by,
                    expected_locked_by,
                )
                return False

            event.status = new_status
            event.last_error = last_error
            event.locked_at = None
            event.locked_by = None
            if attempts is not None:
                event.attempts = attempts
            if next_retry_at is not None:
                event.next_retry_at = next_retry_at
            elif new_status in (OutboxStatus.DELIVERED, OutboxStatus.DEAD_LETTER):
                event.next_retry_at = None
            if new_status == OutboxStatus.DELIVERED:
                event.processed_at = now

            return True
