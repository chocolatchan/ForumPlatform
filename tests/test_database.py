"""Tests for DatabaseSessionManager lifecycle and transactions."""

import pytest

from src.common.database import DatabaseSessionManager


def test_session_manager_init():
    db = DatabaseSessionManager(
        database_url="postgresql+asyncpg://test:test@localhost:5432/forum_test",
        echo=False,
        pool_size=5,
        max_overflow=2,
    )
    assert db._engine is not None
    assert db._sessionmaker is not None


@pytest.mark.asyncio
async def test_session_manager_close():
    db = DatabaseSessionManager(
        database_url="postgresql+asyncpg://test:test@localhost:5432/forum_test",
    )
    await db.close()
    assert db._engine is None
    assert db._sessionmaker is None


@pytest.mark.asyncio
async def test_session_manager_not_initialized():
    db = DatabaseSessionManager(
        database_url="postgresql+asyncpg://test:test@localhost:5432/forum_test",
    )
    await db.close()
    with pytest.raises(RuntimeError, match="DatabaseSessionManager is not initialized"):
        async with db.session():
            pass
