"""Background Email Dispatch Worker and SMTP Client for Notification Service.

Ensures at-least-once, resilient outbound delivery of queued domain event emails:
- Bounded concurrency with PostgreSQL SELECT FOR UPDATE SKIP LOCKED
- Dispatches via SMTP or in-memory sink
- Exponential backoff with randomized jitter
- Max attempt threshold before moving to dead_letter status
- Recipient allowlist enforcement (fail-closed in dev/test)
- Graceful shutdown handling
"""

import asyncio
import contextlib
import html
import logging
import random
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database import DatabaseSessionManager
from src.services.notification.config import notification_settings
from src.services.notification.models import EmailDelivery

logger = logging.getLogger("notification_email_worker")


class EmailWorkerDeliveryError(Exception):
    """Raised when email delivery fails via SMTP or allowlist check."""

    def __init__(self, message: str, recipient: str, is_recipient_rejected: bool = False) -> None:
        super().__init__(message)
        self.recipient = recipient
        self.is_recipient_rejected = is_recipient_rejected


@dataclass(frozen=True)
class SentNotificationEmail:
    recipient: str
    subject: str
    body_text: str
    body_html: str | None
    category: str
    timestamp: datetime


class NotificationEmailSink:
    """In-memory testing sink for Notification Service email deliveries."""

    def __init__(self) -> None:
        self._messages: list[SentNotificationEmail] = []

    def deliver(
        self,
        recipient: str,
        subject: str,
        body_text: str,
        body_html: str | None,
        category: str,
        timestamp: datetime,
    ) -> None:
        self._messages.append(
            SentNotificationEmail(
                recipient=recipient,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                category=category,
                timestamp=timestamp,
            )
        )

    def get_messages(self, recipient: str | None = None) -> list[SentNotificationEmail]:
        if recipient is None:
            return list(self._messages)
        norm = recipient.lower().strip()
        return [m for m in self._messages if m.recipient.lower().strip() == norm]

    def clear(self) -> None:
        self._messages.clear()


notification_email_sink = NotificationEmailSink()


