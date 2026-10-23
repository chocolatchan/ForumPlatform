"""Focused tests for internal service authentication and configuration alignment."""

import inspect
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from starlette.requests import Request

import src.services.content.app as content_app
import src.services.content.auth as content_auth
import src.services.interaction.app as interaction_app
import src.services.moderation.app as moderation_app
import src.services.notification.app as notification_app
import src.services.notification.auth as notification_auth
import src.services.search.app as search_app
import src.services.search.auth as search_auth
import src.services.user.app as user_app
import src.services.user.auth as user_auth
from src.common.config import BaseServiceSettings
from src.common.internal_auth import INTERNAL_SERVICE_KEY_HEADER
from src.common.outbox_worker import OutboxWorker
from src.gateway.config import GatewaySettings
from src.gateway.forwarder import filter_headers
from src.gateway.router import GatewayRouter
from src.services.notification import events as notification_events
from src.services.notification.config import notification_settings
from src.services.user.schemas import SetAvatarRequest

TEST_SECRET = os.environ["INTERNAL_SERVICE_SECRET"]
AUTH_MODULES = (
    (user_auth, "user_settings"),
    (content_auth, "content_settings"),
    (search_auth, "search_settings"),
    (notification_auth, "notification_settings"),
)
OUTBOX_PRODUCERS = (
    (user_app, "user_settings"),
    (content_app, "content_settings"),
    (interaction_app, "interaction_settings"),
    (moderation_app, "moderation_settings"),
)
INTERNAL_ROUTE_CASES = (
    (user_app.app, "GET", f"/internal/users/{uuid.uuid4()}"),
    (content_app.app, "POST", f"/internal/attachments/{uuid.uuid4()}/claim-avatar"),
    (content_app.app, "POST", f"/internal/attachments/{uuid.uuid4()}/release-avatar"),
    (content_app.app, "POST", "/internal/events"),
    (notification_app.app, "POST", "/internal/events"),
    (search_app.app, "POST", "/internal/events"),
)
COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def _request(
    headers: dict[str, str] | None = None, cookies: list[tuple[bytes, bytes]] | None = None
):
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/internal/test",
            "raw_path": b"/internal/test",
            "query_string": b"",
            "headers": raw_headers + (cookies or []),
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )


@pytest.mark.parametrize(("auth_module", "settings_name"), AUTH_MODULES)
async def test_missing_header_rejected_when_bypass_disabled(
    auth_module, settings_name, monkeypatch
):
    monkeypatch.setattr(
        auth_module,
        settings_name,
        SimpleNamespace(internal_service_secret=TEST_SECRET, internal_service_auth_bypass=False),
    )

    with pytest.raises(HTTPException) as exc_info:
        await auth_module.verify_internal_service_key(_request())

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Internal service key missing."


