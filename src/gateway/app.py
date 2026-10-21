"""API Gateway FastAPI application entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from http.cookiejar import CookieJar
from pathlib import Path

import httpx
import yaml
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.common.logging import configure_logging
from src.common.metrics import MetricsRegistry, setup_metrics_endpoint
from src.gateway.config import gateway_settings
from src.gateway.forwarder import forward_request, get_or_generate_request_id
from src.gateway.router import GatewayRouter

configure_logging(gateway_settings.service_name, gateway_settings.log_level)
metrics_registry = MetricsRegistry(gateway_settings.service_name)

OPENAPI_SPEC_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "forum_public_api.openapi.yaml"
)
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
_cached_openapi: dict | None = None


def load_canonical_openapi() -> dict:
    global _cached_openapi
    if _cached_openapi is None and OPENAPI_SPEC_PATH.is_file():
        _cached_openapi = yaml.safe_load(OPENAPI_SPEC_PATH.read_text(encoding="utf-8"))
    if _cached_openapi is None:
        _cached_openapi = {
            "openapi": "3.1.0",
            "info": {"title": "Forum Platform Public API", "version": "1.1.0"},
        }
    return _cached_openapi


class StatelessCookieJar(CookieJar):
    """Stateless cookie jar to prevent cross-request cookie leakage on shared gateway client."""

    def extract_cookies(self, response: object, request: object) -> None:
        pass

    def set_cookie(self, cookie: object) -> None:
        pass


# Shared HTTP client for connection pooling
http_client: httpx.AsyncClient | None = None
router = GatewayRouter(gateway_settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global http_client
    limits = httpx.Limits(max_keepalive_connections=50, max_connections=200)
    timeouts = httpx.Timeout(
        connect=gateway_settings.connect_timeout_seconds,
        read=gateway_settings.request_timeout_seconds,
        write=gateway_settings.request_timeout_seconds,
        pool=5.0,
    )
    http_client = httpx.AsyncClient(limits=limits, timeout=timeouts, cookies=StatelessCookieJar())
    yield
    await http_client.aclose()
    http_client = None


app = FastAPI(
    title="Forum Platform Public API",
    version="1.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

setup_metrics_endpoint(app, metrics_registry)


def custom_openapi() -> dict:
    return load_canonical_openapi()


app.openapi = custom_openapi


@app.get("/health/live", tags=["Health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["Health"])
async def readiness() -> Response:
    if http_client is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "client": "uninitialized"},
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ready"})


@app.api_route(
    "/api/v1/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def gateway_proxy(request: Request, path: str) -> Response:
    request_id = get_or_generate_request_id(request)
    route = router.resolve(path)

    if route is None:
        return JSONResponse(
            status_code=404,
            headers={"X-Request-ID": request_id},
            content={
                "error": {
                    "code": "ROUTE_NOT_FOUND",
                    "message": f"No service registered for route: /api/v1/{path}",
                    "details": [],
                    "request_id": request_id,
                }
            },
        )

    if http_client is None:
        return JSONResponse(
            status_code=503,
            headers={"X-Request-ID": request_id},
            content={
                "error": {
                    "code": "GATEWAY_UNAVAILABLE",
                    "message": "Gateway client not ready.",
                    "details": [],
                    "request_id": request_id,
                }
            },
        )

    target_url = f"{route.target_base_url}{route.upstream_path}"
    return await forward_request(
        client=http_client,
        request=request,
        target_url=target_url,
        request_id=request_id,
    )


# --- Frontend Client Serving (DEC / Task 019) ---
if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index() -> Response:
        index_file = FRONTEND_DIR / "index.html"
        if index_file.is_file():
            return FileResponse(str(index_file))
        return JSONResponse({"status": "ok", "message": "Forum Platform Gateway"})
