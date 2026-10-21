"""Content Service database session manager instance."""

from src.common.database import DatabaseSessionManager
from src.services.content.config import content_settings

content_db = DatabaseSessionManager(
    database_url=content_settings.database_url,
    pool_size=content_settings.db_pool_size,
    max_overflow=content_settings.db_max_overflow,
    pool_timeout=content_settings.db_pool_timeout,
    pool_pre_ping=content_settings.db_pool_pre_ping,
)
