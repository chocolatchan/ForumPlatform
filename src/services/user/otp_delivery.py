"""Development OTP delivery mailbox, SMTP delivery client, and notification sink.

Requirements per FR-2 and Task 006.5:
- Delivery abstraction suitable for development and integration testing.
- SMTP catcher integration (Mailpit) in Docker Compose.
- Strict recipient allowlist enforcement:
  - Empty or unconfigured allowlist must fail-closed (reject all deliveries).
  - Unlisted or partially matched addresses must fail delivery explicitly.
- Safe failure handling: failed delivery must not leave a usable pending OTP in the database.
- Anti-enumeration: Public API returns neutral 202 Accepted.
- Never log plaintext OTPs or email bodies.
"""

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage

from src.services.user.config import user_settings

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when email delivery fails via SMTP or allowlist check."""

    def __init__(self, message: str, recipient: str, is_recipient_rejected: bool = False) -> None:
        super().__init__(message)
        self.recipient = recipient
        self.is_recipient_rejected = is_recipient_rejected


@dataclass(frozen=True)
class SentOtpMessage:
    recipient: str
    code: str
    purpose: str
    timestamp: datetime


class OtpDeliverySink:
    """In-memory testing sink for OTP delivery."""

    def __init__(self) -> None:
        self._messages: list[SentOtpMessage] = []

    def deliver(self, recipient: str, code: str, purpose: str, timestamp: datetime) -> None:
        """Deliver OTP to the testing mailbox."""
        self._messages.append(
            SentOtpMessage(recipient=recipient, code=code, purpose=purpose, timestamp=timestamp)
        )

    def get_latest_otp(self, recipient: str, purpose: str | None = None) -> str | None:
        """Retrieve the most recent OTP delivered to the recipient (test-only helper)."""
        recipient_normalized = recipient.lower().strip()
        for msg in reversed(self._messages):
            if msg.recipient.lower().strip() == recipient_normalized and (
                purpose is None or msg.purpose == purpose
            ):
                return msg.code
        return None

    def clear(self) -> None:
        """Clear the mailbox."""
        self._messages.clear()


otp_delivery_sink = OtpDeliverySink()


def _send_smtp_email_sync(
    recipient: str,
    subject: str,
    body: str,
    from_addr: str,
    host: str,
    port: int,
    user: str = "",
    password: str = "",
) -> None:
    """Synchronous SMTP email sending execution run inside an async executor.

    Strictly avoids logging plaintext code or message body.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = recipient
    msg.set_content(body)

    try:
        with smtplib.SMTP(host=host, port=port, timeout=10) as client:
            if user and password:
                client.login(user, password)
            refused = client.send_message(msg)
            if refused:
                # One or more recipients were refused by the server (e.g. 550)
                raise smtplib.SMTPRecipientsRefused(refused)
    except smtplib.SMTPRecipientsRefused as e:
        logger.warning(
            "SMTP recipient refused by mail server: recipient=%s",
            recipient,
        )
        raise EmailDeliveryError(
            f"Recipient refused by SMTP server: {recipient}",
            recipient=recipient,
            is_recipient_rejected=True,
        ) from e
    except smtplib.SMTPResponseException as e:
        logger.warning(
            "SMTP server returned error: code=%s recipient=%s",
            e.smtp_code,
            recipient,
        )
        raise EmailDeliveryError(
            f"SMTP error ({e.smtp_code}): {e.smtp_error}",
            recipient=recipient,
            is_recipient_rejected=(e.smtp_code == 550),
        ) from e
    except Exception as e:
        logger.error(
            "SMTP delivery connection error to %s:%s for recipient=%s: %s",
            host,
            port,
            recipient,
            type(e).__name__,
        )
        raise EmailDeliveryError(
            f"SMTP connection failed: {type(e).__name__}",
            recipient=recipient,
            is_recipient_rejected=False,
        ) from e


async def deliver_otp(
    recipient: str,
    code: str,
    purpose: str,
    timestamp: datetime,
) -> None:
    """Deliver an OTP via the configured delivery backend.

    Backend options:
    - 'memory': Delivers directly to `otp_delivery_sink` for fast unit tests.
    - 'smtp': Enforces recipient allowlist (fail-closed) and sends to SMTP catcher.

    Raises:
        EmailDeliveryError: If allowlist check fails or SMTP server rejects/fails.
    """
    recipient_clean = recipient.strip().lower()

    # Pre-flight check: Developer recipient allowlist (fail-closed)
    allowed_recipients = user_settings.get_allowed_recipients()
    if recipient_clean not in allowed_recipients:
        logger.warning(
            "Email delivery rejected by allowlist: recipient=%s (allowed count=%d)",
            recipient_clean,
            len(allowed_recipients),
        )
        raise EmailDeliveryError(
            f"Recipient '{recipient_clean}' is not in the configured development email allowlist.",
            recipient=recipient_clean,
            is_recipient_rejected=True,
        )

    # In memory mode (used by unit tests)
    if user_settings.email_delivery_backend == "memory":
        otp_delivery_sink.deliver(
            recipient=recipient_clean,
            code=code,
            purpose=purpose,
            timestamp=timestamp,
        )
        return

    # SMTP mode: compose message and send via Mailpit
    subject = f"Your Forum Verification Code [{purpose.capitalize()}]"
    body = (
        f"Hello,\n\n"
        f"Your verification code is: {code}\n\n"
        f"This code will expire in 10 minutes. If you did not request this code, "
        f"please disregard this message.\n\n"
        f"--\nForum Platform Team\n"
    )

    await asyncio.to_thread(
        _send_smtp_email_sync,
        recipient=recipient_clean,
        subject=subject,
        body=body,
        from_addr=user_settings.email_from,
        host=user_settings.smtp_host,
        port=user_settings.smtp_port,
        user=user_settings.smtp_user,
        password=user_settings.smtp_password,
    )

    # Also record to sink for inspection if needed
    otp_delivery_sink.deliver(
        recipient=recipient_clean,
        code=code,
        purpose=purpose,
        timestamp=timestamp,
    )
