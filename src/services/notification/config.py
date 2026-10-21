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


notification_settings = NotificationSettings()
