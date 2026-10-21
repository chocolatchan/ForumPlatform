#!/usr/bin/env python3
"""Unified Database Migration CLI for Forum Platform.

Allows running Alembic migrations across all 6 service databases or individually.
Usage:
    python scripts/migrate.py upgrade [service|all] [revision]
    python scripts/migrate.py downgrade <service> <revision>
    python scripts/migrate.py current [service|all]
    python scripts/migrate.py revision <service> -m "<message>"
"""

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SERVICES = [
    "users",
    "content",
    "interactions",
    "moderation",
    "notifications",
    "search",
]


def get_alembic_config(service: str) -> Config:
    ini_path = PROJECT_ROOT / "migrations" / service / "alembic.ini"
    if not ini_path.is_file():
        raise FileNotFoundError(f"Alembic configuration not found: {ini_path}")
    cfg = Config(str(ini_path))
    # Ensure alembic resolves migrations directory relative to PROJECT_ROOT
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations" / service))
    return cfg


def run_upgrade(services: list[str], revision: str = "head") -> None:
    for s in services:
        print(f"=== [{s.upper()}] Upgrading to {revision} ===")
        cfg = get_alembic_config(s)
        command.upgrade(cfg, revision)


def run_downgrade(service: str, revision: str) -> None:
    print(f"=== [{service.upper()}] Downgrading to {revision} ===")
    cfg = get_alembic_config(service)
    command.downgrade(cfg, revision)


def run_current(services: list[str]) -> None:
    for s in services:
        print(f"=== [{s.upper()}] Current revision ===")
        cfg = get_alembic_config(s)
        command.current(cfg)


def run_revision(service: str, message: str, autogenerate: bool = False) -> None:
    print(f"=== [{service.upper()}] Generating new revision: '{message}' ===")
    cfg = get_alembic_config(service)
    command.revision(cfg, message=message, autogenerate=autogenerate)


def main() -> None:
    parser = argparse.ArgumentParser(description="Forum Platform Alembic Migration Runner")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    # Upgrade
    p_up = subparsers.add_parser("upgrade", help="Upgrade database(s) to revision")
    p_up.add_argument(
        "target", choices=[*SERVICES, "all"], default="all", nargs="?", help="Service name or 'all'"
    )
    p_up.add_argument("revision", default="head", nargs="?", help="Revision target (default: head)")

    # Downgrade
    p_down = subparsers.add_parser("downgrade", help="Downgrade a database to revision")
    p_down.add_argument("target", choices=SERVICES, help="Specific service name")
    p_down.add_argument("revision", help="Target revision identifier")

    # Current
    p_curr = subparsers.add_parser("current", help="Show current database revision(s)")
    p_curr.add_argument(
        "target", choices=[*SERVICES, "all"], default="all", nargs="?", help="Service name or 'all'"
    )

    # Revision
    p_rev = subparsers.add_parser("revision", help="Create a new migration revision")
    p_rev.add_argument("target", choices=SERVICES, help="Service name to generate revision for")
    p_rev.add_argument("-m", "--message", required=True, help="Revision message")
    p_rev.add_argument(
        "--autogenerate", action="store_true", help="Autogenerate migration from models"
    )

    args = parser.parse_args()

    # Add project root to python path so models can be imported
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    if args.cmd == "upgrade":
        targets = SERVICES if args.target == "all" else [args.target]
        run_upgrade(targets, args.revision)
    elif args.cmd == "downgrade":
        run_downgrade(args.target, args.revision)
    elif args.cmd == "current":
        targets = SERVICES if args.target == "all" else [args.target]
        run_current(targets)
    elif args.cmd == "revision":
        run_revision(args.target, args.message, args.autogenerate)


if __name__ == "__main__":
    main()
