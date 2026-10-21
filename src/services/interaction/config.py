"""Interaction Service configuration."""

from pydantic import Field

from src.common.config import BaseServiceSettings


class InteractionSettings(BaseServiceSettings):
    """Configuration settings for Interaction Service."""

    service_name: str = "interaction-service"
    port: int = Field(default=8003)

    database_url: str = Field(
        default="postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_interactions"
    )

    view_window_seconds: int = 1800  # 30-minute deduplication window (DEC-005)
    view_hmac_secret: str = "default-dev-view-hmac-secret"

    # Inter-service integration
    user_service_url: str = Field(default="http://localhost:8001")
    content_service_url: str = Field(default="http://localhost:8002")
    session_cookie_name: str = "forum_session"
    csrf_secret: str = "default-dev-csrf-secret"


interaction_settings = InteractionSettings()
