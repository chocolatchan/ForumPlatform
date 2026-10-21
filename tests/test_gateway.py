"""Tests for Gateway routing and forwarder functionality."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

import src.gateway.app as gateway_app_module
from src.gateway.app import app
from src.gateway.config import GatewaySettings
from src.gateway.router import GatewayRouter


def test_gateway_router_routing():
    settings = GatewaySettings()
    router = GatewayRouter(settings)

    # Auth & Profile
    r_auth = router.resolve("auth/login")
    assert r_auth is not None
    assert r_auth.target_base_url == "http://localhost:8001"
    assert r_auth.upstream_path == "/auth/login"

    r_me = router.resolve("users/me")
    assert r_me is not None
    assert r_me.target_base_url == "http://localhost:8001"
    assert r_me.upstream_path == "/users/me"

    # Content
    r_threads = router.resolve("threads")
    assert r_threads is not None
    assert r_threads.target_base_url == "http://localhost:8002"
    assert r_threads.upstream_path == "/threads"

    r_cat = router.resolve("categories")
    assert r_cat is not None
    assert r_cat.target_base_url == "http://localhost:8002"

    # Interactions sub-routes (takes priority over /threads and /profile)
    r_vote = router.resolve("threads/123/vote")
    assert r_vote is not None
    assert r_vote.target_base_url == "http://localhost:8003"
    assert r_vote.upstream_path == "/threads/123/vote"

    r_bm = router.resolve("profile/bookmarks")
    assert r_bm is not None
    assert r_bm.target_base_url == "http://localhost:8003"

    # Moderation
    r_rep = router.resolve("reports")
    assert r_rep is not None
    assert r_rep.target_base_url == "http://localhost:8004"

    # Notifications
    r_notif = router.resolve("notifications")
    assert r_notif is not None
    assert r_notif.target_base_url == "http://localhost:8005"

    # Search
    r_search = router.resolve("search")
    assert r_search is not None
    assert r_search.target_base_url == "http://localhost:8006"

    # Unknown
    assert router.resolve("nonexistent_unknown_service") is None


@pytest.mark.asyncio
async def test_gateway_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health/live")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_gateway_route_not_found():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/nonexistent_prefix_path")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "ROUTE_NOT_FOUND"


@pytest.mark.asyncio
async def test_gateway_upstream_timeout():
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.build_request.return_value = httpx.Request("GET", "http://localhost:8002/threads")
    mock_client.send.side_effect = httpx.TimeoutException("Upstream timed out")

    transport = ASGITransport(app=app)
    with patch.object(gateway_app_module, "http_client", mock_client):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/threads")
            assert resp.status_code == 504
            data = resp.json()
            assert data["error"]["code"] == "UPSTREAM_TIMEOUT"
            assert "request_id" in data["error"]


@pytest.mark.asyncio
async def test_gateway_upstream_unavailable():
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.build_request.return_value = httpx.Request("GET", "http://localhost:8002/threads")
    mock_client.send.side_effect = httpx.ConnectError("Connection refused")

    transport = ASGITransport(app=app)
    with patch.object(gateway_app_module, "http_client", mock_client):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/threads")
            assert resp.status_code == 503
            data = resp.json()
            assert data["error"]["code"] == "UPSTREAM_UNAVAILABLE"
            assert "request_id" in data["error"]
