"""Moderation Service configuration."""

from pydantic import Field

from src.common.config import BaseServiceSettings


class ModerationSettings(BaseServiceSettings):
    """Configuration settings for Moderation Service."""

    service_name: str = "moderation-service"
    port: int = Field(default=8004)

    database_url: str = Field(
        default="postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_moderation"
    )

    # Inter-service integration
    user_service_url: str = Field(default="http://localhost:8001")
    content_service_url: str = Field(default="http://localhost:8002")
    session_cookie_name: str = "forum_session"
    csrf_secret: str = "default-dev-csrf-secret"


moderation_settings = ModerationSettings()
