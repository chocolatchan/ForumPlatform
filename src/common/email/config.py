"""Shared email configuration contract for Forum Platform.

The application services use Mailpit as the development/test SMTP catcher. This
module centralizes the contract so the User and Notification services cannot drift
apart or silently select a different SMTP endpoint.

The validation here is deliberately configuration-only: it never opens a socket
and never sends a message. Runtime delivery and production-domain suppression
remain the shared adapter's responsibility.
"""

from __future__ import annotations

from typing import Final

from src.common.email.policy import (
    ALLOWED_EMAIL_ENVIRONMENTS,
    DEVELOPMENT_ENVIRONMENTS,
    normalize_environment,
)

MAILPIT_SERVICE_HOST: Final = "mailpit"
MAILPIT_SMTP_PORT: Final = 1025
MAILPIT_UI_PORT: Final = 8025

ALLOWED_EMAIL_BACKENDS: Final = frozenset({"smtp", "memory"})
LOCAL_SMTP_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})


class EmailConfigurationError(ValueError):
    """Raised when the email configuration contract is not satisfied."""


def validate_email_configuration(
    *,
    environment: str,
    backend: str,
    smtp_host: str,
    smtp_port: int,
    smtp_use_tls: bool,
    smtp_starttls: bool,
    smtp_user: str = "",
    smtp_password: str = "",
    environment_explicit: bool = True,
) -> None:
    """Validate the shared email contract without connecting to SMTP.

    Values are fail-closed:

    * the environment must be present and one of the supported values;
    * SMTP delivery requires an explicitly supplied environment;
    * development/test must target Mailpit by name on the internal Compose
      network and must not carry credentials or TLS settings;
    * production must select a non-local, non-Mailpit SMTP host.

    ``backend=memory`` remains available to deterministic tests. It is not an
    SMTP endpoint and therefore does not require a transport host or explicit
    environment, but any provided environment value must still be valid.
    """

    normalized_environment = normalize_environment(environment)
    normalized_backend = backend.strip().lower()
    normalized_host = smtp_host.strip().lower()

    if not normalized_environment:
        raise EmailConfigurationError("ENVIRONMENT must be set to a supported value.")
    if normalized_environment not in ALLOWED_EMAIL_ENVIRONMENTS:
        supported = ", ".join(sorted(ALLOWED_EMAIL_ENVIRONMENTS))
        raise EmailConfigurationError(
            f"ENVIRONMENT '{environment}' is not supported; expected one of: {supported}."
        )

    if normalized_backend not in ALLOWED_EMAIL_BACKENDS:
        raise EmailConfigurationError(
            "EMAIL_DELIVERY_BACKEND must be either 'smtp' or 'memory'."
        )

    if normalized_backend == "memory":
        # Memory delivery is a deterministic test sink, not an SMTP transport.
        return

    if not environment_explicit:
        raise EmailConfigurationError(
            "ENVIRONMENT must be explicitly set when EMAIL_DELIVERY_BACKEND=smtp."
        )

    if not normalized_host:
        raise EmailConfigurationError(
            "SMTP_HOST must be set when EMAIL_DELIVERY_BACKEND=smtp."
        )
    if not 1 <= smtp_port <= 65535:
        raise EmailConfigurationError("SMTP_PORT must be between 1 and 65535.")

    if normalized_environment in DEVELOPMENT_ENVIRONMENTS:
        if normalized_host in LOCAL_SMTP_HOSTS:
            raise EmailConfigurationError(
                "Development/test SMTP must use the Mailpit service hostname 'mailpit', "
                "not localhost or a loopback address."
            )
        if normalized_host != MAILPIT_SERVICE_HOST:
            raise EmailConfigurationError(
                "Development/test SMTP must target the Mailpit service host 'mailpit'."
            )
        if smtp_port != MAILPIT_SMTP_PORT:
            raise EmailConfigurationError(
                f"Development/test SMTP must use Mailpit's internal port {MAILPIT_SMTP_PORT}."
            )
        if smtp_use_tls or smtp_starttls:
            raise EmailConfigurationError(
                "Mailpit development/test SMTP is plain text; SMTP_USE_TLS and "
                "SMTP_STARTTLS must both be false."
            )
        if smtp_user.strip() or smtp_password:
            raise EmailConfigurationError(
                "Development/test Mailpit SMTP must not be configured with credentials; "
                "production SMTP credentials must never be supplied to non-production services."
            )
        return

    if normalized_environment == "production":
        if normalized_host in LOCAL_SMTP_HOSTS or normalized_host == MAILPIT_SERVICE_HOST:
            raise EmailConfigurationError(
                "Production SMTP_HOST must be an explicitly configured external host; "
                "Mailpit and loopback endpoints are development/test only."
            )
        return

    # Staging is accepted so the repository's existing staging Compose stack can
    # start, but loopback SMTP remains invalid. It is neither the development
    # Mailpit-only class nor the production suppression class.
    if normalized_host in LOCAL_SMTP_HOSTS:
        raise EmailConfigurationError(
            "Staging SMTP_HOST must not be localhost or a loopback address."
        )
