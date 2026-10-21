"""Async database session and engine lifecycle management."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)


class DatabaseSessionManager:
    """Manages the lifecycle of an async SQLAlchemy engine and session factory."""

    def __init__(self, database_url: str, **engine_kwargs: Any) -> None:
        filtered_kwargs = dict(engine_kwargs)
        if "sqlite" in database_url:
            filtered_kwargs.pop("pool_size", None)
            filtered_kwargs.pop("max_overflow", None)
            filtered_kwargs.pop("pool_timeout", None)
            connect_args = filtered_kwargs.get("connect_args", {})
            connect_args["check_same_thread"] = False
            connect_args.setdefault("timeout", 30.0)
            filtered_kwargs["connect_args"] = connect_args

        self._engine: AsyncEngine | None = create_async_engine(
            database_url,
            **filtered_kwargs,
        )

        if "sqlite" in database_url:
            import uuid

            from sqlalchemy import event

            @event.listens_for(self._engine.sync_engine, "before_cursor_execute", retval=True)
            def _sqlite_before_cursor_execute(
                conn: Any,
                cursor: Any,
                statement: str,
                parameters: Any,
                context: Any,
                executemany: bool,
            ) -> tuple[str, Any]:
                if parameters:
                    if isinstance(parameters, (tuple, list)):
                        new_params = []
                        for p in parameters:
                            if isinstance(p, str) and len(p) == 32 and p.isdigit():
                                new_params.append(
                                    f"{p[:8]}-{p[8:12]}-{p[12:16]}-{p[16:20]}-{p[20:]}"
                                )
                            else:
                                new_params.append(p)
                        parameters = tuple(new_params)
                    elif isinstance(parameters, dict):
                        new_dict = {}
                        for k, v in parameters.items():
                            if isinstance(v, str) and len(v) == 32 and v.isdigit():
                                new_dict[k] = f"{v[:8]}-{v[8:12]}-{v[12:16]}-{v[16:20]}-{v[20:]}"
                            else:
                                new_dict[k] = v
                        parameters = new_dict
                return statement, parameters

            @event.listens_for(self._engine.sync_engine, "connect")
            def _sqlite_connect(dbapi_connection: Any, connection_record: Any) -> None:
                raw_conn = getattr(dbapi_connection, "_connection", dbapi_connection)
                underlying = getattr(raw_conn, "_conn", raw_conn)
                if hasattr(underlying, "create_function"):
                    underlying.create_function(
                        "char_length", 1, lambda s: len(s) if s is not None else 0
                    )
                    underlying.create_function("gen_random_uuid", 0, lambda: str(uuid.uuid4()))
                if hasattr(underlying, "execute"):
                    underlying.execute("PRAGMA journal_mode = WAL")
                    underlying.execute("PRAGMA busy_timeout = 10000")
                    underlying.execute("PRAGMA foreign_keys = ON")
                    import os

                    # If database_url is a file path, attach sibling schema files in the same directory
                    db_dir = "/tmp/sqlite_schemas"
                    os.makedirs(db_dir, exist_ok=True)
                    for schema_name in (
                        "users",
                        "content",
                        "interactions",
                        "moderation",
                        "notifications",
                        "search",
                    ):
                        schema_path = os.path.join(db_dir, f"{schema_name}.db")
                        underlying.execute(f"ATTACH DATABASE '{schema_path}' AS {schema_name}")
                        underlying.execute(f"PRAGMA {schema_name}.journal_mode = WAL")
                        underlying.execute(f"PRAGMA {schema_name}.busy_timeout = 10000")

        self._sessionmaker: async_sessionmaker[AsyncSession] | None = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            autoflush=False,
            expire_on_commit=False,
        )

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("Database engine has been disposed or not initialized.")
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is None:
            raise RuntimeError("Database session factory has been disposed.")
        return self._sessionmaker

    async def close(self) -> None:
        """Dispose of the database engine on application shutdown."""
        if self._engine is not None:
            logger.info("Disposing database connection pool...")
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Provide a scoped session context without automatic commit.

        Transaction ownership belongs to the caller (service/unit of work).
        """
        if self._sessionmaker is None:
            raise RuntimeError("DatabaseSessionManager is not initialized.")
        async with self._sessionmaker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[AsyncSession, None]:
        """Provide an explicit transactional session that commits on success and rolls back on error."""
        if self._sessionmaker is None:
            raise RuntimeError("DatabaseSessionManager is not initialized.")
        async with self._sessionmaker() as session, session.begin():
            yield session

    async def ping(self) -> bool:
        """Check database readiness via a lightweight query."""
        if self._engine is None:
            return False
        try:
            async with self.session() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.warning("Database ping failed: %s", e)
            return False

    def get_pool_status(self) -> dict[str, int]:
        """Return current database connection pool utilization metrics."""
        if self._engine is None:
            return {"size": 0, "checked_in": 0, "checked_out": 0, "overflow": 0}
        pool = self._engine.sync_engine.pool
        return {
            "size": getattr(pool, "size", lambda: 0)(),
            "checked_in": getattr(pool, "checkedin", lambda: 0)(),
            "checked_out": getattr(pool, "checkedout", lambda: 0)(),
            "overflow": getattr(pool, "overflow", lambda: 0)(),
        }
