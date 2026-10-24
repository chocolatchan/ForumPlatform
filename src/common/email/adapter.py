"""Shared email adapter for Forum Platform.

Provides an asynchronous, pluggable interface for sending email messages. The
concrete SMTP adapter is the only transport boundary used by the User and
Notification services.

Routing policy is intentionally enforced here, before any SMTP connection is
opened:

* development/test configuration is pinned to Mailpit and accepts every
  application recipient, preserving the original ``To`` metadata in Mailpit;
* production delivers to ordinary external domains but suppresses the exact
  ``test.dev`` domain and its subdomains;
* suppressed production messages raise a dedicated error and are never reported
  as delivered.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage as StdEmailMessage
from typing import Protocol, runtime_checkable

from src.common.email.config import (
    EmailConfigurationError,
    validate_email_configuration,
)
from src.common.email.models import EmailMessage
from src.common.email.policy import (
    DEVELOPMENT_ENVIRONMENTS,
    PRODUCTION_ENVIRONMENTS,
    find_suppressed_recipients,
    normalize_environment,
    suppressed_domains,
)

logger = logging.getLogger(__name__)


class EmailAdapterError(RuntimeError):
    """Base class for all adapter-level errors.

    Sub-classes and instances may expose additional context such as a rejected
    recipient or a policy suppression.
    """

    def __init__(
        self,
        message: str,
        *,
        recipient: str | None = None,
        is_recipient_rejected: bool = False,
        is_suppressed: bool = False,
        suppressed_recipients: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.recipient = recipient
        self.is_recipient_rejected = is_recipient_rejected
        self.is_suppressed = is_suppressed
        self.suppressed_recipients = suppressed_recipients


class EmailSuppressedError(EmailAdapterError):
    """Raised when production recipient-domain policy suppresses a message."""

    def __init__(self, *, recipients: tuple[str, ...], domains: tuple[str, ...]) -> None:
        recipient_list = ", ".join(recipients)
        domain_list = ", ".join(domains)
        super().__init__(
            "Email suppressed by production recipient-domain policy "
            f"(domains: {domain_list}; recipients: {recipient_list}).",
            recipient=recipients[0] if recipients else None,
            is_suppressed=True,
            suppressed_recipients=recipients,
        )
        self.domains = domains


@runtime_checkable
class EmailAdapter(Protocol):
    """Asynchronous email-sending contract.

    Implementations must provide an ``async send(message)`` method that raises
    :class:`EmailAdapterError` on any delivery problem.
    """

    async def send(self, message: EmailMessage) -> None:  # pragma: no cover - protocol definition
        ...


@dataclass(frozen=True, slots=True)
class _SmtpConfig:
    environment: str
    environment_explicit: bool
    backend: str
    host: str
    port: int
    user: str
    password: str
    use_tls: bool
    starttls: bool
    sender: str

    @staticmethod
    def from_user_settings() -> _SmtpConfig:
        from src.services.user.config import user_settings

        return _SmtpConfig(
            environment=user_settings.environment,
            environment_explicit="environment" in user_settings.model_fields_set,
            backend=user_settings.email_delivery_backend,
            host=user_settings.smtp_host,
            port=user_settings.smtp_port,
            user=user_settings.smtp_user,
            password=user_settings.smtp_password,
            use_tls=user_settings.smtp_use_tls,
            starttls=user_settings.smtp_starttls,
            sender=user_settings.email_from,
        )

    @staticmethod
    def from_notification_settings() -> _SmtpConfig:
        from src.services.notification.config import notification_settings

        return _SmtpConfig(
            environment=notification_settings.environment,
            environment_explicit="environment" in notification_settings.model_fields_set,
            backend=notification_settings.email_delivery_backend,
            host=notification_settings.smtp_host,
            port=notification_settings.smtp_port,
            user=notification_settings.smtp_user,
            password=notification_settings.smtp_password,
            use_tls=notification_settings.smtp_use_tls,
            starttls=notification_settings.smtp_starttls,
            sender=notification_settings.email_from,
        )


class SmtpEmailAdapter:
    """Concrete SMTP adapter used by both User and Notification services.

    Configuration is validated at initialization. Development/test delivery can
    only target Mailpit, while production suppression is applied per recipient
    before the standard-library SMTP client is constructed.
    """

    def __init__(self, cfg: _SmtpConfig) -> None:
        self._cfg = cfg
        try:
            validate_email_configuration(
                environment=cfg.environment,
                environment_explicit=cfg.environment_explicit,
                backend=cfg.backend,
                smtp_host=cfg.host,
                smtp_port=cfg.port,
                smtp_use_tls=cfg.use_tls,
                smtp_starttls=cfg.starttls,
                smtp_user=cfg.user,
                smtp_password=cfg.password,
            )
        except EmailConfigurationError as exc:
            # Fail closed at adapter initialisation; the caller must fix the
            # configuration rather than falling back to another SMTP endpoint.
            raise EmailAdapterError(str(exc), is_recipient_rejected=False) from exc

        if cfg.backend.strip().lower() != "smtp":
            raise EmailAdapterError(
                "SmtpEmailAdapter requires EMAIL_DELIVERY_BACKEND=smtp.",
                is_recipient_rejected=False,
            )

    async def send(self, message: EmailMessage) -> None:
        recipients = list(message.recipients)
        if not recipients:
            raise EmailAdapterError(
                "At least one recipient is required.", is_recipient_rejected=True
            )

        environment = normalize_environment(self._cfg.environment)

        # Production suppression must happen before the SMTP transport is
        # invoked. There is deliberately no redirect to another external address.
        if environment in PRODUCTION_ENVIRONMENTS:
            suppressed = find_suppressed_recipients(recipients)
            if suppressed:
                domains = suppressed_domains(suppressed)
                logger.warning(
                    "Email suppressed by production recipient-domain policy: "
                    "domains=%s recipients=%s",
                    domains,
                    suppressed,
                )
                raise EmailSuppressedError(recipients=suppressed, domains=domains)

        # Build a standard library EmailMessage - only plain-text is mandatory.
        std_msg = StdEmailMessage()
        std_msg["Subject"] = message.subject
        std_msg["From"] = self._cfg.sender
        std_msg["To"] = ", ".join(recipients)
        if environment in DEVELOPMENT_ENVIRONMENTS:
            # Mailpit receives the original recipient addresses, preserving the
            # metadata needed by workflow assertions. These headers make the
            # routing decision explicit without changing envelope delivery.
            std_msg["X-Forum-Routing-Environment"] = environment
            std_msg["X-Forum-Original-Recipients"] = ", ".join(recipients)
        std_msg.set_content(message.body_text)
        if message.body_html:
            std_msg.add_alternative(message.body_html, subtype="html")

        # Delegate the blocking I/O to a thread.
        await asyncio.to_thread(self._send_sync, std_msg, recipients)

    def _send_sync(self, msg: StdEmailMessage, recipients: list[str]) -> None:
        host, port = self._cfg.host, self._cfg.port
        try:
            if self._cfg.use_tls:
                client = smtplib.SMTP_SSL(host=host, port=port, timeout=10)
            else:
                client = smtplib.SMTP(host=host, port=port, timeout=10)
            with client as smtp:
                if not self._cfg.use_tls and self._cfg.starttls:
                    smtp.starttls()
                if self._cfg.user and self._cfg.password:
                    smtp.login(self._cfg.user, self._cfg.password)
                refused = smtp.send_message(msg)
                if refused:
                    raise smtplib.SMTPRecipientsRefused(refused)
        except smtplib.SMTPRecipientsRefused as e:
            logger.warning("SMTP recipient refused: %s", recipients)
            raise EmailAdapterError(
                f"Recipient refused by SMTP server: {recipients}",
                recipient=recipients[0] if recipients else None,
                is_recipient_rejected=True,
            ) from e
        except smtplib.SMTPResponseException as e:
            logger.warning("SMTP server error %s for %s", e.smtp_code, recipients)
            raise EmailAdapterError(
                f"SMTP error ({e.smtp_code}): {e.smtp_error}",
                recipient=recipients[0] if recipients else None,
                is_recipient_rejected=(e.smtp_code == 550),
            ) from e
        except Exception as e:
            logger.error("SMTP connection failure to %s:%s - %s", host, port, type(e).__name__)
            raise EmailAdapterError(
                f"SMTP connection failed: {type(e).__name__}",
                recipient=recipients[0] if recipients else None,
            ) from e


def get_user_email_adapter() -> EmailAdapter:
    """Factory returning a configured adapter for the User service.

    The function validates configuration at call-time and raises
    :class:`EmailAdapterError` if required settings are missing or invalid.
    """

    cfg = _SmtpConfig.from_user_settings()
    return SmtpEmailAdapter(cfg)


def get_notification_email_adapter() -> EmailAdapter:
    """Factory returning a configured adapter for the Notification service."""

    cfg = _SmtpConfig.from_notification_settings()
    return SmtpEmailAdapter(cfg)
