"""Deterministic tests for the shared email configuration contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.common.email.config import (
    MAILPIT_SERVICE_HOST,
    MAILPIT_SMTP_PORT,
    EmailConfigurationError,
    validate_email_configuration,
)
from src.services.notification.config import NotificationSettings
from src.services.user.config import UserSettings

DATABASE_URL = "postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_users"


def valid_kwargs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "environment": "test",
        "environment_explicit": True,
        "backend": "smtp",
        "smtp_host": MAILPIT_SERVICE_HOST,
        "smtp_port": MAILPIT_SMTP_PORT,
        "smtp_use_tls": False,
        "smtp_starttls": False,
        "smtp_user": "",
        "smtp_password": "",
    }
    values.update(overrides)
    return values


def test_mailpit_configuration_is_accepted() -> None:
    validate_email_configuration(**valid_kwargs())


@pytest.mark.parametrize("environment", ["", "   ", "qa", "local"])
def test_invalid_or_missing_environment_is_rejected(environment: str) -> None:
    with pytest.raises(EmailConfigurationError, match="ENVIRONMENT"):
        validate_email_configuration(**valid_kwargs(environment=environment))


def test_smtp_requires_an_explicit_environment() -> None:
    with pytest.raises(EmailConfigurationError, match="explicitly set"):
        validate_email_configuration(**valid_kwargs(environment_explicit=False))


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1"])
def test_localhost_is_rejected_in_development_and_test(host: str) -> None:
    with pytest.raises(EmailConfigurationError, match="not localhost"):
        validate_email_configuration(**valid_kwargs(smtp_host=host))


def test_public_smtp_host_is_rejected_in_development_and_test() -> None:
    with pytest.raises(EmailConfigurationError, match="must target the Mailpit service host"):
        validate_email_configuration(**valid_kwargs(smtp_host="smtp.example.com"))


def test_wrong_mailpit_port_is_rejected() -> None:
    with pytest.raises(EmailConfigurationError, match="internal port 1025"):
        validate_email_configuration(**valid_kwargs(smtp_port=8025))


def test_tls_is_rejected_for_mailpit() -> None:
    with pytest.raises(EmailConfigurationError, match="plain text"):
        validate_email_configuration(**valid_kwargs(smtp_use_tls=True))


def test_credentials_are_rejected_for_mailpit() -> None:
    with pytest.raises(EmailConfigurationError, match="must not be configured with credentials"):
        validate_email_configuration(
            **valid_kwargs(smtp_user="production-user", smtp_password="production-password")
        )


def test_memory_backend_does_not_require_an_smtp_endpoint() -> None:
    validate_email_configuration(**valid_kwargs(backend="memory", smtp_host="", smtp_port=0))


def test_memory_backend_still_rejects_invalid_environment() -> None:
    with pytest.raises(EmailConfigurationError, match="ENVIRONMENT"):
        validate_email_configuration(
            **valid_kwargs(backend="memory", environment="invalid", smtp_host="", smtp_port=0)
        )


@pytest.mark.parametrize("host", [MAILPIT_SERVICE_HOST, "localhost", "127.0.0.1", "::1"])
def test_production_rejects_mailpit_and_loopback(host: str) -> None:
    with pytest.raises(EmailConfigurationError, match="Production SMTP_HOST"):
        validate_email_configuration(
            **valid_kwargs(environment="production", smtp_host=host)
        )


def test_production_accepts_an_explicit_external_host_and_credentials() -> None:
    validate_email_configuration(
        **valid_kwargs(
            environment="production",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_starttls=True,
            smtp_user="provider-user",
            smtp_password="provider-password",
        )
    )


def test_staging_remains_valid_but_rejects_loopback() -> None:
    validate_email_configuration(
        **valid_kwargs(environment="staging", smtp_host="staging-smtp.example.com")
    )
    with pytest.raises(EmailConfigurationError, match="Staging SMTP_HOST"):
        validate_email_configuration(
            **valid_kwargs(environment="staging", smtp_host="localhost")
        )


def test_user_service_startup_rejects_localhost_smtp() -> None:
    with pytest.raises(ValidationError, match="not localhost"):
        UserSettings(
            database_url=DATABASE_URL,
            environment="test",
            email_delivery_backend="smtp",
            smtp_host="localhost",
            smtp_port=MAILPIT_SMTP_PORT,
        )


def test_notification_service_startup_rejects_public_smtp_fallback() -> None:
    with pytest.raises(ValidationError, match="must target the Mailpit service host"):
        NotificationSettings(
            database_url=DATABASE_URL,
            environment="test",
            email_delivery_backend="smtp",
            smtp_host="smtp.example.com",
            smtp_port=MAILPIT_SMTP_PORT,
        )


def test_user_service_smtp_startup_requires_explicit_environment(monkeypatch) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    with pytest.raises(ValidationError, match="explicitly set"):
        UserSettings(
            _env_file=None,
            database_url=DATABASE_URL,
            email_delivery_backend="smtp",
            smtp_host=MAILPIT_SERVICE_HOST,
            smtp_port=MAILPIT_SMTP_PORT,
        )


def test_notification_service_smtp_startup_requires_explicit_environment(monkeypatch) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    with pytest.raises(ValidationError, match="explicitly set"):
        NotificationSettings(
            _env_file=None,
            database_url=DATABASE_URL,
            email_delivery_backend="smtp",
            smtp_host=MAILPIT_SERVICE_HOST,
            smtp_port=MAILPIT_SMTP_PORT,
        )
