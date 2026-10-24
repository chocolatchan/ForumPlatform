"""OTP delivery mailbox, shared-adapter client, and testing sink.

Requirements per FR-2, Task 006.5, and Task 031.3.H:
- Delivery abstraction suitable for development and integration testing.
- Development/test SMTP delivery is routed only to Mailpit by the shared adapter.
- Production recipient suppression is enforced by the shared adapter before SMTP.
- Safe failure handling: failed delivery must not leave a usable pending OTP in the database.
- Anti-enumeration: Public API returns neutral 202 Accepted.
- Never log plaintext OTPs or email bodies.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from src.common.email import EmailAdapterError, EmailMessage, get_user_email_adapter
from src.services.user.config import user_settings

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when email delivery fails via SMTP or recipient policy."""

    def __init__(
        self,
        message: str,
        recipient: str,
        is_recipient_rejected: bool = False,
        is_suppressed: bool = False,
    ) -> None:
        super().__init__(message)
        self.recipient = recipient
        self.is_recipient_rejected = is_recipient_rejected
        self.is_suppressed = is_suppressed


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


async def deliver_otp(
    recipient: str,
    code: str,
    purpose: str,
    timestamp: datetime,
) -> None:
    """Deliver an OTP via the configured delivery backend.

    Backend options:
    - 'memory': Delivers directly to `otp_delivery_sink` for fast unit tests.
    - 'smtp': Sends through the shared adapter, which owns environment routing
      and production recipient-domain suppression.

    Raises:
        EmailDeliveryError: If the shared adapter rejects, suppresses, or fails.
    """
    recipient_clean = recipient.strip().lower()

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

    try:
        adapter = get_user_email_adapter()
        await adapter.send(
            EmailMessage(
                subject=subject,
                body_text=body,
                sender=user_settings.email_from,
                recipients=[recipient_clean],
            )
        )
    except EmailAdapterError as exc:
        # Preserve the User service's existing public error type and flags.
        raise EmailDeliveryError(
            str(exc),
            recipient=exc.recipient or recipient_clean,
            is_recipient_rejected=exc.is_recipient_rejected,
            is_suppressed=exc.is_suppressed,
        ) from exc

    # Also record to sink for inspection if needed
    otp_delivery_sink.deliver(
        recipient=recipient_clean,
        code=code,
        purpose=purpose,
        timestamp=timestamp,
    )
