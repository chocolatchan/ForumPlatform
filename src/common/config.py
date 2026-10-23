"""Base configuration using Pydantic Settings."""

from typing import Self

from pydantic import Field, field_validator, model_validator
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
        default="",
        description="Shared secret for internal service-to-service calls",
    )
    internal_service_auth_bypass: bool = Field(
        default=False,
        description=(
            "Explicitly allow missing internal credentials only when ENVIRONMENT is "
            "explicitly development or test. Missing or other environments are rejected."
        ),
    )
    trusted_proxies: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "::1", "gateway"],
        description="List of trusted reverse proxy IPs / hostnames for X-Forwarded-For parsing",
    )

    # Inter-service base URLs
    user_service_url: str = Field(default="http://localhost:8001")
    content_service_url: str = Field(default="http://localhost:8002")
    interaction_service_url: str = Field(default="http://localhost:8003")
    moderation_service_url: str = Field(default="http://localhost:8004")
    notification_service_url: str = Field(default="http://localhost:8005")
    search_service_url: str = Field(default="http://localhost:8006")

    def get_service_urls(self) -> dict[str, str]:
        return {
            "user-service": self.user_service_url,
            "content-service": self.content_service_url,
            "interaction-service": self.interaction_service_url,
            "moderation-service": self.moderation_service_url,
            "notification-service": self.notification_service_url,
            "search-service": self.search_service_url,
        }

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

    @field_validator("internal_service_secret")
    @classmethod
    def validate_internal_service_secret_not_empty(cls, v: str) -> str:
        """Reject missing or whitespace-only internal credentials."""
        if not v.strip():
            raise ValueError("INTERNAL_SERVICE_SECRET must not be empty.")
        return v

    @model_validator(mode="after")
    def validate_staging_production_secrets(self) -> Self:
        """Enforce strict configuration rules for staging and production environments."""
        if self.internal_service_auth_bypass:
            # The bypass is fail-closed: require an explicit development/test
            # environment rather than trusting the default "development" value.
            if "environment" not in self.model_fields_set:
                raise ValueError(
                    "INTERNAL_SERVICE_AUTH_BYPASS requires ENVIRONMENT to be explicitly set "
                    "to development or test."
                )
            if self.environment not in ("development", "test"):
                raise ValueError(
                    f"{self.environment.capitalize()} must not enable "
                    "INTERNAL_SERVICE_AUTH_BYPASS; the bypass is restricted to development and test."
                )

        if self.environment in ("staging", "production"):
            # Check CSRF secret if present on settings instance first for backward compatibility
            csrf_val = getattr(self, "csrf_secret", None)
            if csrf_val is not None:
                insecure_csrf_defaults = {
                    "default-dev-csrf-secret",
                    "replace-with-random-csrf-secret-key",
                }
                if not csrf_val or csrf_val in insecure_csrf_defaults or len(csrf_val) < 32:
                    raise ValueError(
                        f"{self.environment.capitalize()} requires a non-default, cryptographically secure CSRF_SECRET of at least 32 characters."
                    )

            insecure_key_defaults = {
                "default-internal-service-secret-key-32chars",
                "default-dev-internal-key",
                "replace-with-secure-internal-secret-min-32-chars",
                "replace-with-a-secure-random-secret-key-min-32-chars",
                "replace-with-random-csrf-secret-key",
                "staging-internal-service-secret-must-be-32-chars-long",
                "forum_dev_password",
            }
            if (
                not self.internal_service_secret
                or self.internal_service_secret in insecure_key_defaults
            ):
                raise ValueError(
                    f"{self.environment.capitalize()} environment requires a non-default, secure INTERNAL_SERVICE_SECRET."
                )
            if len(self.internal_service_secret) < 32:
                raise ValueError(
                    f"{self.environment.capitalize()} INTERNAL_SERVICE_SECRET must be at least 32 characters long."
                )
            if "forum_dev_password" in self.database_url:
                raise ValueError(
                    f"{self.environment.capitalize()} DATABASE_URL must not contain default development password 'forum_dev_password'."
                )
        return self
