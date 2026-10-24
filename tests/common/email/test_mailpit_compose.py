"""Static checks for the Mailpit/Compose email configuration contract.

These tests deliberately read the Compose file only. They do not start
containers, open SMTP connections, or send mail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[3] / "docker-compose.yml"


def _compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_mailpit_uses_internal_smtp_port_and_preserves_ui_mapping() -> None:
    compose = _compose()
    mailpit = compose["services"]["mailpit"]

    assert mailpit["environment"]["MP_SMTP_BIND_ADDR"] == "0.0.0.0:1025"
    assert mailpit["environment"]["MP_UI_BIND_ADDR"] == "0.0.0.0:8025"
    assert "${MAILPIT_UI_PORT:-8025}:8025" in mailpit["ports"]

    # SMTP is reachable only over the Compose network; only the UI/API is published.
    assert all("1025" not in str(port) for port in mailpit["ports"])
    assert "/mailpit" in mailpit["healthcheck"]["test"]
    assert "readyz" in mailpit["healthcheck"]["test"]


def test_mailpit_accepts_every_routed_development_recipient() -> None:
    """No Mailpit recipient filter may contradict development/test routing."""
    mailpit = _compose()["services"]["mailpit"]
    environment = mailpit["environment"]

    assert "MP_SMTP_ALLOWED_RECIPIENTS" not in environment
    assert "MAILPIT_ALLOWED_RECIPIENTS" not in str(environment)


def test_application_services_share_the_mailpit_email_contract() -> None:
    compose = _compose()
    shared = compose["x-email-environment"]

    assert shared["EMAIL_DELIVERY_BACKEND"] == "${EMAIL_DELIVERY_BACKEND:-smtp}"
    assert shared["SMTP_HOST"] == "${DEV_SMTP_HOST:-mailpit}"
    assert shared["SMTP_PORT"] == "${DEV_SMTP_PORT:-1025}"
    assert shared["SMTP_USER"] == "${DEV_SMTP_USER:-}"
    assert shared["SMTP_PASSWORD"] == "${DEV_SMTP_PASSWORD:-}"
    assert shared["SMTP_USE_TLS"] == "${DEV_SMTP_USE_TLS:-false}"
    assert shared["SMTP_STARTTLS"] == "${DEV_SMTP_STARTTLS:-false}"
    assert "DEV_EMAIL_ALLOWLIST" not in shared

    for service_name in ("user-service", "notification-service"):
        service = compose["services"][service_name]
        environment = service["environment"]
        for key, value in shared.items():
            assert environment[key] == value, f"{service_name} differs for {key}"
        assert environment["SMTP_HOST"] == "${DEV_SMTP_HOST:-mailpit}"
        assert environment["SMTP_PORT"] == "${DEV_SMTP_PORT:-1025}"
        assert service["depends_on"]["mailpit"]["condition"] == "service_healthy"


def test_development_services_do_not_consume_production_smtp_credentials() -> None:
    """The development Compose stack maps only DEV_SMTP_* transport values."""
    compose = _compose()
    shared = compose["x-email-environment"]

    assert "${SMTP_HOST" not in str(shared)
    assert "${SMTP_PORT" not in str(shared)
    assert "${SMTP_USER" not in str(shared)
    assert "${SMTP_PASSWORD" not in str(shared)

    for service_name in ("user-service", "notification-service"):
        environment = compose["services"][service_name]["environment"]
        assert environment["SMTP_HOST"] == "${DEV_SMTP_HOST:-mailpit}"
        assert environment["SMTP_USER"] == "${DEV_SMTP_USER:-}"
        assert environment["SMTP_PASSWORD"] == "${DEV_SMTP_PASSWORD:-}"


def test_no_compose_service_defaults_to_a_public_smtp_endpoint() -> None:
    compose = _compose()
    for service_name, service in compose["services"].items():
        environment = service.get("environment", {})
        host = str(environment.get("SMTP_HOST", ""))
        assert host not in {"smtp.example.com", "smtp.gmail.com", "mail.example.com"}, (
            f"{service_name} must not default to a public SMTP endpoint"
        )
