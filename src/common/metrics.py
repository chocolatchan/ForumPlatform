"""Standardized Prometheus-compatible operational metrics registry and HTTP middleware.

Provides:
- Lightweight counters and gauges without external C-extension dependencies.
- Standard HTTP request metrics (requests_total, request_duration_seconds).
- Database connection pool utilization metrics.
- Outbox worker lifecycle and queue depth metrics.
- Strict low-cardinality label enforcement (method, status_code, service, db_name).
- Thread-safe in-memory aggregation formatted for Prometheus scraping (/metrics).
"""

import asyncio
import contextlib
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response


class MetricsRegistry:
    """Thread-safe Prometheus-compatible metrics registry."""

    def __init__(self, service_name: str) -> None:
        self.service_name = service_name
        self._lock = threading.Lock()
        # Metric key -> {label_tuple: value}
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(
            lambda: defaultdict(float)
        )
        self._gauges: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(
            lambda: defaultdict(float)
        )
        self._descriptions: dict[str, str] = {}
        self._types: dict[str, str] = {}
        self._callbacks: list[Callable[[], None]] = []
        self._async_callbacks: list[Callable[[], Any]] = []

    def register_callback(self, cb: Callable[[], None]) -> None:
        """Register a callback invoked immediately prior to scrape (e.g. for dynamic gauges)."""
        self._callbacks.append(cb)

    def inc_counter(
        self, name: str, value: float = 1.0, labels: dict[str, str] | None = None, desc: str = ""
    ) -> None:
        """Increment a counter metric."""
        lbl_tuple = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._types[name] = "counter"
            if desc:
                self._descriptions[name] = desc
            self._counters[name][lbl_tuple] += value

    def set_gauge(
        self, name: str, value: float, labels: dict[str, str] | None = None, desc: str = ""
    ) -> None:
        """Set the absolute value of a gauge metric."""
        lbl_tuple = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._types[name] = "gauge"
            if desc:
                self._descriptions[name] = desc
            self._gauges[name][lbl_tuple] = value

    def generate_prometheus_text(self) -> str:
        """Generate Prometheus exposition text format (0.0.4)."""
        # Execute callbacks to refresh dynamic gauges (e.g. DB pool, outbox counts)
        for cb in self._callbacks:
            with contextlib.suppress(Exception):
                cb()

        lines: list[str] = []
        with self._lock:
            # Output counters
            for name, series in sorted(self._counters.items()):
                desc = self._descriptions.get(name, f"Counter {name}")
                lines.append(f"# HELP {name} {desc}")
                lines.append(f"# TYPE {name} counter")
                for lbl_tuple, val in sorted(series.items()):
                    lbl_str = (
                        "{"
                        + ",".join(
                            f'{k}="{v}"' for k, v in [("service", self.service_name), *lbl_tuple]
                        )
                        + "}"
                    )
                    lines.append(f"{name}{lbl_str} {val}")

            # Output gauges
            for name, series in sorted(self._gauges.items()):
                desc = self._descriptions.get(name, f"Gauge {name}")
                lines.append(f"# HELP {name} {desc}")
                lines.append(f"# TYPE {name} gauge")
                for lbl_tuple, val in sorted(series.items()):
                    lbl_str = (
                        "{"
                        + ",".join(
                            f'{k}="{v}"' for k, v in [("service", self.service_name), *lbl_tuple]
                        )
                        + "}"
                    )
                    lines.append(f"{name}{lbl_str} {val}")

        return "\n".join(lines) + "\n"

    def register_async_callback(self, cb: Callable[[], Any]) -> None:
        """Register an async callback invoked prior to scrape (e.g. for async DB queries)."""
        self._async_callbacks.append(cb)

    async def generate_prometheus_text_async(self) -> str:
        """Generate Prometheus exposition text format including async metric sources."""
        # Execute sync callbacks
        for cb in self._callbacks:
            with contextlib.suppress(Exception):
                cb()

        # Execute async callbacks
        for acb in self._async_callbacks:
            try:
                res = acb()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass

        return self.generate_prometheus_text()