@pytest.mark.parametrize(("auth_module", "settings_name"), AUTH_MODULES)
async def test_incorrect_header_rejected_when_bypass_disabled(
    auth_module, settings_name, monkeypatch
):
    monkeypatch.setattr(
        auth_module,
        settings_name,
        SimpleNamespace(internal_service_secret=TEST_SECRET, internal_service_auth_bypass=False),
    )

    with pytest.raises(HTTPException) as exc_info:
        await auth_module.verify_internal_service_key(
            _request({INTERNAL_SERVICE_KEY_HEADER: "incorrect-internal-secret"})
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Internal service key invalid."


@pytest.mark.parametrize(("auth_module", "settings_name"), AUTH_MODULES)
async def test_correct_header_accepted(auth_module, settings_name, monkeypatch):
    monkeypatch.setattr(
        auth_module,
        settings_name,
        SimpleNamespace(internal_service_secret=TEST_SECRET, internal_service_auth_bypass=False),
    )

    await auth_module.verify_internal_service_key(
        _request({INTERNAL_SERVICE_KEY_HEADER: TEST_SECRET})
    )


@pytest.mark.parametrize(("auth_module", "settings_name"), AUTH_MODULES)
async def test_explicit_bypass_allows_missing_header_only(auth_module, settings_name, monkeypatch):
    monkeypatch.setattr(
        auth_module,
        settings_name,
        SimpleNamespace(internal_service_secret=TEST_SECRET, internal_service_auth_bypass=True),
    )

    await auth_module.verify_internal_service_key(_request())

    with pytest.raises(HTTPException) as exc_info:
        await auth_module.verify_internal_service_key(
            _request({INTERNAL_SERVICE_KEY_HEADER: "incorrect-internal-secret"})
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Internal service key invalid."


@pytest.mark.parametrize(("auth_module", "settings_name"), AUTH_MODULES)
async def test_legacy_header_is_not_accepted(auth_module, settings_name, monkeypatch):
    monkeypatch.setattr(
        auth_module,
        settings_name,
        SimpleNamespace(internal_service_secret=TEST_SECRET, internal_service_auth_bypass=False),
    )

    with pytest.raises(HTTPException) as exc_info:
        await auth_module.verify_internal_service_key(_request({"X-Internal-Key": TEST_SECRET}))

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Internal service key missing."


@pytest.mark.parametrize(("auth_module", "settings_name"), AUTH_MODULES)
async def test_receivers_delegate_to_shared_verifier(auth_module, settings_name, monkeypatch):
    settings = SimpleNamespace(
        internal_service_secret=TEST_SECRET,
        internal_service_auth_bypass=False,
    )
    monkeypatch.setattr(auth_module, settings_name, settings)

    with patch.object(auth_module, "verify_internal_service_request") as shared_verifier:
        await auth_module.verify_internal_service_key(_request())

    shared_verifier.assert_called_once()


def test_internal_secret_is_required(monkeypatch):
    monkeypatch.delenv("INTERNAL_SERVICE_SECRET", raising=False)

    with pytest.raises(ValidationError, match="internal_service_secret"):
        BaseServiceSettings(
            database_url="sqlite+aiosqlite:///:memory:",
            _env_file=None,
        )


def test_empty_internal_secret_is_rejected():
    with pytest.raises(ValidationError, match="must not be empty"):
        BaseServiceSettings(
            database_url="sqlite+aiosqlite:///:memory:",
            internal_service_secret="   ",
            _env_file=None,
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_bypass_rejected_outside_development_and_test(environment):
    with pytest.raises(ValidationError, match="INTERNAL_SERVICE_AUTH_BYPASS"):
        BaseServiceSettings(
            environment=environment,
            database_url=(
                "postgresql+asyncpg://strong_user:strong_password_12345@localhost:5432/forum_test"
            ),
            internal_service_secret="a-secure-internal-secret-value-that-is-at-least-32-chars",
            internal_service_auth_bypass=True,
            _env_file=None,
        )


def test_bypass_requires_explicit_environment(monkeypatch):
    """A missing ENVIRONMENT must not inherit the development default for bypass."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    with pytest.raises(ValidationError, match="ENVIRONMENT"):
        BaseServiceSettings(
            database_url="sqlite+aiosqlite:///:memory:",
            internal_service_secret="a-secure-internal-secret-value-that-is-at-least-32-chars",
            internal_service_auth_bypass=True,
            _env_file=None,
        )


@pytest.mark.parametrize("environment", ["development", "test"])
def test_bypass_allowed_only_when_development_or_test_is_explicit(environment):
    settings = BaseServiceSettings(
        environment=environment,
        database_url="sqlite+aiosqlite:///:memory:",
        internal_service_secret="a-secure-internal-secret-value-that-is-at-least-32-chars",
        internal_service_auth_bypass=True,
        _env_file=None,
    )

    assert settings.internal_service_auth_bypass is True
    assert settings.environment == environment


def test_bypass_accepts_explicit_environment_from_process_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", TEST_SECRET)
    monkeypatch.setenv("INTERNAL_SERVICE_AUTH_BYPASS", "true")

    settings = BaseServiceSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        _env_file=None,
    )

    assert settings.internal_service_auth_bypass is True
    assert settings.environment == "test"


def test_legacy_environment_variable_is_not_used(monkeypatch):
    """The legacy key must not satisfy the canonical secret requirement."""
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", "legacy-internal-key")
    monkeypatch.delenv("INTERNAL_SERVICE_SECRET", raising=False)

    with pytest.raises(ValidationError, match="internal_service_secret"):
        BaseServiceSettings(
            database_url="sqlite+aiosqlite:///:memory:",
            _env_file=None,
        )


@pytest.mark.parametrize(
    "example_secret",
    [
        "default-internal-service-secret-key-32chars",
        "default-dev-internal-key",
        "replace-with-secure-internal-secret-min-32-chars",
        "staging-internal-service-secret-must-be-32-chars-long",
    ],
)
def test_documented_example_secrets_rejected_in_staging(example_secret):
    with pytest.raises(
        ValidationError, match="requires a non-default, secure INTERNAL_SERVICE_SECRET"
    ):
        BaseServiceSettings(
            environment="staging",
            database_url=(
                "postgresql+asyncpg://strong_user:strong_password_12345@localhost:5432/forum_test"
            ),
            internal_service_secret=example_secret,
            _env_file=None,
        )


def test_outbox_worker_has_no_default_internal_key():
    parameter = inspect.signature(OutboxWorker).parameters["internal_key"]
    assert parameter.default is inspect.Parameter.empty


def test_outbox_worker_rejects_empty_internal_key():
    with pytest.raises(ValueError, match="non-empty internal service credential"):
        OutboxWorker(
            service_name="test-service",
            db_manager=cast(Any, None),
            model_class=object,
            service_urls={},
            internal_key="   ",
        )


@pytest.mark.asyncio
async def test_outbox_worker_uses_canonical_header():
    worker = OutboxWorker(
        service_name="test-service",
        db_manager=cast(Any, None),
        model_class=object,
        service_urls={},
        internal_key=TEST_SECRET,
    )
    worker._run_loop = AsyncMock()  # type: ignore[method-assign]

    with patch("src.common.outbox_worker.httpx.AsyncClient") as client_class:
        client_instance = AsyncMock()
        client_class.return_value = client_instance

        await worker.start()
        headers = client_class.call_args.kwargs["headers"]
        assert headers[INTERNAL_SERVICE_KEY_HEADER] == TEST_SECRET

        await worker.stop()


@pytest.mark.parametrize(("app_module", "settings_name"), OUTBOX_PRODUCERS)
def test_outbox_producers_wire_configured_internal_secret(app_module, settings_name):
    settings = getattr(app_module, settings_name)
    worker = app_module.outbox_worker

    assert worker.internal_key
    assert worker.internal_key == settings.internal_service_secret


@pytest.mark.asyncio
async def test_notification_to_user_caller_uses_canonical_header(monkeypatch):
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"email": "recipient@example.test"}

    class FakeClient:
        async def get(self, url: str, headers: dict[str, str]):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(notification_events, "get_auth_http_client", lambda: FakeClient())
    user_id = uuid.uuid4()

    email = await notification_events.EventHandlerRegistry._fetch_recipient_email(user_id)

    assert email == "recipient@example.test"
    assert captured["url"] == (
        f"{notification_settings.user_service_url.rstrip('/')}/internal/users/{user_id}"
    )
    assert captured["headers"] == {
        INTERNAL_SERVICE_KEY_HEADER: notification_settings.internal_service_secret
    }


@pytest.mark.asyncio
async def test_user_avatar_caller_uses_canonical_header(monkeypatch):
    captured: dict[str, Any] = {}
    image_id = uuid.uuid4()

    class FakeResponse:
        status_code = 200

    class FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, headers: dict[str, str], json: dict[str, str] | None = None):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse()

    class FakeResult:
        def scalar_one(self):
            return SimpleNamespace(
                id=uuid.uuid4(),
                avatar_object_key=None,
                username="tester",
                display_name="Tester",
                bio=None,
                email="tester@example.test",
                role="member",
                created_at=None,
            )

    class FakeSession:
        async def execute(self, query):
            return FakeResult()

    class FakeTransaction:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeDatabase:
        def transaction(self):
            return FakeTransaction()

    monkeypatch.setattr(user_app, "user_db", FakeDatabase())
    monkeypatch.setattr(user_app.httpx, "AsyncClient", FakeAsyncClient)

    context = SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4()))
    response = await user_app.set_avatar(
        req=SetAvatarRequest(image_id=image_id),
        request=_request(),
        ctx=context,  # type: ignore[arg-type]
    )

    assert response.status_code == 200
    assert captured["url"] == f"/internal/attachments/{image_id}/claim-avatar"
    assert captured["headers"] == {
        INTERNAL_SERVICE_KEY_HEADER: user_app.user_settings.internal_service_secret
    }


@pytest.mark.asyncio
async def test_user_avatar_release_caller_uses_canonical_header(monkeypatch):
    captured: dict[str, Any] = {}
    image_id = uuid.uuid4()

    class FakeResponse:
        status_code = 204

    class FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, headers: dict[str, str]):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse()

    class FakeResult:
        def scalar_one(self):
            return SimpleNamespace(id=uuid.uuid4(), avatar_object_key=str(image_id))

    class FakeSession:
        async def execute(self, query):
            return FakeResult()

    class FakeTransaction:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeDatabase:
        def transaction(self):
            return FakeTransaction()

    monkeypatch.setattr(user_app, "user_db", FakeDatabase())
    monkeypatch.setattr(user_app.httpx, "AsyncClient", FakeAsyncClient)

    context = SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4()))
    response = await user_app.delete_avatar(
        request=_request(),
        ctx=context,  # type: ignore[arg-type]
    )

    assert response.status_code == 204
    assert captured["url"] == f"/internal/attachments/{image_id}/release-avatar"
    assert captured["headers"] == {
        INTERNAL_SERVICE_KEY_HEADER: user_app.user_settings.internal_service_secret
    }


@pytest.mark.asyncio
async def test_user_internal_route_requires_internal_header():
    transport = ASGITransport(app=user_app.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.get(f"/internal/users/{uuid.uuid4()}")
        invalid = await client.get(
            f"/internal/users/{uuid.uuid4()}",
            headers={INTERNAL_SERVICE_KEY_HEADER: "incorrect-internal-secret"},
        )

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "UNAUTHORIZED"
    assert missing.json()["error"]["message"] == "Internal service key missing."
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "UNAUTHORIZED"
    assert invalid.json()["error"]["message"] == "Internal service key invalid."


@pytest.mark.parametrize(("app", "method", "path"), INTERNAL_ROUTE_CASES)
async def test_all_internal_receivers_reject_missing_invalid_and_legacy_headers(
    app, method, path
):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.request(method, path, json={})
        invalid = await client.request(
            method,
            path,
            json={},
            headers={INTERNAL_SERVICE_KEY_HEADER: "incorrect-internal-secret"},
        )
        legacy = await client.request(
            method,
            path,
            json={},
            headers={"X-Internal-Key": TEST_SECRET},
        )

    assert missing.status_code == 401
    assert missing.json()["error"]["message"] == "Internal service key missing."
    assert invalid.status_code == 401
    assert invalid.json()["error"]["message"] == "Internal service key invalid."
    assert legacy.status_code == 401
    assert legacy.json()["error"]["message"] == "Internal service key missing."


def test_gateway_strips_external_internal_service_header():
    forwarded = filter_headers(
        {INTERNAL_SERVICE_KEY_HEADER: TEST_SECRET, "Accept": "application/json"}
    )

    assert forwarded == {"Accept": "application/json"}


@pytest.mark.parametrize(
    "path",
    [
        "/internal/events",
        "internal/events",
        "/internal/users/00000000-0000-0000-0000-000000000000",
        "/internal/attachments/00000000-0000-0000-0000-000000000000/claim-avatar",
    ],
)
def test_gateway_never_routes_internal_paths(path):
    router = GatewayRouter(GatewaySettings(_env_file=None))

    assert router.resolve(path) is None


def test_compose_requires_canonical_internal_secret_for_all_services():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = compose["services"]

    for service_name in (
        "user-service",
        "content-service",
        "interaction-service",
        "moderation-service",
        "notification-service",
        "search-service",
    ):
        environment = services[service_name]["environment"]
        secret = environment["INTERNAL_SERVICE_SECRET"]

        assert secret.startswith("${INTERNAL_SERVICE_SECRET:?")
        assert environment["INTERNAL_SERVICE_AUTH_BYPASS"] == (
            "${INTERNAL_SERVICE_AUTH_BYPASS:-false}"
        )
        assert "INTERNAL_SERVICE_KEY" not in environment


@pytest.mark.asyncio
async def test_public_session_auth_still_requires_cookie():
    with pytest.raises(HTTPException) as exc_info:
        await user_auth.get_current_session(_request())

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_public_csrf_auth_still_requires_token():
    request = _request(cookies=[(b"cookie", b"forum_session=session-token")])

    with pytest.raises(HTTPException) as exc_info:
        await user_auth.verify_csrf_header(request)

    assert exc_info.value.status_code == 403
