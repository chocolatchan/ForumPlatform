"""Shared isolation policy for PostgreSQL-backed outbox tests.

The helpers in this module are intentionally free of database connections and application
settings.  A test database target must come from an explicit ``TEST_*_DATABASE_URL``
environment variable and must use a disposable database name.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

from sqlalchemy import Delete
from sqlalchemy.engine import make_url

# Database names in this repository that back running application services.
LIVE_DATABASE_NAMES = frozenset(
    {
        "forum_users",
        "forum_content",
        "forum_interactions",
        "forum_moderation",
        "forum_notifications",
        "forum_search",
    }
)

# Disposable database prefixes used by the repository's test and stress tooling.
DISPOSABLE_DATABASE_PREFIXES = ("test_", "stress_")
_DISPOSABLE_DATABASE_PATTERN = re.compile(r"^(?:test|stress)_[a-z0-9_]+$")


class UnsafeTestDatabaseError(ValueError):
    """Raised when a test database URL is missing, malformed, or not disposable."""


def validate_test_database_url(raw_url: str, env_var: str) -> str:
    """Validate and normalize a disposable PostgreSQL test database URL.

    The caller supplies the environment variable name so errors identify the required
    configuration without echoing credentials.  No connection is opened here.
    """

    candidate = raw_url.strip()
    if not candidate:
        raise UnsafeTestDatabaseError(f"{env_var} is set but empty; refusing to connect.")

    try:
        url = make_url(candidate)
    except Exception:
        raise UnsafeTestDatabaseError(
            f"{env_var} is not a parseable database URL; refusing to connect."
        ) from None

    if url.drivername not in {"postgresql", "postgresql+asyncpg"}:
        raise UnsafeTestDatabaseError(
            f"{env_var} must be a PostgreSQL URL using the asyncpg driver; refusing to connect."
        )

    database_name = (url.database or "").casefold()
    if not database_name:
        raise UnsafeTestDatabaseError(
            f"{env_var} must name an explicit disposable database; refusing to connect."
        )

    if database_name in LIVE_DATABASE_NAMES or database_name.startswith("forum_"):
        raise UnsafeTestDatabaseError(
            f"{env_var} points at a live/shared application database; refusing to connect."
        )

    if not _DISPOSABLE_DATABASE_PATTERN.fullmatch(database_name):
        prefixes = ", ".join(f"{prefix}*" for prefix in DISPOSABLE_DATABASE_PREFIXES)
        raise UnsafeTestDatabaseError(
            f"{env_var} must name a disposable database matching {prefixes}; refusing to connect."
        )

    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+asyncpg")

    return url.render_as_string(hide_password=False)


def load_optional_test_database_url(
    env_var: str,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Load a test database URL without any fallback to app settings or ``.env``.

    An unset or blank variable returns ``None`` so the corresponding database-backed
    tests can be skipped safely.  A present but invalid value raises immediately during
    collection, before any connection can be opened.
    """

    source = os.environ if environ is None else environ
    raw_url = source.get(env_var)
    if raw_url is None or not raw_url.strip():
        return None
    return validate_test_database_url(raw_url, env_var)


async def cleanup_test_rows(db_manager: Any, *statements: Delete) -> None:
    """Execute only explicitly scoped cleanup statements in one transaction.

    Callers must build ``delete(...).where(...)`` statements using identifiers created by
    the test.  Passing no statements is a no-op, preventing accidental table-wide cleanup.
    """

    if not statements:
        return

    async with db_manager.transaction() as session:
        for statement in statements:
            await session.execute(statement)
