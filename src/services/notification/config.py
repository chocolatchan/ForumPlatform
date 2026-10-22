"""Notification Service configuration."""

from pydantic import Field

from src.common.config import BaseServiceSettings


class NotificationSettings(BaseServiceSettings):
    """Configuration settings for Notification Service."""

    service_name: str = "notification-service"
    port: int = Field(default=8005)

    database_url: str = Field(
        default="postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_notifications"
    )

    # Inter-service integration
    user_service_url: str = Field(default="http://localhost:8001")
    content_service_url: str = Field(default="http://localhost:8002")
    session_cookie_name: str = "forum_session"
    csrf_secret: str = "default-dev-csrf-secret"

    # Outbound Email Delivery & SMTP Configuration
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
    email_from: str = Field(default="notifications@forumplatform.local")
    email_delivery_backend: str = Field(
        default="memory",
        description="Delivery backend: 'smtp' for local SMTP catcher (Mailpit) or 'memory' for unit tests",
    )
    dev_email_allowlist: str = Field(
        default="developer@example.test,tester@example.test",
        description="Comma-separated list of allowed recipient email addresses. Empty means fail-closed.",
    )

    # Worker configuration
    email_worker_poll_interval: float = Field(
        default=1.0, description="Interval in seconds between email outbox polling runs"
    )
    email_worker_batch_size: int = Field(
        default=10, description="Max outbound emails claimed per batch"
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



notification_settings = NotificationSettings()
