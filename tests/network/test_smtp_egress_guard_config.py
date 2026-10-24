"""Static checks for the per-container outbound SMTP guard."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "docker-compose.yml"
DOCKERFILE_PATH = ROOT / "Dockerfile"
GUARD_PATH = ROOT / "scripts" / "network" / "smtp_egress_guard.sh"
VERIFY_PATH = ROOT / "scripts" / "network" / "verify_smtp_egress_guard.sh"

GUARDED_SERVICES = {"user-service", "notification-service"}


def _compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_only_email_sending_services_use_the_guard() -> None:
    services = _compose()["services"]

    for service_name in GUARDED_SERVICES:
        service = services[service_name]
        assert service["entrypoint"] == ["/app/scripts/network/smtp_egress_guard.sh"]
        assert "NET_ADMIN" in service["cap_add"]

    for service_name, service in services.items():
        if service_name in GUARDED_SERVICES:
            continue
        assert "NET_ADMIN" not in service.get("cap_add", [])
        assert service.get("entrypoint") != ["/app/scripts/network/smtp_egress_guard.sh"]


def test_guard_script_is_executable_and_syntax_valid() -> None:
    assert os.access(GUARD_PATH, os.X_OK)
    subprocess.run(["sh", "-n", str(GUARD_PATH)], check=True)


def test_verification_script_is_executable_and_syntax_valid() -> None:
    assert os.access(VERIFY_PATH, os.X_OK)
    subprocess.run(["sh", "-n", str(VERIFY_PATH)], check=True)
    script = VERIFY_PATH.read_text(encoding="utf-8")
    assert "controlled listener" in script
    assert "does not restart" in script


def test_guard_script_has_narrow_destination_rules() -> None:
    script = GUARD_PATH.read_text(encoding="utf-8")

    assert 'SMTP_GUARD_ALLOWED_HOST="${SMTP_GUARD_ALLOWED_HOST:-mailpit}"' in script
    assert 'SMTP_GUARD_ALLOWED_PORT="${SMTP_GUARD_ALLOWED_PORT:-1025}"' in script
    assert 'SMTP_GUARD_PORTS="${SMTP_GUARD_PORTS:-25 465 587 1025 2525}"' in script
    assert "REJECT --reject-with tcp-reset" in script
    assert "ip6tables" in script
    assert "setpriv" in script
    assert "--bounding-set=-net_admin" in script
    assert "staging|production" in script
    assert "development|test" in script


def test_guard_rules_do_not_use_application_smtp_host() -> None:
    script = GUARD_PATH.read_text(encoding="utf-8")

    # The allowlist target must not be derived from SMTP_HOST/SMTP_PORT.
    assert 'resolve_ipv4 "$SMTP_HOST"' not in script
    assert 'resolve_ipv6 "$SMTP_HOST"' not in script
    assert 'SMTP_GUARD_ALLOWED_HOST="${SMTP_HOST' not in script
    assert 'SMTP_GUARD_ALLOWED_PORT="${SMTP_PORT' not in script


def test_image_contains_iptables_and_capability_drop_tool() -> None:
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

    assert "iptables" in dockerfile
    assert "util-linux" in dockerfile
