"""Base configuration using Pydantic Settings."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    """Base settings for microservices with async database connectivity."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="development")
    debug: bool = Field(default=False)
    log_level: str = Field(default="INFO")

    # Database connection settings
    database_url: str = Field(..., description="Async database connection URL")
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=10, ge=0, le=50)
    db_pool_timeout: int = Field(default=30, ge=1, le=120)
    db_pool_pre_ping: bool = Field(default=True)

    # Inter-service authentication & Proxy trust
    internal_service_secret: str = Field(
        default="default-internal-service-secret-key-32chars",
        description="Shared secret for internal service-to-service calls",
    )
    trusted_proxies: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "::1", "gateway"],
        description="List of trusted reverse proxy IPs / hostnames for X-Forwarded-For parsing",
    )

    @field_validator("database_url")
    @classmethod
    def validate_async_pg_url(cls, v: str) -> str:
        if v.startswith("sqlite+aiosqlite://") or v.startswith("sqlite://"):
            return (
                v
                if v.startswith("sqlite+aiosqlite://")
                else v.replace("sqlite://", "sqlite+aiosqlite://", 1)
            )
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError(
                f"Database URL must use postgresql+asyncpg or sqlite+aiosqlite driver. Received: {v.split('://')[0]}://"
            )
        return v
