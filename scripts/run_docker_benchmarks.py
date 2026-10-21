"""Standalone benchmark executing live HTTP requests through the Docker Gateway and PostgreSQL."""

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import httpx

GATEWAY_URL = "http://localhost:8000"


@dataclass
class ScenarioResult:
    scenario: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    duration_seconds: float
    throughput_rps: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_min_ms: float
    latency_max_ms: float
    status_distribution: dict[int, int]


async def execute_load(
    name: str, client: httpx.AsyncClient, req_factory, total_req: int, concurrency: int
) -> ScenarioResult:
    latencies: list[float] = []
    status_counts: dict[int, int] = {}
    success = 0
    failed = 0

    sem = asyncio.Semaphore(concurrency)

    async def single_worker(idx: int):
        nonlocal success, failed
        async with sem:
            t0 = time.perf_counter()
            try:
                resp = await req_factory(idx)
                dt = (time.perf_counter() - t0) * 1000.0
                latencies.append(dt)
                st = resp.status_code
                status_counts[st] = status_counts.get(st, 0) + 1
                if 200 <= st < 400:
                    success += 1
                else:
                    failed += 1
            except Exception:
                dt = (time.perf_counter() - t0) * 1000.0
                latencies.append(dt)
                failed += 1
                status_counts[599] = status_counts.get(599, 0) + 1

    t_start = time.perf_counter()
    tasks = [asyncio.create_task(single_worker(i)) for i in range(total_req)]
    await asyncio.gather(*tasks)
    total_duration = time.perf_counter() - t_start

    latencies.sort()
    n = len(latencies)
    p50 = latencies[int(n * 0.50)] if n else 0.0
    p95 = latencies[int(n * 0.95)] if n else 0.0
    p99 = latencies[int(n * 0.99)] if n else 0.0
    min_lat = latencies[0] if n else 0.0
    max_lat = latencies[-1] if n else 0.0
    rps = total_req / total_duration if total_duration > 0 else 0.0

    print(
        f"[{name}] {total_req} reqs in {total_duration:.2f}s | {rps:.1f} RPS | p50: {p50:.1f}ms | p95: {p95:.1f}ms | p99: {p99:.1f}ms | Success: {success}/{total_req}"
    )

    return ScenarioResult(
        scenario=name,
        total_requests=total_req,
        successful_requests=success,
        failed_requests=failed,
        duration_seconds=round(total_duration, 3),
        throughput_rps=round(rps, 2),
        latency_p50_ms=round(p50, 2),
        latency_p95_ms=round(p95, 2),
        latency_p99_ms=round(p99, 2),
        latency_min_ms=round(min_lat, 2),
        latency_max_ms=round(max_lat, 2),
        status_distribution=status_counts,
    )


async def main():
    print("=== LIVE DOCKER & POSTGRESQL RUNTIME BENCHMARK ===")
    limits = httpx.Limits(max_keepalive_connections=50, max_connections=100)
    timeout = httpx.Timeout(15.0)

    # 1. Warm-up
    async with httpx.AsyncClient(base_url=GATEWAY_URL, limits=limits, timeout=timeout) as client:
        print("\n--- Warming up Gateway & Upstream Containers ---")
        for _ in range(20):
            await client.get("/health/live")
            await client.get("/api/v1/categories")

        results = {}

        # Scenario 1: Public Read - Categories
        results["categories_read"] = asdict(
            await execute_load(
                "Public Reads: List Categories",
                client,
                lambda i: client.get("/api/v1/categories"),
                total_req=200,
                concurrency=10,
            )
        )

        # Scenario 2: Public Read - Threads
        results["threads_read"] = asdict(
            await execute_load(
                "Public Reads: List Threads (Paginated)",
                client,
                lambda i: client.get("/api/v1/threads?limit=20"),
                total_req=200,
                concurrency=10,
            )
        )

        # Scenario 3: Search Queries
        results["search_queries"] = asdict(
            await execute_load(
                "Search: Keyword Query",
                client,
                lambda i: client.get("/api/v1/search?q=PostgreSQL"),
                total_req=150,
                concurrency=10,
            )
        )

        # Scenario 4: Autocomplete Suggestions
        results["search_suggestions"] = asdict(
            await execute_load(
                "Search: Autocomplete Suggestions",
                client,
                lambda i: client.get("/api/v1/search/suggestions?q=Post"),
                total_req=150,
                concurrency=10,
            )
        )

        # Scenario 5: Authenticated Profile Reads
        auth_headers = {"Cookie": "forum_session=mPda_DWRWjCzrR46wbN_xkMBS4rLhY8qrv_6CzQL43g"}
        results["authenticated_profile"] = asdict(
            await execute_load(
                "Authenticated: Profile Read",
                client,
                lambda i: client.get("/api/v1/profile", headers=auth_headers),
                total_req=150,
                concurrency=10,
            )
        )

        # Scenario 6: Concurrency Scaling Progression (C=1, 5, 20, 50)
        progression = []
        for c in [1, 5, 20, 50]:
            res_c = await execute_load(
                f"Concurrency Progression (C={c})",
                client,
                lambda i: client.get("/api/v1/categories"),
                total_req=150,
                concurrency=c,
            )
            progression.append(asdict(res_c))
        results["concurrency_progression"] = progression

    out_file = "docs/benchmarks/docker_postgres_benchmark_results.json"
    os.makedirs("docs/benchmarks", exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "environment": {
                    "runtime": "Docker Compose",
                    "database": "PostgreSQL 16 Alpine",
                    "cache": "Redis 7 Alpine",
                    "gateway": "API Gateway Port 8000",
                    "host_cpus": 16,
                    "host_memory": "15GiB",
                },
                "results": results,
            },
            f,
            indent=2,
        )
    print(f"\nBenchmark complete. Saved to {out_file}")


if __name__ == "__main__":
    asyncio.run(main())
