"""Phase E: Simulated 1,000 Concurrent Virtual Users Validation.

Models 1,000 simulated concurrent virtual users arriving over a ramp-up period,
performing realistic browsing and interacting journeys with think-time delays,
evaluating whether the multi-container architecture sustains 1,000 active users.
"""

import asyncio
import json
import os
import platform
import random
import subprocess
import time
import uuid
from datetime import UTC, datetime

import httpx

from scripts.bench_harness import (
    BenchHarness,
    collect_container_stats,
    collect_outbox_queue_metrics,
)

GATEWAY_URL = "http://localhost:8000"


async def run_1000_user_validation():
    run_id = f"users1000_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    harness = BenchHarness(run_id=run_id)

    # Load pre-seeded benchmark tokens
    with open("scripts/bench_tokens.json") as f:
        vusers = json.load(f)
    print(f"Loaded {len(vusers)} base authenticated users.")

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=10.0) as client:
        cat_resp = await client.get("/api/v1/categories")
        cat_data = cat_resp.json()
        categories = cat_data.get("items", []) if isinstance(cat_data, dict) else cat_data
        cat_id = categories[0]["id"] if categories else ""

        for u in vusers:
            csrf_resp = await client.get(
                "/api/v1/auth/csrf", headers={"Cookie": f"forum_session={u['token']}"}
            )
            u["csrf_token"] = csrf_resp.json().get("csrf_token", "")

    print(f"Target Category: {cat_id}")

    container_stats_pre = await collect_container_stats()
    outbox_stats_pre = await collect_outbox_queue_metrics()

    results = {}

    # Define User Journeys
    # 1,000 concurrent user sessions executing a cycle of actions with realistic think time (1.0 to 3.0 seconds).
    # Total duration of simulation: 60 seconds of sustained activity with 1,000 concurrent user tasks.
    SEARCH_TERMS = [
        "FastAPI",
        "Docker",
        "PostgreSQL",
        "Redis",
        "outbox",
        "token",
        "session",
        "architecture",
        "microservice",
    ]

    sim_start_time = time.perf_counter()
    duration_seconds = 60.0
    num_users = 1000

    print(f"\n>>> LAUNCHING 1,000 CONCURRENT SIMULATED USERS FOR {duration_seconds}s <<<")

    # Shared HTTP client with connection pool sized for the workload
    limits = httpx.Limits(max_connections=300, max_keepalive_connections=150)
    timeout = httpx.Timeout(10.0, connect=5.0)

    # Collect per-request latencies in memory
    journey_records = []
    status_counts = {}
    journey_errors = {}

    async with httpx.AsyncClient(base_url=GATEWAY_URL, limits=limits, timeout=timeout) as client:

        async def simulated_user_loop(user_id: int):
            # Staggered ramp-up: users arrive randomly over first 10 seconds
            await asyncio.sleep(random.uniform(0.0, 10.0))
            vuser = vusers[user_id % len(vusers)]
            session_cookie = f"forum_session={vuser['token']}"
            csrf_token = vuser["csrf_token"]

            while (time.perf_counter() - sim_start_time) < duration_seconds:
                action_choice = random.random()
                req_t0 = time.perf_counter()
                status = 0
                err_msg = None
                method = "GET"
                path = ""

                try:
                    if action_choice < 0.40:
                        # 40% Browse threads
                        method = "GET"
                        path = "/api/v1/threads?limit=15"
                        resp = await client.get(path)
                        status = resp.status_code
                    elif action_choice < 0.65:
                        # 25% Browse categories
                        method = "GET"
                        path = "/api/v1/categories"
                        resp = await client.get(path)
                        status = resp.status_code
                    elif action_choice < 0.85:
                        # 20% Search
                        query = random.choice(SEARCH_TERMS)
                        method = "GET"
                        path = f"/api/v1/search?q={query}"
                        resp = await client.get(path)
                        status = resp.status_code
                    elif action_choice < 0.95:
                        # 10% Authenticated profile
                        method = "GET"
                        path = "/api/v1/profile"
                        resp = await client.get(path, headers={"Cookie": session_cookie})
                        status = resp.status_code
                    else:
                        # 5% Create Thread (Write mutation)
                        method = "POST"
                        path = "/api/v1/threads"
                        resp = await client.post(
                            path,
                            headers={"Cookie": session_cookie, "X-CSRF-Token": csrf_token},
                            json={
                                "category_id": cat_id,
                                "title": f"SimUser {user_id} - {uuid.uuid4().hex[:6]}",
                                "body": "Simulation message payload from 1000 concurrent user benchmark.",
                                "tags": ["sim1000"],
                            },
                        )
                        status = resp.status_code
                except httpx.TimeoutException:
                    err_msg = "Timeout"
                    status = 408
                except Exception as exc:
                    err_msg = str(type(exc).__name__)
                    status = 500

                lat_ms = (time.perf_counter() - req_t0) * 1000.0
                record = {
                    "timestamp": req_t0,
                    "user_id": user_id,
                    "method": method,
                    "path": path,
                    "status": status,
                    "latency_ms": lat_ms,
                    "error": err_msg,
                }
                journey_records.append(record)
                status_counts[status] = status_counts.get(status, 0) + 1
                if err_msg:
                    journey_errors[err_msg] = journey_errors.get(err_msg, 0) + 1

                # Realistic think time: 1.5s to 3.5s before next user action
                await asyncio.sleep(random.uniform(1.5, 3.5))

        # Launch 1,000 concurrent tasks
        tasks = [asyncio.create_task(simulated_user_loop(u)) for u in range(num_users)]
        await asyncio.gather(*tasks)

    sim_elapsed = time.perf_counter() - sim_start_time
    total_reqs = len(journey_records)
    successful_reqs = sum(c for st, c in status_counts.items() if 200 <= st < 400)
    failed_reqs = sum(c for st, c in status_counts.items() if st >= 400 or st == 0)

    latencies = sorted(r["latency_ms"] for r in journey_records)

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        k = (len(latencies) - 1) * (p / 100.0)
        f = int(k)
        c = min(f + 1, len(latencies) - 1)
        d = k - f
        return latencies[f] + d * (latencies[c] - latencies[f])

    p50 = pct(50.0)
    p90 = pct(90.0)
    p95 = pct(95.0)
    p99 = pct(99.0)
    min_lat = latencies[0] if latencies else 0.0
    max_lat = latencies[-1] if latencies else 0.0

    overall_rps = total_reqs / sim_elapsed if sim_elapsed > 0 else 0.0
    success_rps = successful_reqs / sim_elapsed if sim_elapsed > 0 else 0.0

    print("\n[1,000 User Simulation Summary]")
    print(
        f"Elapsed: {sim_elapsed:.2f}s | Completed Requests: {total_reqs} | Success: {successful_reqs} | Failed: {failed_reqs}"
    )
    print(f"Offered / Achieved RPS: {overall_rps:.1f} | Success RPS: {success_rps:.1f}")
    print(
        f"p50: {p50:.1f}ms | p90: {p90:.1f}ms | p95: {p95:.1f}ms | p99: {p99:.1f}ms | Max: {max_lat:.1f}ms"
    )
    print(f"Status distribution: {status_counts}")
    if journey_errors:
        print(f"Errors: {journey_errors}")

    results["users_1000_journey"] = {
        "concurrent_simulated_users": num_users,
        "duration_seconds": sim_elapsed,
        "total_requests": total_reqs,
        "successful_requests": successful_reqs,
        "failed_requests": failed_reqs,
        "achieved_rps": overall_rps,
        "successful_rps": success_rps,
        "latency_p50_ms": p50,
        "latency_p90_ms": p90,
        "latency_p95_ms": p95,
        "latency_p99_ms": p99,
        "latency_min_ms": min_lat,
        "latency_max_ms": max_lat,
        "status_distribution": status_counts,
        "errors": journey_errors,
    }

    container_stats_post = await collect_container_stats()
    outbox_stats_post = await collect_outbox_queue_metrics()

    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    manifest = {
        "run_id": run_id,
        "test_type": "1000_concurrent_simulated_users",
        "git_commit_sha": git_sha,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "host": {
            "os": platform.system(),
            "kernel": platform.release(),
            "cpu_architecture": platform.machine(),
            "cpu_cores": os.cpu_count(),
        },
        "target": GATEWAY_URL,
        "simulated_users": num_users,
        "think_time_range_seconds": "1.5 - 3.5s",
        "container_stats_pre": container_stats_pre,
        "container_stats_post": container_stats_post,
        "outbox_stats_pre": outbox_stats_pre,
        "outbox_stats_post": outbox_stats_post,
    }

    # Write raw request records to file
    raw_path = os.path.join(harness.out_dir, "raw_requests.jsonl")
    with open(raw_path, "w") as f:
        for r in journey_records:
            f.write(json.dumps(r) + "\n")

    summary_path = os.path.join(harness.out_dir, "summary.json")
    with open(summary_path, "w") as f:
        json.dump(results, f, indent=2)

    manifest_path = os.path.join(harness.out_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    # Compute checksums
    import hashlib

    checksums = {}
    for fname in ["raw_requests.jsonl", "summary.json", "manifest.json"]:
        fpath = os.path.join(harness.out_dir, fname)
        if os.path.exists(fpath):
            with open(fpath, "rb") as bf:
                checksums[fname] = hashlib.sha256(bf.read()).hexdigest()
    with open(os.path.join(harness.out_dir, "checksums.sha256"), "w") as f:
        for k, v in checksums.items():
            f.write(f"{v}  {k}\n")

    print(f"\n[DONE] 1,000 user validation artifacts saved in: {harness.out_dir}")
    return run_id


if __name__ == "__main__":
    asyncio.run(run_1000_user_validation())
