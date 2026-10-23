"""Tests for Gateway routing and forwarder functionality."""

from collections.abc import AsyncIterator
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import ClientDisconnect, Request

import src.gateway.app as gateway_app_module
from src.gateway.app import app
from src.gateway.config import GatewaySettings
from src.gateway.forwarder import (
    MAX_GATEWAY_BODY_SIZE_BYTES,
    forward_request,
)
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

    # Test-only routes are not part of the public gateway surface.
    assert router.resolve("test/otp") is None
    assert router.resolve("test/reset-rate-limiters") is None


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
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/test/reset-rate-limiters"),
        ("GET", "/api/v1/test/otp?recipient=test-route@example.test"),
    ],
)
async def test_gateway_does_not_proxy_test_only_routes(method: str, path: str):
    """Verify test-only service routes are not reachable through the public gateway."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.request(method, path)
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


# ---------------------------------------------------------------------------
# Task 027-H1-R2: request-body streaming regression tests
# ---------------------------------------------------------------------------


class _AsyncBody(httpx.AsyncByteStream):
    def __init__(self, data: bytes = b"upstream-ok") -> None:
        self._data = data

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._data


def _make_request(
    chunks: list[bytes],
    headers: dict[str, str] | None = None,
    receive_error: Exception | None = None,
) -> tuple[Request, dict[str, Any]]:
    state: dict[str, Any] = {"finished": False, "produced": 0}
    index = 0

    async def receive() -> dict[str, Any]:
        nonlocal index
        if receive_error is not None and index > 0:
            raise receive_error
        if index >= len(chunks):
            return {"type": "http.disconnect"}
        chunk = chunks[index]
        index += 1
        state["produced"] = index
        if index == len(chunks):
            state["finished"] = True
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks),
        }

    raw_headers = []
    header_keys = {key.lower() for key in (headers or {})}
    if "content-type" not in header_keys:
        raw_headers.append((b"content-type", b"application/octet-stream"))
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode(), value.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/uploads/images",
        "raw_path": b"/api/v1/uploads/images",
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope, receive=receive), state


class _StreamingClient(httpx.AsyncClient):
    """HTTPX client whose send() forwards the request stream verbatim.

    Unlike MockTransport (which calls ``request.aread()`` first), this exercises
    the actual HTTPX request construction and streaming path without buffering.
    """

    def __init__(self, seen: dict[str, Any], fail_with: Exception | None = None) -> None:
        super().__init__()
        self.seen = seen
        self.fail_with = fail_with
        self.captured: list[bytes] = []

    async def send(self, request: httpx.Request, *, stream: bool = False, **kwargs: object):
        self.seen["content_length_headers"] = request.headers.get_list("content-length")
        self.seen["transfer_encoding"] = request.headers.get("transfer-encoding")
        self.seen["headers"] = dict(request.headers)
        self.seen["first_chunk_state"] = None
        async for chunk in cast(AsyncIterator[bytes], request.stream):
            if self.seen["first_chunk_state"] is None:
                self.seen["first_chunk_state"] = dict(self.seen["receive_state"])
            self.captured.append(chunk)
        if self.fail_with is not None:
            raise self.fail_with
        return httpx.Response(200, stream=_AsyncBody(), request=request)


def _calling_client(seen: dict[str, Any], fail_with: Exception | None = None):
    client = _StreamingClient(seen, fail_with)
    return client, client.captured


async def _response_body(response: Any) -> bytes:
    if hasattr(response, "body_iterator"):
        return b"".join([chunk async for chunk in response.body_iterator])
    return bytes(response.body)


@pytest.mark.asyncio
async def test_gateway_streams_request_body_without_buffering():
    chunks = [b"a" * 1024, b"b" * 1024, b"c" * 1024]
    request, state = _make_request(chunks)
    seen: dict[str, Any] = {"receive_state": state}
    client, captured = _calling_client(seen)

    try:
        response = await forward_request(client, request, "http://upstream/test", "rid-stream")
        assert response.status_code == 200
        assert await _response_body(response) == b"upstream-ok"
    finally:
        await client.aclose()

    # Upstream saw the first chunk while the receive callable had not finished.
    assert seen["first_chunk_state"] is not None
    assert seen["first_chunk_state"]["finished"] is False
    assert b"".join(captured) == b"".join(chunks)
    # Stale framing is removed; HTTPX recomputes chunked framing.
    assert seen["content_length_headers"] == []
    assert (seen["transfer_encoding"] or "").lower() == "chunked"


@pytest.mark.asyncio
async def test_gateway_forwards_multipart_headers_and_cookies():
    boundary = "----forum-boundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="x.png"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode() + b"\x89PNG\r\n\x1a\n" + f"\r\n--{boundary}--\r\n".encode()
    headers = {
        "Cookie": "forum_session=session-token",
        "X-CSRF-Token": "csrf-token",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    request, state = _make_request([body], headers=headers)
    seen: dict[str, Any] = {"receive_state": state}
    client, captured = _calling_client(seen)

    try:
        response = await forward_request(client, request, "http://upstream/threads", "rid-mp")
        assert response.status_code == 200
        await _response_body(response)
    finally:
        await client.aclose()

    forwarded = seen["headers"]
    assert forwarded["cookie"] == "forum_session=session-token"
    assert forwarded["x-csrf-token"] == "csrf-token"
    assert forwarded["content-type"] == f"multipart/form-data; boundary={boundary}"
    assert b"".join(captured) == body


@pytest.mark.asyncio
async def test_gateway_rejects_declared_oversize_content_length_without_reading():
    request, state = _make_request(
        [b"x"], headers={"Content-Length": str(MAX_GATEWAY_BODY_SIZE_BYTES + 1)}
    )
    seen: dict[str, Any] = {"receive_state": state}
    client, captured = _calling_client(seen)

    try:
        response = await forward_request(client, request, "http://upstream/test", "rid-cl")
    finally:
        await client.aclose()

    assert response.status_code == 413
    assert response.body
    assert state["produced"] == 0
    assert captured == []


@pytest.mark.asyncio
async def test_gateway_rejects_missing_content_length_when_stream_exceeds_limit():
    chunk = b"x" * (1024 * 1024)
    chunks = [chunk] * 5 + [b"y" * 16]
    request, state = _make_request(chunks)
    seen: dict[str, Any] = {"receive_state": state}
    client, captured = _calling_client(seen)

    try:
        response = await forward_request(client, request, "http://upstream/test", "rid-chunked")
    finally:
        await client.aclose()

    assert response.status_code == 413
    # Upstream may have received a prefix, but never the complete oversize body.
    assert sum(len(c) for c in captured) <= MAX_GATEWAY_BODY_SIZE_BYTES


@pytest.mark.asyncio
async def test_gateway_ignores_misleading_small_content_length():
    chunk = b"x" * (1024 * 1024)
    request, state = _make_request(
        [chunk] * 5 + [b"y" * 16], headers={"Content-Length": "10"}
    )
    seen: dict[str, Any] = {"receive_state": state}
    client, captured = _calling_client(seen)

    try:
        response = await forward_request(client, request, "http://upstream/test", "rid-lie")
    finally:
        await client.aclose()

    assert response.status_code == 413
    assert sum(len(c) for c in captured) <= MAX_GATEWAY_BODY_SIZE_BYTES


@pytest.mark.asyncio
async def test_gateway_accepts_exactly_the_limit():
    half = b"x" * (MAX_GATEWAY_BODY_SIZE_BYTES // 2)
    chunks = [half, half]
    request, state = _make_request(chunks)
    seen: dict[str, Any] = {"receive_state": state}
    client, captured = _calling_client(seen)

    try:
        response = await forward_request(client, request, "http://upstream/test", "rid-limit")
        assert response.status_code == 200
        await _response_body(response)
    finally:
        await client.aclose()

    assert sum(len(c) for c in captured) == MAX_GATEWAY_BODY_SIZE_BYTES


@pytest.mark.asyncio
async def test_gateway_client_disconnect_is_handled_without_leaking():
    request, state = _make_request([b"first", b"second"], receive_error=ClientDisconnect())
    seen: dict[str, Any] = {"receive_state": state}
    client, _ = _calling_client(seen)

    try:
        response = await forward_request(client, request, "http://upstream/test", "rid-dc")
    finally:
        await client.aclose()

    assert response.status_code == 499


@pytest.mark.asyncio
async def test_gateway_upstream_timeout_during_stream_is_not_retried():
    request, state = _make_request([b"a", b"b"])
    seen: dict[str, Any] = {"receive_state": state}
    client, captured = _calling_client(seen, fail_with=httpx.ReadTimeout("slow upstream"))

    try:
        response = await forward_request(client, request, "http://upstream/test", "rid-to")
    finally:
        await client.aclose()

    assert response.status_code == 504
    assert b"".join(captured) == b"ab"
