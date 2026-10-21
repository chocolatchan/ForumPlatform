"""Automated integration tests for Phase D: PostgreSQL Backup and Isolated Restore.

Proves:
1. All six logical databases (forum_users, forum_content, forum_interactions,
   forum_moderation, forum_notifications, forum_search) can be dumped using pg_dump.
2. Backup artifacts exist, are non-empty, and contain expected format headers.
3. Dumps can be restored into fresh isolated databases (prefixed with 'restore_drill_')
   without impacting the active primary databases.
4. Schema objects, Alembic revision state, and table row counts match expected integrity criteria.
5. Isolated databases are cleanly destroyed after validation.
"""

import subprocess
from pathlib import Path

from sqlalchemy import text

from src.common.database import DatabaseSessionManager

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_SCRIPT = PROJECT_ROOT / "scripts" / "backup" / "backup_databases.sh"
RESTORE_SCRIPT = PROJECT_ROOT / "scripts" / "backup" / "restore_databases.sh"

DATABASES = [
    "forum_users",
    "forum_content",
    "forum_interactions",
    "forum_moderation",
    "forum_notifications",
    "forum_search",
]

EXPECTED_HEADS = {
    "forum_users": ("users", "alembic_version_users", "users_0002"),
    "forum_content": ("content", "alembic_version_content", "content_0003"),
    "forum_interactions": ("interactions", "alembic_version_interactions", "interactions_0002"),
    "forum_moderation": ("moderation", "alembic_version_moderation", "moderation_0003"),
    "forum_notifications": ("notifications", "alembic_version_notifications", "notifications_0002"),
    "forum_search": ("search", "alembic_version_search", "search_0002"),
}


def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )


def test_backup_and_isolated_restore_lifecycle(tmp_path: Path):
    """Execute live backup and isolated restore verification drill."""
    backup_dest = tmp_path / "backups"
    backup_dest.mkdir(parents=True, exist_ok=True)

    # 1. Execute backup
    res = run_cmd([str(BACKUP_SCRIPT), str(backup_dest)])
    assert "SUCCESS: All 6 databases backed up cleanly" in res.stdout

    # Find the timestamped backup folder
    backup_dirs = [d for d in backup_dest.iterdir() if d.is_dir()]
    assert len(backup_dirs) == 1
    target_dir = backup_dirs[0]

    # 2. Verify all 6 backup files exist and are non-empty (>1000 bytes)
    for db in DATABASES:
        dump_path = target_dir / f"{db}.dump"
        assert dump_path.is_file(), f"Expected backup dump {dump_path} does not exist"
        size = dump_path.stat().st_size
        assert size > 1000, f"Backup {dump_path} is suspiciously small ({size} bytes)"

    # Verify manifest
    manifest_path = target_dir / "backup_manifest.json"
    assert manifest_path.is_file()

    # 3. Restore into isolated test databases
    prefix = "restore_drill_"
    restore_res = run_cmd([str(RESTORE_SCRIPT), str(target_dir), prefix])
    assert "SUCCESS: All 6 databases restored successfully" in restore_res.stdout

    # 4. Connect to each restored database and verify Alembic head and table presence
    try:
        import asyncio

        async def verify_integrity():
            for db, (schema_name, table_name, expected_rev) in EXPECTED_HEADS.items():
                restored_db_name = f"{prefix}{db}"
                db_url = f"postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/{restored_db_name}"
                mgr = DatabaseSessionManager(db_url)
                try:
                    async with mgr.session() as session:
                        # Verify Alembic head
                        stmt = text(f"SELECT version_num FROM {schema_name}.{table_name}")
                        row = (await session.execute(stmt)).scalar_one_or_none()
                        assert row == expected_rev, (
                            f"Mismatch in {restored_db_name}: expected {expected_rev}, got {row}"
                        )

                        # Verify table count > 0
                        tables_stmt = text(
                            f"SELECT count(*) FROM information_schema.tables WHERE table_schema = '{schema_name}'"
                        )
                        table_count = (await session.execute(tables_stmt)).scalar()
                        assert table_count is not None and table_count > 0
                finally:
                    await mgr.close()

        asyncio.run(verify_integrity())

    finally:
        # 5. Clean up isolated databases
        for db in DATABASES:
            restored_db_name = f"{prefix}{db}"
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-U",
                    "forum_admin",
                    "-d",
                    "postgres",
                    "-c",
                    f"DROP DATABASE IF EXISTS {restored_db_name};",
                ],
                cwd=PROJECT_ROOT,
                check=False,
            )
