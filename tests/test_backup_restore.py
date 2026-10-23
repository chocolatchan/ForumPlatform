"""Safety-focused backup/restore tests.

These tests never connect to PostgreSQL, never invoke a real Docker daemon, and
never execute destructive SQL. They use temporary backup artifacts and a fake
``docker`` executable so validation/refusal behavior can be tested offline.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_SCRIPT = PROJECT_ROOT / "scripts" / "backup" / "backup_databases.sh"
RESTORE_SCRIPT = PROJECT_ROOT / "scripts" / "backup" / "restore_databases.sh"
BACKUP_RUNBOOK = PROJECT_ROOT / "docs" / "operations" / "backup_restore_runbook.md"
STAGING_RUNBOOK = PROJECT_ROOT / "docs" / "operations" / "staging_deployment_runbook.md"

DATABASES = [
    "forum_users",
    "forum_content",
    "forum_interactions",
    "forum_moderation",
    "forum_notifications",
    "forum_search",
]

EXPECTED_HEADS = {
    "users": "users_0003",
    "content": "content_0003",
    "interactions": "interactions_0002",
    "moderation": "moderation_0003",
    "notifications": "notifications_0004",
    "search": "search_0002",
}

TARGET_PREFIX = "restore_drill_20261023_"
ENVIRONMENT = "test"


def _run(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _write_fake_docker(bin_dir: Path) -> Path:
    """Create a fake docker CLI that never contacts a daemon."""
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env python3
import os
import sys

args = sys.argv[1:]
marker = os.environ.get("FAKE_DOCKER_MARKER")
if marker:
    with open(marker, "a", encoding="utf-8") as handle:
        handle.write(" ".join(args) + "\\n")

if "pg_dump" in args:
    fail_db = os.environ.get("FAKE_FAIL_DB")
    if fail_db and fail_db in args:
        sys.exit(1)
    sys.stdout.write("synthetic-dump-" + ("x" * 256) + "\\n")
    sys.exit(0)

if "pg_restore" in args and "--list" in args:
    sys.stdout.write("; synthetic archive listing\\n")
    sys.exit(0)

# Any unexpected docker command is recorded above and then fails closed.
sys.exit(2)
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return bin_dir


def _fake_env(
    bin_dir: Path,
    *,
    marker: Path | None = None,
    fail_db: str | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    if marker is not None:
        env["FAKE_DOCKER_MARKER"] = str(marker)
    if fail_db is not None:
        env["FAKE_FAIL_DB"] = fail_db
    return env


def _write_checksums(backup_dir: Path) -> None:
    lines: list[str] = []
    for db in DATABASES:
        digest = hashlib.sha256((backup_dir / f"{db}.dump").read_bytes()).hexdigest()
        lines.append(f"{digest}  {db}.dump")
    (backup_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_completed_artifact(root: Path) -> Path:
    backup_dir = root / "20261023_120000Z"
    backup_dir.mkdir()
    for db in DATABASES:
        (backup_dir / f"{db}.dump").write_bytes((f"synthetic {db}\n" * 12).encode())
    manifest = {
        "timestamp": "20261023_120000Z",
        "databases": DATABASES,
        "backup_format": "pg_dump -Fc (custom)",
        "checksum_algorithm": "sha256",
        "checksum_file": "SHA256SUMS",
        "checksum_scope": "six .dump files only",
        "status": "completed",
    }
    (backup_dir / "backup_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    _write_checksums(backup_dir)
    return backup_dir


def _restore_command(
    backup_dir: Path | None,
    *,
    target_prefix: str | None = TARGET_PREFIX,
    environment: str | None = ENVIRONMENT,
    confirm_target: str | None = None,
    verify_only: bool = True,
) -> list[str]:
    command = [str(RESTORE_SCRIPT)]
    if backup_dir is not None:
        command.extend(["--backup-dir", str(backup_dir)])
    if target_prefix is not None:
        command.extend(["--target-prefix", target_prefix])
    if environment is not None:
        command.extend(["--environment", environment])
    if confirm_target is not None:
        command.extend(["--confirm-target", confirm_target])
    if verify_only:
        command.append("--verify-only")
    return command


def _offline_migration_heads() -> dict[str, str]:
    """Compute heads from revision files only; no database connection."""
    heads: dict[str, str] = {}
    for service in EXPECTED_HEADS:
        revisions: dict[str, str] = {}
        down_revisions: set[str] = set()
        for path in sorted((PROJECT_ROOT / "migrations" / service / "versions").glob("*.py")):
            values: dict[str, object] = {}
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    if node.target.id in {"revision", "down_revision"} and node.value is not None:
                        values[node.target.id] = ast.literal_eval(node.value)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id in {
                            "revision",
                            "down_revision",
                        }:
                            values[target.id] = ast.literal_eval(node.value)
            revision = values.get("revision")
            assert isinstance(revision, str)
            revisions[revision] = path.name
            down_revision = values.get("down_revision")
            if isinstance(down_revision, str):
                down_revisions.add(down_revision)
            elif isinstance(down_revision, tuple):
                down_revisions.update(str(item) for item in down_revision)

        current_heads = sorted(set(revisions) - down_revisions)
        assert len(current_heads) == 1, (service, current_heads)
        heads[service] = current_heads[0]
    return heads


def test_backup_script_generates_checksums_and_restore_verify_only_accepts_artifact(
    tmp_path: Path,
) -> None:
    bin_dir = _write_fake_docker(tmp_path / "bin")
    backup_root = tmp_path / "backups"
    backup_marker = tmp_path / "backup_docker_calls"
    backup_result = _run(
        [str(BACKUP_SCRIPT), str(backup_root)],
        env=_fake_env(bin_dir, marker=backup_marker),
    )
    assert backup_result.returncode == 0, backup_result.stderr + backup_result.stdout

    completed_dirs = [
        path
        for path in backup_root.iterdir()
        if path.is_dir() and not path.name.endswith(".partial")
    ]
    assert len(completed_dirs) == 1
    backup_dir = completed_dirs[0]
    assert not (backup_dir / "INCOMPLETE").exists()

    checksum_lines = (backup_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert len(checksum_lines) == len(DATABASES)
    for line in checksum_lines:
        assert re.fullmatch(r"[0-9a-f]{64}  forum_[a-z_]+\.dump", line), line
    assert backup_marker.exists()
    assert "pg_dump" in backup_marker.read_text(encoding="utf-8")

    restore_marker = tmp_path / "restore_docker_calls"
    restore_result = _run(
        _restore_command(backup_dir),
        env=_fake_env(bin_dir, marker=restore_marker),
    )
    assert restore_result.returncode == 0, restore_result.stderr + restore_result.stdout
    assert "VERIFICATION COMPLETE" in restore_result.stdout
    assert not restore_marker.exists()


def test_failed_backup_remains_partial_and_restore_rejects_it(tmp_path: Path) -> None:
    bin_dir = _write_fake_docker(tmp_path / "bin")
    backup_root = tmp_path / "backups"
    result = _run(
        [str(BACKUP_SCRIPT), str(backup_root)],
        env=_fake_env(bin_dir, fail_db="forum_notifications"),
    )
    assert result.returncode != 0
    assert not any(
        path.is_dir() and not path.name.endswith(".partial") for path in backup_root.iterdir()
    )
    partial_dirs = [path for path in backup_root.iterdir() if path.name.endswith(".partial")]
    assert len(partial_dirs) == 1
    assert (partial_dirs[0] / "INCOMPLETE").exists()

    restore_result = _run(_restore_command(partial_dirs[0]))
    assert restore_result.returncode != 0
    assert ".partial" in restore_result.stderr


def test_restore_rejects_missing_target(tmp_path: Path) -> None:
    backup_dir = _write_completed_artifact(tmp_path)
    result = _run(_restore_command(backup_dir, target_prefix=None))
    assert result.returncode != 0
    assert "target-prefix" in result.stderr


@pytest.mark.parametrize("prefix", ["", "forum_", "forum_users"])
def test_restore_rejects_empty_or_live_prefix(tmp_path: Path, prefix: str) -> None:
    backup_dir = _write_completed_artifact(tmp_path)
    result = _run(_restore_command(backup_dir, target_prefix=prefix))
    assert result.returncode != 0
    assert "prefix" in result.stderr


@pytest.mark.parametrize("prefix", ["restore_", "restore_drill", "restore_Drill_"])
def test_restore_rejects_ambiguous_or_invalid_prefix(tmp_path: Path, prefix: str) -> None:
    backup_dir = _write_completed_artifact(tmp_path)
    result = _run(_restore_command(backup_dir, target_prefix=prefix))
    assert result.returncode != 0
    assert "prefix" in result.stderr


@pytest.mark.parametrize("environment", ["prod", "", " staging"])
def test_restore_rejects_unrecognized_environment(tmp_path: Path, environment: str) -> None:
    backup_dir = _write_completed_artifact(tmp_path)
    result = _run(_restore_command(backup_dir, environment=environment))
    assert result.returncode != 0
    assert "environment" in result.stderr.lower()


def test_restore_rejects_missing_checksum(tmp_path: Path) -> None:
    backup_dir = _write_completed_artifact(tmp_path)
    (backup_dir / "SHA256SUMS").unlink()
    result = _run(_restore_command(backup_dir))
    assert result.returncode != 0
    assert "SHA256SUMS" in result.stderr


def test_restore_rejects_malformed_checksum(tmp_path: Path) -> None:
    backup_dir = _write_completed_artifact(tmp_path)
    lines = (backup_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    lines[0] = "not-a-valid-checksum  forum_users.dump"
    (backup_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = _run(_restore_command(backup_dir))
    assert result.returncode != 0
    assert "well-formed entry" in result.stderr


def test_restore_rejects_checksum_mismatch(tmp_path: Path) -> None:
    backup_dir = _write_completed_artifact(tmp_path)
    (backup_dir / "forum_users.dump").write_bytes(b"tampered artifact\n")
    result = _run(_restore_command(backup_dir))
    assert result.returncode != 0
    assert "checksum verification failed" in result.stderr


def test_validation_failure_prevents_any_docker_invocation(tmp_path: Path) -> None:
    backup_dir = _write_completed_artifact(tmp_path)
    (backup_dir / "forum_content.dump").write_bytes(b"tampered artifact\n")
    bin_dir = _write_fake_docker(tmp_path / "bin")
    marker = tmp_path / "docker_calls"
    result = _run(
        _restore_command(
            backup_dir,
            confirm_target=TARGET_PREFIX,
            verify_only=False,
        ),
        env=_fake_env(bin_dir, marker=marker),
    )
    assert result.returncode != 0
    assert "checksum verification failed" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("confirm_target", [None, "", "restore_other_20261023_"])
def test_destructive_restore_requires_exact_non_empty_confirmation(
    tmp_path: Path, confirm_target: str | None
) -> None:
    backup_dir = _write_completed_artifact(tmp_path)
    bin_dir = _write_fake_docker(tmp_path / "bin")
    marker = tmp_path / "docker_calls"
    result = _run(
        _restore_command(
            backup_dir,
            confirm_target=confirm_target,
            verify_only=False,
        ),
        env=_fake_env(bin_dir, marker=marker),
    )
    assert result.returncode != 0
    assert "confirm-target" in result.stderr
    assert not marker.exists()


def test_migration_heads_match_current_revision_files() -> None:
    assert _offline_migration_heads() == EXPECTED_HEADS


def test_runbooks_use_current_heads_and_safe_restore_guidance() -> None:
    backup_runbook = BACKUP_RUNBOOK.read_text(encoding="utf-8")
    staging_runbook = STAGING_RUNBOOK.read_text(encoding="utf-8")

    for runbook in (backup_runbook, staging_runbook):
        assert "users_0002" not in runbook
        assert "notifications_0002" not in runbook
        assert "--backup-dir" in runbook
        assert "--target-prefix" in runbook
        assert "--verify-only" in runbook
        assert "SHA256SUMS" in runbook

    assert "users_0003" in staging_runbook
    assert "notifications_0004" in staging_runbook
    assert "scripts/migrate.py current all" not in staging_runbook
    assert "scripts/migrate.py upgrade all" not in staging_runbook
    assert "./scripts/backup/restore_databases.sh <path_to_backup_directory>" not in backup_runbook
    assert (
        "./scripts/backup/restore_databases.sh /path/to/pre_deployment_backup/"
        not in staging_runbook
    )
