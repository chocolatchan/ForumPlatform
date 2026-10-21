"""Moderation Service database session manager instance."""

from src.common.database import DatabaseSessionManager
from src.services.moderation.config import moderation_settings

moderation_db = DatabaseSessionManager(
    database_url=moderation_settings.database_url,
    pool_size=moderation_settings.db_pool_size,
    max_overflow=moderation_settings.db_max_overflow,
    pool_timeout=moderation_settings.db_pool_timeout,
    pool_pre_ping=moderation_settings.db_pool_pre_ping,
)
