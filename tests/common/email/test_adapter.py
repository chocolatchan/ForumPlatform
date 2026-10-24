"""Shared adapter routing, suppression, and transport tests for Task 031.3.H."""

from __future__ import annotations

import asyncio
import logging
from unittest import mock

import pytest

from src.common.email import (
    EmailAdapterError,
    EmailMessage,
    EmailSuppressedError,
    get_notification_email_adapter,
    get_user_email_adapter,
)


def make_message(recipient: str = "developer@example.test") -> EmailMessage:
    return EmailMessage(
        subject="Test",
        body_text="Hello",
        sender="noreply@forumplatform.local",
        recipients=[recipient],
        body_html=None,
    )


def _mock_smtp(monkeypatch: pytest.MonkeyPatch) -> tuple[mock.MagicMock, mock.MagicMock]:
    mock_smtp = mock.MagicMock()
    mock_smtp.send_message.return_value = {}
    mock_ctx = mock.MagicMock()
    mock_ctx.__enter__.return_value = mock_smtp
    constructor = mock.MagicMock(return_value=mock_ctx)
    monkeypatch.setattr("smtplib.SMTP", constructor)
    return constructor, mock_smtp


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch: pytest.MonkeyPatch):
    """Use valid, explicit non-production Mailpit settings for each test."""
    from src.services.notification import config as notif_cfg
    from src.services.user import config as user_cfg

    monkeypatch.setattr(user_cfg.user_settings, "environment", "test")
    monkeypatch.setattr(user_cfg.user_settings, "email_delivery_backend", "smtp")
    monkeypatch.setattr(user_cfg.user_settings, "smtp_host", "mailpit")
    monkeypatch.setattr(user_cfg.user_settings, "smtp_port", 1025)
    monkeypatch.setattr(user_cfg.user_settings, "smtp_user", "")
    monkeypatch.setattr(user_cfg.user_settings, "smtp_password", "")
    monkeypatch.setattr(user_cfg.user_settings, "smtp_use_tls", False)
    monkeypatch.setattr(user_cfg.user_settings, "smtp_starttls", False)
    monkeypatch.setattr(user_cfg.user_settings, "email_from", "noreply@forumplatform.local")

    monkeypatch.setattr(notif_cfg.notification_settings, "environment", "test")
    monkeypatch.setattr(notif_cfg.notification_settings, "email_delivery_backend", "smtp")
    monkeypatch.setattr(notif_cfg.notification_settings, "smtp_host", "mailpit")
    monkeypatch.setattr(notif_cfg.notification_settings, "smtp_port", 1025)
    monkeypatch.setattr(notif_cfg.notification_settings, "smtp_user", "")
    monkeypatch.setattr(notif_cfg.notification_settings, "smtp_password", "")
    monkeypatch.setattr(notif_cfg.notification_settings, "smtp_use_tls", False)
    monkeypatch.setattr(notif_cfg.notification_settings, "smtp_starttls", False)
    monkeypatch.setattr(
        notif_cfg.notification_settings, "email_from", "notifications@forumplatform.local"
    )
    yield


