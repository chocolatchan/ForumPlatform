"""Tests for Tier I Swagger UI, OpenAPI spec, routing, validation, and 501 responses."""

from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

import src.gateway.app as gateway_app_module
from src.gateway.app import app as gateway_app
from src.services.content.app import app as content_app
from src.services.interaction.app import app as interaction_app
from src.services.moderation.app import app as moderation_app
from src.services.notification.app import app as notification_app
from src.services.search.app import app as search_app
from src.services.user.app import app as user_app


@pytest.mark.asyncio
async def test_gateway_swagger_docs_and_openapi_json():
    """Verify Gateway exposes Swagger UI at /docs and canonical spec at /openapi.json."""
    transport = ASGITransport(app=gateway_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Swagger UI HTML
        res_docs = await client.get("/docs")
        assert res_docs.status_code == 200
        assert "swagger-ui" in res_docs.text

        # OpenAPI JSON
        res_json = await client.get("/openapi.json")
        assert res_json.status_code == 200
        spec = res_json.json()
        assert spec["openapi"] == "3.1.0"
        assert spec["info"]["title"] == "Forum Platform Public API"
        assert len(spec["paths"]) >= 48
        # Server URL points to Gateway
        assert any("localhost:8000" in s["url"] for s in spec["servers"])


@pytest.mark.asyncio
async def test_gateway_routes_to_all_six_services():
    """Verify Gateway successfully routes to each of the six services via forwarder."""
    # Build a simulated multi-service transport map
    service_apps = {
        "http://localhost:8001": user_app,
        "http://localhost:8002": content_app,
        "http://localhost:8003": interaction_app,
        "http://localhost:8004": moderation_app,
        "http://localhost:8005": notification_app,
        "http://localhost:8006": search_app,
    }

    transport = ASGITransport(app=gateway_app)
    mock_client = AsyncClient(
        transport=ASGITransport(app=user_app), base_url="http://localhost:8001"
    )

    async def mock_forward_send(request, **kwargs):
        # Decode host
        host = (
            request.url.netloc.decode()
            if isinstance(request.url.netloc, bytes)
            else request.url.netloc
        )
        base_url = f"{request.url.scheme}://{host}"
        target_app = service_apps.get(base_url)
        if not target_app:
            raise AssertionError(f"Unknown upstream base URL: {base_url}")

        # Stream reading
        body_bytes = b""
        if hasattr(request.stream, "__aiter__"):
            async for chunk in request.stream:
                body_bytes += chunk
        elif hasattr(request, "read"):
            body_bytes = request.read()

        # In offline unit test without PostgreSQL daemon, mock responses for apps requiring live DB
        if target_app == content_app and request.url.path == "/threads":
            content_bytes = b'{"items": [], "next_cursor": null}'
            return httpx.Response(
                status_code=200,
                headers={"content-type": "application/json"},
                stream=httpx.ByteStream(content_bytes),
                request=request,
            )
        if target_app == interaction_app and "/reputation" in request.url.path:
            content_bytes = (
                b'{"user_id": "00000000-0000-0000-0000-000000000001", "reputation_score": 0}'
            )
            return httpx.Response(
                status_code=200,
                headers={"content-type": "application/json"},
                stream=httpx.ByteStream(content_bytes),
                request=request,
            )
        if target_app == moderation_app and request.url.path == "/moderation/actions":
            content_bytes = b'{"items": [], "next_cursor": null}'
            return httpx.Response(
                status_code=200,
                headers={"content-type": "application/json"},
                stream=httpx.ByteStream(content_bytes),
                request=request,
            )
        if target_app == search_app and request.url.path == "/search":
            content_bytes = b'{"items": [], "next_cursor": null}'
            return httpx.Response(
                status_code=200,
                headers={"content-type": "application/json"},
                stream=httpx.ByteStream(content_bytes),
                request=request,
            )

        async with AsyncClient(
            transport=ASGITransport(app=target_app), base_url=base_url
        ) as svc_client:
            # Recreate request inside service
            resp = await svc_client.request(
                method=request.method,
                url=str(request.url),
                headers=dict(request.headers),
                content=body_bytes,
            )
            # Create a fresh unconsumed streaming response with ByteStream
            return httpx.Response(
                status_code=resp.status_code,
                headers=resp.headers,
                stream=httpx.ByteStream(resp.content),
                request=request,
            )

    mock_client.send = mock_forward_send

    with patch.object(gateway_app_module, "http_client", mock_client):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. User Service: GET /api/v1/auth/csrf -> 200
            res_user = await client.get("/api/v1/auth/csrf")
            assert res_user.status_code == 200
            assert "csrf_token" in res_user.json()

            # 2. Content Service: GET /api/v1/threads -> 200 (implemented in Task 008)
            res_content = await client.get("/api/v1/threads")
            assert res_content.status_code == 200
            assert "items" in res_content.json()

            # 3. Interaction Service: GET /api/v1/users/00000000-0000-0000-0000-000000000001/reputation -> 200
            res_interaction = await client.get(
                "/api/v1/users/00000000-0000-0000-0000-000000000001/reputation"
            )
            assert res_interaction.status_code == 200
            assert res_interaction.json()["reputation_score"] == 0

            # 4. Moderation Service: GET /api/v1/moderation/actions -> 200 with mod identity headers
            res_mod = await client.get(
                "/api/v1/moderation/actions",
                headers={
                    "X-Forwarded-User-ID": "00000000-0000-0000-0000-000000000001",
                    "X-Forwarded-Role": "moderator",
                    "X-Forwarded-Username": "test_moderator",
                },
            )
            assert res_mod.status_code == 200
            assert "items" in res_mod.json()

            # 5. Notification Service: GET /api/v1/notifications/unread-count -> 200
            res_notif = await client.get("/api/v1/notifications/unread-count")
            assert res_notif.status_code == 200
            assert res_notif.json() == {"unread_count": 0}

            # 6. Search Service: GET /api/v1/search?q=test -> 200
            res_search = await client.get("/api/v1/search?q=test")
            assert res_search.status_code == 200
            assert "items" in res_search.json()


@pytest.mark.asyncio
async def test_unimplemented_endpoints_return_501_not_implemented():
    """Verify unimplemented business endpoints return controlled 501 with canonical Error schema."""
    transport_user = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport_user, base_url="http://test") as client:
        # POST /auth/recovery/request with valid schema -> 501 (recovery not yet implemented)
        res = await client.post(
            "/auth/recovery/request",
            json={
                "email": "valid_user@example.com",
            },
        )
        assert res.status_code == 501
        data = res.json()
        assert data["error"]["code"] == "NOT_IMPLEMENTED"
        assert "POST /auth/recovery/request" in data["error"]["message"]

    transport_content = ASGITransport(app=content_app)
    async with AsyncClient(transport=transport_content, base_url="http://test") as client:
        # POST /uploads/images -> 501 (images upload not implemented)
        res = await client.post("/uploads/images")
        assert res.status_code == 501
        data = res.json()
        assert data["error"]["code"] == "NOT_IMPLEMENTED"
        assert "POST /uploads/images" in data["error"]["message"]


