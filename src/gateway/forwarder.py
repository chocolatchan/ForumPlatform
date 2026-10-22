"""Asynchronous HTTP request forwarder with streaming and error handling."""

import contextlib
import logging
import uuid
from collections.abc import AsyncGenerator

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.requests import ClientDisconnect

logger = logging.getLogger(__name__)

# RFC 7230 Hop-by-hop headers to filter
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
}


def get_or_generate_request_id(request: Request) -> str:
    """Extract or generate a canonical X-Request-ID."""
    req_id = request.headers.get("X-Request-ID")
    if not req_id or len(req_id) > 128:
        return str(uuid.uuid4())
    return req_id


# Untrusted client identity headers to strip to prevent spoofing
UNTRUSTED_CLIENT_HEADERS = {
    "x-authenticated-user-id",
    "x-authenticated-role",
    "x-user-id",
    "x-role",
    "x-forwarded-user-id",
    "x-forwarded-role",
    "x-forwarded-username",
    "x-internal-service-key",
}


def filter_headers(headers: httpx.Headers | dict[str, str]) -> dict[str, str]:
    """Remove hop-by-hop and spoofable client identity headers from forwarding."""
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS and k.lower() not in UNTRUSTED_CLIENT_HEADERS
    }


# Maximum request body size accepted by API Gateway (5 MB)
# Allows 2 MB image file payload + multipart boundary/headers + JSON payloads
MAX_GATEWAY_BODY_SIZE_BYTES = 5242880


class GatewayPayloadTooLargeError(Exception):
    """Raised by the request-body iterator when the cumulative limit is crossed."""


def _payload_too_large_response(request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        headers={"X-Request-ID": request_id},
        content={
            "error": {
                "code": "PAYLOAD_TOO_LARGE",
                "message": (
                    "Request body exceeds maximum gateway limit of "
                    f"{MAX_GATEWAY_BODY_SIZE_BYTES} bytes (5 MB)."
                ),
                "details": [],
                "request_id": request_id,
            }
        },
    )


def _declared_content_length(request: Request) -> int | None:
    """Parse Content-Length for an early rejection hint only.

    The value is never trusted for admission: the streamed byte count is the
    authoritative limit. Invalid/negative values are ignored so a misleading
    header cannot bypass the cumulative check.
    """
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


async def _stream_request_body(
    request: Request, limit: int
) -> AsyncGenerator[bytes, None]:
    """Yield incoming request chunks while enforcing a cumulative ceiling.

    Nothing is accumulated in memory. If the limit is crossed after upstream
    transmission has begun, the raised error aborts the HTTPX request stream;
    the upstream service therefore never receives a complete oversize body.
    """
    total_received = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total_received += len(chunk)
        if total_received > limit:
            raise GatewayPayloadTooLargeError
        yield chunk


async def _close_request_stream(upstream_req: httpx.Request) -> None:
    """Close a partially transmitted HTTPX request without masking the cause."""
    with contextlib.suppress(Exception):
        stream = getattr(upstream_req, "stream", None)
        if stream is not None and hasattr(stream, "aclose"):
            await stream.aclose()


async def forward_request(
    client: httpx.AsyncClient,
    request: Request,
    target_url: str,
    request_id: str,
) -> Response:
    """Stream an incoming HTTP request to upstream service and return response.

    The request body is forwarded as an asynchronous iterator consumed by
    HTTPX. It is never joined into a single bytes object. HTTPX recomputes
    framing and sends ``Transfer-Encoding: chunked`` because the stale
    client-supplied Content-Length header is removed.
    """
    outgoing_headers = filter_headers(dict(request.headers))
    # Framing belongs to HTTPX once the body becomes a stream. Keeping a
    # client-supplied length would either be wrong or force buffering.
    outgoing_headers = {
        k: v for k, v in outgoing_headers.items() if k.lower() != "content-length"
    }
    outgoing_headers["X-Request-ID"] = request_id

    # 1. Early rejection only when the declared length is already too large.
    declared_length = _declared_content_length(request)
    if declared_length is not None and declared_length > MAX_GATEWAY_BODY_SIZE_BYTES:
        return _payload_too_large_response(request_id)

    try:
        upstream_req = client.build_request(
            method=request.method,
            url=target_url,
            headers=outgoing_headers,
            params=request.query_params,
            content=_stream_request_body(request, MAX_GATEWAY_BODY_SIZE_BYTES),
        )

        # 2. Body is pulled lazily by HTTPX during transmission.
        upstream_resp = await client.send(upstream_req, stream=True)

        response_headers = filter_headers(upstream_resp.headers)

        async def body_iterator() -> AsyncGenerator[bytes, None]:
            try:
                async for chunk in upstream_resp.aiter_raw():
                    yield chunk
            finally:
                await upstream_resp.aclose()

        response = StreamingResponse(
            body_iterator(),
            status_code=upstream_resp.status_code,
            headers=response_headers,
        )

        # Preserve multi-valued headers such as Set-Cookie explicitly
        set_cookie_headers = upstream_resp.headers.get_list("set-cookie")
        if set_cookie_headers:
            for sc in set_cookie_headers:
                response.raw_headers.append((b"set-cookie", sc.encode("latin-1")))

        return response

    except GatewayPayloadTooLargeError:
        # The request may already be partially on the wire; abort it. The body
        # is not retried, and no upstream response is forwarded.
        if "upstream_req" in locals():
            await _close_request_stream(upstream_req)
        logger.warning("[%s] Request body exceeded gateway limit while streaming", request_id)
        return _payload_too_large_response(request_id)
    except ClientDisconnect:
        if "upstream_req" in locals():
            await _close_request_stream(upstream_req)
        logger.info("[%s] Client disconnected while streaming request body", request_id)
        return JSONResponse(
            status_code=499,
            headers={"X-Request-ID": request_id},
            content={
                "error": {
                    "code": "CLIENT_DISCONNECTED",
                    "message": "Client disconnected before the request completed.",
                    "details": [],
                    "request_id": request_id,
                }
            },
        )
    except httpx.TimeoutException as e:
        if "upstream_req" in locals():
            await _close_request_stream(upstream_req)
        logger.warning("[%s] Upstream timeout at %s: %s", request_id, target_url, e)
        return JSONResponse(
            status_code=504,
            headers={"X-Request-ID": request_id},
            content={
                "error": {
                    "code": "UPSTREAM_TIMEOUT",
                    "message": "The upstream service timed out.",
                    "details": [],
                    "request_id": request_id,
                }
            },
        )
    except (httpx.ConnectError, httpx.NetworkError) as e:
        if "upstream_req" in locals():
            await _close_request_stream(upstream_req)
        logger.warning("[%s] Upstream connection failed at %s: %s", request_id, target_url, e)
        return JSONResponse(
            status_code=503,
            headers={"X-Request-ID": request_id},
            content={
                "error": {
                    "code": "UPSTREAM_UNAVAILABLE",
                    "message": "The upstream service is temporarily unavailable.",
                    "details": [],
                    "request_id": request_id,
                }
            },
        )
    except Exception as e:
        if "upstream_req" in locals():
            await _close_request_stream(upstream_req)
        logger.error(
            "[%s] Unexpected gateway forwarding error at %s: %s",
            request_id,
            target_url,
            e,
            exc_info=True,
        )
        return JSONResponse(
            status_code=502,
            headers={"X-Request-ID": request_id},
            content={
                "error": {
                    "code": "BAD_GATEWAY",
                    "message": "Gateway failed to forward request.",
                    "details": [],
                    "request_id": request_id,
                }
            },
        )
