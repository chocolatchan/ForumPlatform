"""Notification Service configuration."""

from typing import Self

from pydantic import Field, model_validator

from src.common.config import BaseServiceSettings
from src.common.email.config import validate_email_configuration


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
        description="Delivery backend: 'smtp' for the shared transport or 'memory' for unit tests",
    )

    # Worker configuration
    email_worker_poll_interval: float = Field(
        default=1.0, description="Interval in seconds between email outbox polling runs"
    )
    email_worker_batch_size: int = Field(
        default=10, description="Max outbound emails claimed per batch"
    )
    email_worker_lease_timeout_seconds: int = Field(
        default=300,
        description="Seconds before a 'sending' row is considered an orphaned lease and eligible for recovery."
    )

    @model_validator(mode="after")
    def validate_email_transport(self) -> "Self":
        """Validate the shared email contract at service startup."""
        validate_email_configuration(
            environment=self.environment,
            environment_explicit="environment" in self.model_fields_set,
            backend=self.email_delivery_backend,
            smtp_host=self.smtp_host,
            smtp_port=self.smtp_port,
            smtp_use_tls=self.smtp_use_tls,
            smtp_starttls=self.smtp_starttls,
            smtp_user=self.smtp_user,
            smtp_password=self.smtp_password,
        )
        return self


notification_settings = NotificationSettings()
