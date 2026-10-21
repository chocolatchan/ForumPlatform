"""Shared async Alembic migration runner for Forum Platform microservices."""

import asyncio
import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.schema import CreateSchema


def run_service_migrations(
    alembic_config: Any,
    target_metadata: Any,
    db_url_env_var: str,
    default_db_url: str,
    schema_name: str | None = None,
) -> None:
    """Execute migrations for a specific service using async engine."""
    # Configure logging from alembic.ini if present
    if alembic_config.config_file_name is not None:
        fileConfig(alembic_config.config_file_name)

    # Resolve database URL from environment variable or fallback
    url = os.getenv(db_url_env_var)
    if not url:
        # Check if generic DATABASE_URL is set (e.g. inside Docker container)
        generic_url = os.getenv("DATABASE_URL")
        if generic_url and schema_name:
            # Replace database name with service database if needed
            # e.g. postgresql+asyncpg://.../forum_users -> forum_<service>
            db_name = f"forum_{schema_name}"
            prefix = generic_url.rsplit("/", 1)[0]
            url = f"{prefix}/{db_name}"
        else:
            url = generic_url or default_db_url

    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    alembic_config.set_main_option("sqlalchemy.url", url)

    def do_run_migrations(connection: Any) -> None:
        # Ensure target schema exists in single-cluster dev environments
        if schema_name:
            connection.execute(CreateSchema(schema_name, if_not_exists=True))

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=f"alembic_version_{schema_name}" if schema_name else "alembic_version",
            version_table_schema=schema_name,
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    async def run_async_migrations() -> None:
        from sqlalchemy.ext.asyncio import create_async_engine

        connectable = create_async_engine(
            url,
            poolclass=pool.NullPool,
        )
        async with connectable.begin() as connection:
            await connection.run_sync(do_run_migrations)
        await connectable.dispose()

    if context.is_offline_mode():
        context.configure(
            url=url,
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            version_table_schema=schema_name,
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        asyncio.run(run_async_migrations())
