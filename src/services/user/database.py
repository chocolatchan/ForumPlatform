"""User Service database session manager instance."""

from src.common.database import DatabaseSessionManager
from src.services.user.config import user_settings

user_db = DatabaseSessionManager(
    database_url=user_settings.database_url,
    pool_size=user_settings.db_pool_size,
    max_overflow=user_settings.db_max_overflow,
    pool_timeout=user_settings.db_pool_timeout,
    pool_pre_ping=user_settings.db_pool_pre_ping,
)
