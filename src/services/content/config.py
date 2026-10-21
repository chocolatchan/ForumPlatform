"""Content Service configuration."""

from pydantic import Field

from src.common.config import BaseServiceSettings


class ContentSettings(BaseServiceSettings):
    """Configuration settings for Content Service."""

    service_name: str = "content-service"
    port: int = Field(default=8002)

    database_url: str = Field(
        default="postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_content"
    )

    storage_backend: str = "local"
    storage_local_dir: str = "./uploads"
    storage_max_file_size_bytes: int = 2097152

    # User Service / Auth integration
    user_service_url: str = Field(default="http://localhost:8001")
    session_cookie_name: str = "forum_session"
    csrf_secret: str = "default-dev-csrf-secret"


content_settings = ContentSettings()
