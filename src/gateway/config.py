"""API Gateway configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    """Configuration settings for API Gateway."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    debug: bool = False
    port: int = Field(default=8000)
    service_name: str = "api-gateway"
    log_level: str = "INFO"

    # Upstream microservice base URLs
    user_service_url: str = Field(default="http://localhost:8001")
    content_service_url: str = Field(default="http://localhost:8002")
    interaction_service_url: str = Field(default="http://localhost:8003")
    moderation_service_url: str = Field(default="http://localhost:8004")
    notification_service_url: str = Field(default="http://localhost:8005")
    search_service_url: str = Field(default="http://localhost:8006")

    # Timeouts
    connect_timeout_seconds: float = Field(default=5.0)
    request_timeout_seconds: float = Field(default=30.0)


gateway_settings = GatewaySettings()
