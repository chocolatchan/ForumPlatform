"""Interaction Service database session manager instance."""

from src.common.database import DatabaseSessionManager
from src.services.interaction.config import interaction_settings

interaction_db = DatabaseSessionManager(
    database_url=interaction_settings.database_url,
    pool_size=interaction_settings.db_pool_size,
    max_overflow=interaction_settings.db_max_overflow,
    pool_timeout=interaction_settings.db_pool_timeout,
    pool_pre_ping=interaction_settings.db_pool_pre_ping,
)