@pytest.mark.asyncio
async def test_request_validation_failures_return_canonical_422():
    """Verify invalid payloads produce 422 with canonical Error schema details."""
    transport_user = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport_user, base_url="http://test") as client:
        # Invalid username (too short) and invalid password (too short)
        res = await client.post(
            "/auth/register",
            json={"username": "ab", "email": "not-an-email", "password": "short"},
        )
        assert res.status_code == 422
        data = res.json()
        assert data["error"]["code"] == "VALIDATION_ERROR"
        assert len(data["error"]["details"]) >= 2
        fields = [d["field"] for d in data["error"]["details"]]
        assert "username" in fields or "password" in fields

    transport_interaction = ASGITransport(app=interaction_app)
    async with AsyncClient(transport=transport_interaction, base_url="http://test") as client:
        # Invalid vote value (0 instead of 1 or -1) with test identity headers
        test_headers = {
            "X-Forwarded-User-ID": "00000000-0000-0000-0000-000000000001",
            "X-Forwarded-Role": "member",
            "X-Forwarded-Username": "test_member",
        }
        res = await client.put(
            "/threads/00000000-0000-0000-0000-000000000001/vote",
            json={"value": 0},
            headers=test_headers,
        )
        assert res.status_code == 422
        data = res.json()
        assert data["error"]["code"] == "VALIDATION_ERROR"
