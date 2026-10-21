"""Run Task 023 Reproducible Baseline, Cache Sensitivity, and Saturation Discovery."""

import asyncio
import json
import os
import platform
import random
import subprocess
import uuid
from dataclasses import asdict
from datetime import UTC, datetime

import httpx

from scripts.bench_harness import (
    BenchHarness,
    collect_container_stats,
    collect_outbox_queue_metrics,
)

GATEWAY_URL = "http://localhost:8000"


async def run_baseline_and_discovery():
    run_id = f"baseline_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    harness = BenchHarness(run_id=run_id)

    # Load pre-seeded virtual user tokens
    tokens_file = "scripts/bench_tokens.json"
    with open(tokens_file) as f:
        vusers = json.load(f)
    print(f"Loaded {len(vusers)} pre-seeded benchmark tokens.")

    # Fetch Category ID and pre-fetch session-bound CSRF tokens for all virtual users
    print("Pre-fetching CSRF tokens for virtual users...")
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=10.0) as client:
        cat_resp = await client.get("/api/v1/categories")
        cat_data = cat_resp.json()
        categories = cat_data.get("items", []) if isinstance(cat_data, dict) else cat_data
        cat_id = categories[0]["id"] if categories else ""

        for u in vusers:
            csrf_resp = await client.get(
                "/api/v1/auth/csrf",
                headers={"Cookie": f"forum_session={u['token']}"},
            )
            u["csrf_token"] = csrf_resp.json().get("csrf_token", "")

    print(f"Target Category ID: {cat_id}")
    print(f"Initialized {len(vusers)} users with individual CSRF tokens.")

    # Capture pre-test stats
    container_stats_pre = await collect_container_stats()
    outbox_stats_pre = await collect_outbox_queue_metrics()

    results = {}

    # --- Phase B.1: Warm-up ---
    print("\n>>> PHASE B.1: WARM-UP (50 requests) <<<")
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=10.0) as client:
        for _ in range(50):
            await client.get("/api/v1/categories")
            await client.get("/api/v1/threads?limit=10")

    # --- Phase B.2: Workload Baselines (3 runs each) ---
    print("\n>>> PHASE B.2: BASELINE RUNS (Workloads 1 to 6) <<<")

    # Workload 1: Public Categories Read
    for r in range(1, 4):
        res = await harness.execute_scenario(
            f"WL1: Public Categories Read (Run {r})",
            lambda i, cl: cl.get("/api/v1/categories"),
            total_requests=200,
            concurrency=10,
        )
        results[f"wl1_categories_run_{r}"] = asdict(res)

    # Workload 2: Public Threads Paginated Read
    for r in range(1, 4):
        res = await harness.execute_scenario(
            f"WL2: Public Threads Read (Run {r})",
            lambda i, cl: cl.get("/api/v1/threads?limit=20"),
            total_requests=200,
            concurrency=10,
        )
        results[f"wl2_threads_run_{r}"] = asdict(res)

    # Workload 3: Keyword Search
    for r in range(1, 4):
        query = random.choice(["FastAPI", "outbox", "PostgreSQL", "architecture", "test"])
        res = await harness.execute_scenario(
            f"WL3: Keyword Search (Run {r})",
            lambda i, cl: cl.get(f"/api/v1/search?q={query}"),
            total_requests=200,
            concurrency=10,
        )
        results[f"wl3_search_run_{r}"] = asdict(res)

    # Workload 4: Authenticated Profile Reads (Rotating across 50 virtual users)
    for r in range(1, 4):
        res = await harness.execute_scenario(
            f"WL4: Authenticated Profile Reads (Run {r})",
            lambda i, cl: cl.get(
                "/api/v1/profile",
                headers={"Cookie": f"forum_session={vusers[i % len(vusers)]['token']}"},
            ),
            total_requests=200,
            concurrency=10,
        )
        results[f"wl4_profile_run_{r}"] = asdict(res)

    # Workload 5: Thread Creation (Write mutation generating transactional outbox event)
    for r in range(1, 4):
        res = await harness.execute_scenario(
            f"WL5: Thread Creation Mutating (Run {r})",
            lambda i, cl: cl.post(
                "/api/v1/threads",
                headers={
                    "Cookie": f"forum_session={vusers[i % len(vusers)]['token']}",
                    "X-CSRF-Token": vusers[i % len(vusers)]["csrf_token"],
                },
                json={
                    "category_id": cat_id,
                    "title": f"Bench Thread Run {r} - Req {i} {uuid.uuid4().hex[:6]}",
                    "body": "Automated capacity baseline thread payload generating transactional outbox event.",
                    "tags": ["benchmark", "baseline"],
                },
            ),
            total_requests=50,
            concurrency=5,
        )
        results[f"wl5_create_thread_run_{r}"] = asdict(res)

    # Workload 6: Mixed Realistic Workflow (70% reads, 20% profile, 10% writes)
    for r in range(1, 4):

        async def mixed_worker(i, cl):
            dice = i % 10
            vuser = vusers[i % len(vusers)]
            if dice < 5:
                return await cl.get("/api/v1/threads?limit=10")
            elif dice < 7:
                return await cl.get("/api/v1/categories")
            elif dice < 9:
                return await cl.get(
                    "/api/v1/profile", headers={"Cookie": f"forum_session={vuser['token']}"}
                )
            else:
                return await cl.post(
                    "/api/v1/threads",
                    headers={
                        "Cookie": f"forum_session={vuser['token']}",
                        "X-CSRF-Token": vuser["csrf_token"],
                    },
                    json={
                        "category_id": cat_id,
                        "title": f"Mixed Thread {r}-{i}-{uuid.uuid4().hex[:6]}",
                        "body": "Body content for mixed load test.",
                        "tags": ["mixed"],
                    },
                )

        res = await harness.execute_scenario(
            f"WL6: Mixed Realistic Journey (Run {r})",
            mixed_worker,
            total_requests=200,
            concurrency=15,
        )
        results[f"wl6_mixed_run_{r}"] = asdict(res)

    # --- Phase C: Cache Sensitivity Analysis ---
    print("\n>>> PHASE C: CACHE SENSITIVITY TESTING <<<")
    # Repeated hot-key read
    res_hot = await harness.execute_scenario(
        "Cache Sensitivity: Hot-Key Category Read",
        lambda i, cl: cl.get("/api/v1/categories"),
        total_requests=300,
        concurrency=20,
    )
    results["cache_hot_key"] = asdict(res_hot)

    # Dispersed search terms
    terms = [
        "FastAPI",
        "Python",
        "Docker",
        "Compose",
        "PostgreSQL",
        "Redis",
        "Outbox",
        "Async",
        "SQLAlchemy",
        "RabbitMQ",
    ]
    res_dispersed = await harness.execute_scenario(
        "Cache Sensitivity: Dispersed Search Terms",
        lambda i, cl: cl.get(f"/api/v1/search?q={terms[i % len(terms)]}"),
        total_requests=300,
        concurrency=20,
    )
    results["cache_dispersed"] = asdict(res_dispersed)

    # --- Phase D: Saturation Ramp Analysis ---
    print("\n>>> PHASE D: CONTROLLED CONCURRENCY RAMP & SATURATION DISCOVERY <<<")
    ramp_levels = [1, 5, 10, 25, 50, 100, 200]
    ramp_results = []
    for c in ramp_levels:
        res_c = await harness.execute_scenario(
            f"Saturation Ramp (Concurrency={c})",
            lambda i, cl: cl.get("/api/v1/threads?limit=10"),
            total_requests=max(200, c * 5),
            concurrency=c,
            timeout_seconds=15.0,
        )
        ramp_results.append(asdict(res_c))
    results["saturation_ramp"] = ramp_results

    # Post-test stats
    container_stats_post = await collect_container_stats()
    outbox_stats_post = await collect_outbox_queue_metrics()

    # Manifest
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    manifest = {
        "run_id": run_id,
        "git_commit_sha": git_sha,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "host": {
            "os": platform.system(),
            "kernel": platform.release(),
            "cpu_architecture": platform.machine(),
            "cpu_cores": os.cpu_count(),
        },
        "target": GATEWAY_URL,
        "container_stats_pre": container_stats_pre,
        "container_stats_post": container_stats_post,
        "outbox_stats_pre": outbox_stats_pre,
        "outbox_stats_post": outbox_stats_post,
    }

    harness.save_artifacts(manifest, results)
    print(f"\n[DONE] Baseline & Saturation Discovery complete: {run_id}")
    return run_id


if __name__ == "__main__":
    asyncio.run(run_baseline_and_discovery())
