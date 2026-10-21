"""Notification Service Alembic env.py."""

from alembic import context

from migrations.async_env import run_service_migrations
from src.common.models import Base

run_service_migrations(
    alembic_config=context.config,
    target_metadata=Base.metadata,
    db_url_env_var="DATABASE_NOTIFICATIONS_URL",
    default_db_url="postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_notifications",
    schema_name="notifications",
)