def _configure_user_as_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services.user import config as user_cfg

    monkeypatch.setattr(user_cfg.user_settings, "environment", "production")
    monkeypatch.setattr(user_cfg.user_settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(user_cfg.user_settings, "smtp_port", 587)
    monkeypatch.setattr(user_cfg.user_settings, "smtp_starttls", True)
    monkeypatch.setattr(user_cfg.user_settings, "smtp_user", "smtp-user")
    monkeypatch.setattr(user_cfg.user_settings, "smtp_password", "smtp-password")


def test_development_routes_every_recipient_to_mailpit(monkeypatch):
    """Recipients inside and outside test.dev all go to Mailpit unchanged."""
    constructor, smtp = _mock_smtp(monkeypatch)
    adapter = get_user_email_adapter()

    for recipient in (
        "developer@example.test",
        "ordinary-user@example.com",
        "blocked-in-production@test.dev",
        "blocked-subdomain@sub.test.dev",
    ):
        asyncio.run(adapter.send(make_message(recipient)))

    assert smtp.send_message.call_count == 4
    assert constructor.call_count == 4
    for call in constructor.call_args_list:
        assert call.kwargs["host"] == "mailpit"
        assert call.kwargs["port"] == 1025

    sent_messages = [call.args[0] for call in smtp.send_message.call_args_list]
    assert sent_messages[0]["To"] == "developer@example.test"
    assert sent_messages[1]["To"] == "ordinary-user@example.com"
    assert sent_messages[2]["To"] == "blocked-in-production@test.dev"
    assert sent_messages[2]["X-Forum-Routing-Environment"] == "test"
    assert sent_messages[2]["X-Forum-Original-Recipients"] == "blocked-in-production@test.dev"


def test_development_configuration_cannot_target_external_smtp(monkeypatch):
    """A development/test adapter fails closed if SMTP_HOST is not Mailpit."""
    from src.services.user import config as user_cfg

    monkeypatch.setattr(user_cfg.user_settings, "smtp_host", "smtp.example.com")
    with pytest.raises(EmailAdapterError, match="must target the Mailpit service host"):
        get_user_email_adapter()


def test_development_rejects_credentials(monkeypatch):
    """Production credentials must not be accepted by development/test configuration."""
    from src.services.user import config as user_cfg

    monkeypatch.setattr(user_cfg.user_settings, "smtp_user", "production-user")
    monkeypatch.setattr(user_cfg.user_settings, "smtp_password", "production-password")
    with pytest.raises(EmailAdapterError, match="must not be configured with credentials"):
        get_user_email_adapter()


def test_production_delivers_to_ordinary_external_domain(monkeypatch):
    """Production permits an ordinary external recipient through SMTP."""
    _configure_user_as_production(monkeypatch)
    _, smtp = _mock_smtp(monkeypatch)

    adapter = get_user_email_adapter()
    asyncio.run(adapter.send(make_message("user@ordinary.example")))

    assert smtp.send_message.call_count == 1
    sent = smtp.send_message.call_args.args[0]
    assert sent["To"] == "user@ordinary.example"


@pytest.mark.parametrize(
    "recipient",
    [
        "user@test.dev",
        "USER@TEST.DEV",
        "user@sub.test.dev",
        "user@deep.sub.test.dev",
        "user@TEST.DEV.",
    ],
)
def test_production_suppresses_test_dev_and_subdomains(monkeypatch, recipient):
    """Production suppresses exact test.dev and subdomains case-insensitively."""
    _configure_user_as_production(monkeypatch)
    constructor, smtp = _mock_smtp(monkeypatch)

    adapter = get_user_email_adapter()
    with pytest.raises(EmailSuppressedError) as exc_info:
        asyncio.run(adapter.send(make_message(recipient)))

    assert exc_info.value.is_suppressed is True
    assert exc_info.value.suppressed_recipients == (recipient,)
    assert smtp.send_message.call_count == 0
    assert constructor.call_count == 0


@pytest.mark.parametrize(
    "recipient",
    [
        "user@not-test.dev",
        "user@test.dev.example.com",
        "user@mytest.dev",
        "user@test.devil",
        "user@test.dev.evil",
    ],
)
def test_production_allows_lookalike_domains(monkeypatch, recipient):
    """Suppression uses exact domain boundaries, never substring matching."""
    _configure_user_as_production(monkeypatch)
    _, smtp = _mock_smtp(monkeypatch)

    adapter = get_user_email_adapter()
    asyncio.run(adapter.send(make_message(recipient)))

    assert smtp.send_message.call_count == 1


def test_production_suppression_is_observable_and_precedes_transport(monkeypatch, caplog):
    """Suppression is logged without invoking SMTP and exposes recipient metadata."""
    _configure_user_as_production(monkeypatch)
    constructor, smtp = _mock_smtp(monkeypatch)
    caplog.set_level(logging.WARNING)

    adapter = get_user_email_adapter()
    with pytest.raises(EmailSuppressedError) as exc_info:
        asyncio.run(adapter.send(make_message("secret@sub.test.dev")))

    assert "production recipient-domain policy" in caplog.text
    assert "sub.test.dev" in caplog.text
    assert exc_info.value.recipient == "secret@sub.test.dev"
    assert exc_info.value.domains == ("sub.test.dev",)
    assert constructor.call_count == 0
    assert smtp.send_message.call_count == 0


def test_smtp_connection_error_is_retryable(monkeypatch):
    """Transport failures remain non-suppression, non-recipient errors."""

    def raise_error(*args, **kwargs):
        raise ConnectionError("cannot connect")

    monkeypatch.setattr("smtplib.SMTP", raise_error)
    adapter = get_user_email_adapter()

    with pytest.raises(EmailAdapterError) as exc_info:
        asyncio.run(adapter.send(make_message()))

    assert not exc_info.value.is_recipient_rejected
    assert not exc_info.value.is_suppressed
    assert "connection failed" in str(exc_info.value).lower()


def test_smtp_recipient_refusal_is_recipient_rejected(monkeypatch):
    """SMTP recipient refusal keeps its dead-letter semantics."""
    import smtplib

    mock_smtp = mock.MagicMock()
    mock_smtp.send_message.side_effect = smtplib.SMTPRecipientsRefused(
        {"developer@example.test": (550, b"no such user")}
    )
    mock_ctx = mock.MagicMock()
    mock_ctx.__enter__.return_value = mock_smtp
    monkeypatch.setattr("smtplib.SMTP", lambda *args, **kwargs: mock_ctx)

    adapter = get_user_email_adapter()
    with pytest.raises(EmailAdapterError) as exc_info:
        asyncio.run(adapter.send(make_message()))

    assert exc_info.value.is_recipient_rejected is True
    assert exc_info.value.is_suppressed is False


def test_notification_adapter_uses_notification_sender(monkeypatch):
    _, smtp = _mock_smtp(monkeypatch)
    adapter = get_notification_email_adapter()
    asyncio.run(adapter.send(make_message(recipient="tester@example.test")))

    sent_msg = smtp.send_message.call_args.args[0]
    assert sent_msg["From"] == "notifications@forumplatform.local"


def test_notification_adapter_suppresses_production_test_domain(monkeypatch):
    from src.services.notification import config as notif_cfg

    monkeypatch.setattr(notif_cfg.notification_settings, "environment", "production")
    monkeypatch.setattr(notif_cfg.notification_settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(notif_cfg.notification_settings, "smtp_port", 587)
    monkeypatch.setattr(notif_cfg.notification_settings, "smtp_starttls", True)
    constructor, _ = _mock_smtp(monkeypatch)

    adapter = get_notification_email_adapter()
    with pytest.raises(EmailSuppressedError):
        asyncio.run(adapter.send(make_message("user@test.dev")))

    assert constructor.call_count == 0
