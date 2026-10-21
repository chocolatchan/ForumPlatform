"""Asynchronous HTTP request forwarder with streaming and error handling."""

import logging
import uuid
from collections.abc import AsyncGenerator

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

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


async def forward_request(
    client: httpx.AsyncClient,
    request: Request,
    target_url: str,
    request_id: str,
) -> Response:
    """Stream an incoming HTTP request to upstream service and return response."""
    outgoing_headers = filter_headers(dict(request.headers))
    outgoing_headers["X-Request-ID"] = request_id
    # Read incoming request body
    body = await request.body()

    try:
        upstream_req = client.build_request(
            method=request.method,
            url=target_url,
            headers=outgoing_headers,
            params=request.query_params,
            content=body,
        )

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

    except httpx.TimeoutException as e:
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
