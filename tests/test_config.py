"""Tests for common configuration and settings validation."""

import pytest
from pydantic import ValidationError

from src.common.config import BaseServiceSettings
from src.gateway.config import GatewaySettings
from src.services.content.config import ContentSettings
from src.services.user.config import UserSettings


def test_base_service_settings_url_transformation(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/forum_users")
    settings = BaseServiceSettings(database_url="postgresql://user:pass@localhost:5432/forum_users")
    assert str(settings.database_url).startswith("postgresql+asyncpg://")


def test_base_service_settings_invalid_scheme(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "mysql://user:pass@localhost:3306/db")
    with pytest.raises(ValidationError):
        BaseServiceSettings(database_url="mysql://user:pass@localhost:3306/db")


def test_user_settings_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/forum_users")
    settings = UserSettings()
    assert settings.service_name == "user-service"
    assert settings.port == 8001
    assert str(settings.database_url).startswith("postgresql+asyncpg://")


def test_content_settings_custom_port(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/forum_content"
    )
    monkeypatch.setenv("PORT", "9002")
    settings = ContentSettings()
    assert settings.port == 9002
    assert settings.service_name == "content-service"


def test_gateway_settings_defaults():
    settings = GatewaySettings()
    assert settings.port == 8000
    assert settings.user_service_url == "http://localhost:8001"
    assert settings.content_service_url == "http://localhost:8002"
    assert settings.interaction_service_url == "http://localhost:8003"
    assert settings.moderation_service_url == "http://localhost:8004"
    assert settings.notification_service_url == "http://localhost:8005"
    assert settings.search_service_url == "http://localhost:8006"
