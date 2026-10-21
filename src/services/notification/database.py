"""Notification Service database session manager instance."""

from src.common.database import DatabaseSessionManager
from src.services.notification.config import notification_settings

notification_db = DatabaseSessionManager(
    database_url=notification_settings.database_url,
    pool_size=notification_settings.db_pool_size,
    max_overflow=notification_settings.db_max_overflow,
    pool_timeout=notification_settings.db_pool_timeout,
    pool_pre_ping=notification_settings.db_pool_pre_ping,
)