class PrometheusMetricsMiddleware(BaseHTTPMiddleware):
    """Starlette/FastAPI middleware tracking HTTP requests with safe low-cardinality labels."""

    def __init__(self, app: Any, registry: MetricsRegistry) -> None:
        super().__init__(app)
        self.registry = registry

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        # Avoid tracking the metrics endpoint itself
        if request.url.path == "/metrics":
            return await call_next(request)

        t0 = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            status_code = 500
            raise
        finally:
            dt_seconds = time.perf_counter() - t0
            status_str = str(status_code)
            status_class = f"{status_str[0]}xx"
            method = request.method.upper()

            labels = {
                "method": method,
                "status_code": status_str,
                "status_class": status_class,
            }
            self.registry.inc_counter(
                "http_requests_total",
                value=1.0,
                labels=labels,
                desc="Total HTTP requests processed",
            )
            self.registry.set_gauge(
                "http_request_duration_seconds",
                value=dt_seconds,
                labels=labels,
                desc="Last observed request latency in seconds",
            )


def setup_metrics_endpoint(
    app: Any,
    registry: MetricsRegistry,
    db_manager: Any | None = None,
    outbox_worker: Any | None = None,
) -> None:
    """Attach middleware and /metrics scraping route to a FastAPI application."""
    app.add_middleware(PrometheusMetricsMiddleware, registry=registry)

    # Register DB connection pool callback
    if db_manager is not None:

        def update_db_metrics() -> None:
            pool_stats = db_manager.get_pool_status()
            registry.set_gauge(
                "database_pool_size",
                float(pool_stats["size"]),
                desc="Current configured database connection pool size",
            )
            registry.set_gauge(
                "database_pool_checked_in",
                float(pool_stats["checked_in"]),
                desc="Available idle database connections in pool",
            )
            registry.set_gauge(
                "database_pool_checked_out",
                float(pool_stats["checked_out"]),
                desc="Active checked out database connections in pool",
            )
            registry.set_gauge(
                "database_pool_overflow",
                float(pool_stats["overflow"]),
                desc="Active overflow connections beyond pool size",
            )

        registry.register_callback(update_db_metrics)

    # Register outbox worker queue metrics callback
    if outbox_worker is not None:

        async def update_outbox_metrics() -> None:
            qm = await outbox_worker.get_queue_metrics()
            counts = qm["status_counts"]
            for st, val in counts.items():
                registry.set_gauge(
                    "outbox_events_count",
                    float(val),
                    labels={"status": st},
                    desc="Current number of outbox events in database by status",
                )
            registry.set_gauge(
                "outbox_oldest_event_age_seconds",
                float(qm["oldest_age_seconds"]),
                desc="Age in seconds of the oldest active/pending outbox event",
            )
            wm = qm["worker_metrics"]
            registry.set_gauge(
                "outbox_worker_claimed_total",
                float(wm.get("claimed_count", 0)),
                desc="Total outbox events claimed by worker",
            )
            registry.set_gauge(
                "outbox_worker_delivered_total",
                float(wm.get("success_count", 0)),
                desc="Total outbox events delivered by worker",
            )
            registry.set_gauge(
                "outbox_worker_failed_total",
                float(wm.get("failure_count", 0)),
                desc="Total outbox events failed during delivery",
            )
            registry.set_gauge(
                "outbox_worker_dead_letter_total",
                float(wm.get("dead_letter_count", 0)),
                desc="Total outbox events moved to dead-letter",
            )
            registry.set_gauge(
                "outbox_worker_stale_reclaimed_total",
                float(wm.get("stale_reclaimed_count", 0)),
                desc="Total stale processing events reclaimed",
            )
            registry.set_gauge(
                "outbox_worker_last_delivery_latency_ms",
                float(wm.get("last_delivery_latency_ms", 0.0)),
                desc="Latency in milliseconds of the last successful event delivery",
            )
            registry.set_gauge(
                "outbox_worker_running",
                1.0 if qm["is_running"] else 0.0,
                desc="Outbox background worker running status (1=running, 0=stopped)",
            )

        registry.register_async_callback(update_outbox_metrics)

    @app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
    async def metrics() -> PlainTextResponse:
        content = await registry.generate_prometheus_text_async()
        return PlainTextResponse(content=content, media_type="text/plain; version=0.0.4")