def _send_smtp_email_sync(
    recipient: str,
    subject: str,
    body_text: str,
    body_html: str | None,
    from_addr: str,
    host: str,
    port: int,
    user: str = "",
    password: str = "",
    use_tls: bool = False,
    starttls: bool = False,
) -> None:
    """Synchronous SMTP email dispatch executed in an async executor thread."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = recipient
    msg.set_content(body_text)

    if body_html:
        msg.add_alternative(body_html, subtype="html")

    try:
        if use_tls:
            with smtplib.SMTP_SSL(host=host, port=port, timeout=10) as client:
                if user and password:
                    client.login(user, password)
                refused = client.send_message(msg)
                if refused:
                    raise smtplib.SMTPRecipientsRefused(refused)
        else:
            with smtplib.SMTP(host=host, port=port, timeout=10) as client:
                if starttls:
                    client.starttls()
                if user and password:
                    client.login(user, password)
                refused = client.send_message(msg)
                if refused:
                    raise smtplib.SMTPRecipientsRefused(refused)
    except smtplib.SMTPRecipientsRefused as e:
        logger.warning("SMTP recipient refused: recipient=%s", recipient)
        raise EmailWorkerDeliveryError(
            f"Recipient refused by SMTP server: {recipient}",
            recipient=recipient,
            is_recipient_rejected=True,
        ) from e
    except smtplib.SMTPResponseException as e:
        logger.warning("SMTP server error: code=%s recipient=%s", e.smtp_code, recipient)
        raise EmailWorkerDeliveryError(
            f"SMTP error ({e.smtp_code}): {e.smtp_error}",
            recipient=recipient,
            is_recipient_rejected=(e.smtp_code == 550),
        ) from e
    except Exception as e:
        logger.error(
            "SMTP connection error to %s:%s for recipient=%s: %s",
            host,
            port,
            recipient,
            type(e).__name__,
        )
        raise EmailWorkerDeliveryError(
            f"SMTP connection failed: {type(e).__name__}",
            recipient=recipient,
            is_recipient_rejected=False,
        ) from e


async def dispatch_single_email(delivery: EmailDelivery) -> None:
    """Send single email via configured backend (smtp or memory)."""
    recipient = delivery.recipient_email

    # Enforce allowlist in dev/test/staging
    if not notification_settings.is_recipient_allowed(recipient):
        logger.warning(
            "Email rejected: recipient '%s' is not in configured allowlist.",
            recipient,
        )
        raise EmailWorkerDeliveryError(
            f"Recipient '{recipient}' not permitted by allowlist.",
            recipient=recipient,
            is_recipient_rejected=True,
        )

    backend = notification_settings.email_delivery_backend
    if backend == "memory":
        notification_email_sink.deliver(
            recipient=recipient,
            subject=delivery.subject,
            body_text=delivery.body_text,
            body_html=delivery.body_html,
            category=delivery.category,
            timestamp=datetime.now(UTC),
        )
        return

    if backend == "smtp":
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            _send_smtp_email_sync,
            recipient,
            delivery.subject,
            delivery.body_text,
            delivery.body_html,
            notification_settings.email_from,
            notification_settings.smtp_host,
            notification_settings.smtp_port,
            notification_settings.smtp_user,
            notification_settings.smtp_password,
            notification_settings.smtp_use_tls,
            notification_settings.smtp_starttls,
        )
        return

    raise ValueError(f"Unknown email delivery backend: '{backend}'")


def calculate_email_backoff(attempt: int, base_delay: float = 2.0, max_delay: float = 60.0) -> float:
    """Calculate exponential backoff with randomized jitter in [0.5, 1.5]."""
    delay = min(max_delay, base_delay * (2 ** max(0, attempt - 1)))
    jitter = random.uniform(0.5, 1.5)
    return min(max_delay, delay * jitter)


class EmailDispatchWorker:
    """Autonomous polling worker that claims pending email deliveries and dispatches them."""

    def __init__(
        self,
        db_manager: DatabaseSessionManager,
        poll_interval: float | None = None,
        batch_size: int | None = None,
    ) -> None:
        self.db = db_manager
        self.poll_interval = (
            poll_interval
            if poll_interval is not None
            else notification_settings.email_worker_poll_interval
        )
        self.batch_size = (
            batch_size
            if batch_size is not None
            else notification_settings.email_worker_batch_size
        )
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background email dispatch polling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="EmailDispatchWorker")
        logger.info("Notification EmailDispatchWorker started.")

    async def stop(self) -> None:
        """Gracefully stop the background worker."""
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("Notification EmailDispatchWorker stopped.")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                processed = await self.process_batch()
                if processed == 0:
                    await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Unexpected error in EmailDispatchWorker loop: %s", e, exc_info=True)
                await asyncio.sleep(self.poll_interval)

    async def process_batch(self) -> int:
        """Claim a batch of pending/failed emails and attempt delivery.

        Uses SELECT FOR UPDATE SKIP LOCKED to prevent duplicate concurrent delivery.
        Remote SMTP dispatch is executed outside of the DB lock window.
        """
        now = datetime.now(UTC)
        claimed_ids: list[tuple[Any, EmailDelivery]] = []

        async with self.db.transaction() as session:
            # Query candidate rows for dispatch
            stmt = (
                select(EmailDelivery)
                .where(
                    EmailDelivery.status.in_(["pending", "failed"]),
                    EmailDelivery.next_attempt_at <= now,
                    EmailDelivery.attempts < EmailDelivery.max_attempts,
                )
                .order_by(EmailDelivery.next_attempt_at.asc())
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(stmt)
            deliveries = list(result.scalars().all())

            for d in deliveries:
                d.status = "sending"
                claimed_ids.append((d.id, d))

        if not claimed_ids:
            return 0

        # Remote execution outside of lock
        for delivery_id, delivery in claimed_ids:
            try:
                await dispatch_single_email(delivery)
                success = True
                err_msg = None
                rejected = False
            except EmailWorkerDeliveryError as e:
                success = False
                err_msg = str(e)
                rejected = e.is_recipient_rejected
            except Exception as e:
                success = False
                err_msg = f"{type(e).__name__}: {str(e)}"
                rejected = False

            # Update delivery status
            async with self.db.transaction() as session:
                d_stmt = select(EmailDelivery).where(EmailDelivery.id == delivery_id)
                d_row = (await session.execute(d_stmt)).scalar_one_or_none()
                if not d_row:
                    continue

                d_row.attempts += 1
                curr_now = datetime.now(UTC)

                if success:
                    d_row.status = "delivered"
                    d_row.delivered_at = curr_now
                    d_row.last_error = None
                else:
                    d_row.last_error = err_msg
                    if rejected or d_row.attempts >= d_row.max_attempts:
                        d_row.status = "dead_letter"
                        logger.error(
                            "Email delivery %s moved to dead_letter: attempts=%d, error=%s",
                            delivery_id,
                            d_row.attempts,
                            err_msg,
                        )
                    else:
                        d_row.status = "failed"
                        backoff = calculate_email_backoff(d_row.attempts)
                        d_row.next_attempt_at = curr_now + timedelta(seconds=backoff)
                        logger.warning(
                            "Email delivery %s failed (attempt %d/%d). Retrying in %.1fs: %s",
                            delivery_id,
                            d_row.attempts,
                            d_row.max_attempts,
                            backoff,
                            err_msg,
                        )

        return len(claimed_ids)
