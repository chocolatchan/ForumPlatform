from typing import Self

from pydantic import Field, model_validator

from src.common.config import BaseServiceSettings


class UserSettings(BaseServiceSettings):
    """Configuration settings for User Service."""

    service_name: str = "user-service"
    port: int = Field(default=8001)

    database_url: str = Field(
        default="postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_users"
    )

    # Auth specific
    session_cookie_name: str = "forum_session"
    session_cookie_secure: bool | None = Field(
        default=None,
        description="Explicit override for Secure cookie flag. If None, enabled in staging and production.",
    )
    session_idle_timeout_seconds: int = 14400
    session_absolute_timeout_seconds: int = 86400
    csrf_secret: str = "default-dev-csrf-secret"

    # Rate Limiting Settings
    rate_limit_register_max: int = Field(
        default=10, description="Max register attempts per minute per IP"
    )
    rate_limit_login_max: int = Field(
        default=10, description="Max login attempts per minute per IP"
    )
    rate_limit_verify_otp_max: int = Field(
        default=15, description="Max verify attempts per minute per IP"
    )
    rate_limit_resend_otp_max: int = Field(
        default=5, description="Max resend attempts per minute per IP"
    )
    rate_limit_window_seconds: int = Field(
        default=60, description="Sliding window duration in seconds"
    )

    # Email & SMTP Delivery (Task 006.5)
    smtp_host: str = Field(default="localhost")
    smtp_port: int = Field(default=1025)
    smtp_user: str = Field(default="")
    smtp_password: str = Field(default="")
    smtp_use_tls: bool = Field(
        default=False, description="Use direct SSL/TLS for SMTP connection (e.g. port 465)"
    )
    smtp_starttls: bool = Field(
        default=False, description="Issue STARTTLS command for SMTP connection (e.g. port 587)"
    )
    email_from: str = Field(default="noreply@forumplatform.local")
    email_delivery_backend: str = Field(
        default="memory",
        description="Delivery backend: 'smtp' for local SMTP catcher (Mailpit) or 'memory' for unit tests",
    )
    dev_email_allowlist: str = Field(
        default="developer@example.test,tester@example.test",
        description="Comma-separated list of allowed recipient email addresses. Empty means fail-closed.",
    )

    def get_allowed_recipients(self) -> set[str]:
        """Return normalized (lowercased, trimmed) set of allowed recipient email addresses."""
        if not self.dev_email_allowlist or not self.dev_email_allowlist.strip():
            return set()
        return {
            part.strip().lower() for part in self.dev_email_allowlist.split(",") if part.strip()
        }

    def is_recipient_allowed(self, recipient: str) -> bool:
        """Check whether recipient email matches configured allowlist rules."""
        norm_recipient = recipient.strip().lower()
        allowed = self.get_allowed_recipients()
        if "*" in allowed:
            return True
        if norm_recipient in allowed:
            return True
        # Check wildcard domain patterns (e.g., *@example.test)
        if "@" in norm_recipient:
            domain = norm_recipient.split("@", 1)[1]
            if f"*@{domain}" in allowed:
                return True
        return False

    def is_cookie_secure(self) -> bool:
        """Determine whether session cookies must be marked with Secure attribute.

        Defaults to True in staging and production, or follows explicit session_cookie_secure override.
        """
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return self.environment in ("staging", "production")

    @model_validator(mode="after")
    def validate_production_security(self) -> "Self":
        """Enforce strict secret safety in production environment."""
        if self.environment == "production":
            insecure_defaults = {
                "default-dev-csrf-secret",
                "replace-with-random-csrf-secret-key",
            }
            if (
                not self.csrf_secret
                or self.csrf_secret in insecure_defaults
                or len(self.csrf_secret) < 32
            ):
                raise ValueError(
                    "Production requires a non-default, cryptographically secure CSRF_SECRET of at least 32 characters."
                )
            if self.email_delivery_backend == "smtp" and self.smtp_host in (
                "localhost",
                "127.0.0.1",
                "mailpit",
            ):
                # Warning or notice in production if pointing to local mailpit
                pass
        return self


user_settings = UserSettings()
