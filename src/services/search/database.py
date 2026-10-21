"""Search Service database session manager instance."""

from src.common.database import DatabaseSessionManager
from src.services.search.config import search_settings

search_db = DatabaseSessionManager(
    database_url=search_settings.database_url,
    pool_size=search_settings.db_pool_size,
    max_overflow=search_settings.db_max_overflow,
    pool_timeout=search_settings.db_pool_timeout,
    pool_pre_ping=search_settings.db_pool_pre_ping,
)
