"""Phase F: Sustained Load Soak Test (30 minutes).

Runs at the candidate sustainable operating point discovered in Phase D:
Target: Concurrency = 12, rate-controlled around 220-250 RPS.
Workload: 70% thread reads, 15% search, 10% authenticated profile, 5% thread writes.
Duration: 30 minutes (1800 seconds).
Continuously logs 1-minute windowed throughput, p50, p95, p99 latencies,
container memory/CPU stats, database pool metrics, and outbox lag.
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
DURATION_SECONDS = 1800  # 30 minutes
CONCURRENCY = 12


async def run_soak_test():
    run_id = f"soak_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    harness = BenchHarness(run_id=run_id)

    # Load pre-seeded virtual user tokens
    with open("scripts/bench_tokens.json") as f:
        vusers = json.load(f)

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

    print(
        f"Starting Soak Test: run_id={run_id}, duration={DURATION_SECONDS}s, concurrency={CONCURRENCY}"
    )

    container_stats_pre = await collect_container_stats()
    outbox_stats_pre = await collect_outbox_queue_metrics()

    search_terms = ["FastAPI", "Docker", "PostgreSQL", "Redis", "outbox", "session", "architecture"]

    start_time = time.perf_counter()
    end_time = start_time + DURATION_SECONDS

    # Window metrics logging
    minute_samples = []
    current_window_records = []
    all_status_counts = {}
    all_errors = {}
    total_completed = 0

    limits = httpx.Limits(max_connections=100, max_keepalive_connections=50)
    timeout = httpx.Timeout(10.0, connect=5.0)

    raw_file = open(os.path.join(harness.out_dir, "raw_requests.jsonl"), "w")

    async with httpx.AsyncClient(base_url=GATEWAY_URL, limits=limits, timeout=timeout) as client:
        # Worker function
        async def soak_worker(worker_id: int):
            nonlocal total_completed
            req_idx = 0
            while time.perf_counter() < end_time:
                req_idx += 1
                vuser = vusers[(worker_id + req_idx) % len(vusers)]
                session_cookie = f"forum_session={vuser['token']}"
                csrf_token = vuser["csrf_token"]

                action = random.random()
                method = "GET"
                path = ""
                req_t0 = time.perf_counter()
                status = 0
                err_msg = None

                try:
                    if action < 0.70:
                        # 70% Thread list
                        path = "/api/v1/threads?limit=10"
                        r = await client.get(path)
                        status = r.status_code
                    elif action < 0.85:
                        # 15% Search
                        q = random.choice(search_terms)
                        path = f"/api/v1/search?q={q}"
                        r = await client.get(path)
                        status = r.status_code
                    elif action < 0.95:
                        # 10% Profile
                        path = "/api/v1/profile"
                        r = await client.get(path, headers={"Cookie": session_cookie})
                        status = r.status_code
                    else:
                        # 5% Thread Create
                        method = "POST"
                        path = "/api/v1/threads"
                        r = await client.post(
                            path,
                            headers={"Cookie": session_cookie, "X-CSRF-Token": csrf_token},
                            json={
                                "category_id": cat_id,
                                "title": f"Soak {worker_id}-{req_idx}-{uuid.uuid4().hex[:6]}",
                                "body": "Sustained load soak verification payload.",
                                "tags": ["soak"],
                            },
                        )
                        status = r.status_code
                except httpx.TimeoutException:
                    err_msg = "Timeout"
                    status = 408
                except Exception as exc:
                    err_msg = str(type(exc).__name__)
                    status = 500

                lat_ms = (time.perf_counter() - req_t0) * 1000.0
                rec = {
                    "timestamp": req_t0,
                    "worker_id": worker_id,
                    "method": method,
                    "path": path,
                    "status": status,
                    "latency_ms": lat_ms,
                    "error": err_msg,
                }
                raw_file.write(json.dumps(rec) + "\n")
                current_window_records.append(rec)
                all_status_counts[status] = all_status_counts.get(status, 0) + 1
                if err_msg:
                    all_errors[err_msg] = all_errors.get(err_msg, 0) + 1
                total_completed += 1

                # Yield control to prevent CPU starvation
                await asyncio.sleep(0.01)

        # Window monitor task
        async def window_monitor():
            window_num = 0
            while time.perf_counter() < end_time:
                await asyncio.sleep(60.0)
                window_num += 1
                now_records = list(current_window_records)
                current_window_records.clear()

                w_reqs = len(now_records)
                w_lats = sorted(r["latency_ms"] for r in now_records) if now_records else []

                def p(pct):
                    if not w_lats:
                        return 0.0
                    return w_lats[int((len(w_lats) - 1) * (pct / 100.0))]

                w_rps = w_reqs / 60.0
                p50, p95, p99 = p(50), p(95), p(99)
                c_stats = await collect_container_stats()
                gw_mem = c_stats.get("forum_api_gateway", {}).get("mem_usage", "N/A")
                pg_mem = c_stats.get("forum_postgres", {}).get("mem_usage", "N/A")
                red_mem = c_stats.get("forum_redis", {}).get("mem_usage", "N/A")

                sample = {
                    "minute": window_num,
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                    "window_requests": w_reqs,
                    "achieved_rps": w_rps,
                    "p50_ms": p50,
                    "p95_ms": p95,
                    "p99_ms": p99,
                    "gateway_mem": gw_mem,
                    "postgres_mem": pg_mem,
                    "redis_mem": red_mem,
                }
                minute_samples.append(sample)
                print(
                    f"[Soak Min {window_num:02d}/30] Reqs: {w_reqs} | RPS: {w_rps:.1f} | "
                    f"p50: {p50:.1f}ms | p95: {p95:.1f}ms | p99: {p99:.1f}ms | "
                    f"GW Mem: {gw_mem} | PG Mem: {pg_mem}"
                )

        workers = [asyncio.create_task(soak_worker(w)) for w in range(CONCURRENCY)]
        monitor = asyncio.create_task(window_monitor())
        await asyncio.gather(*workers)
        await monitor

    raw_file.close()
    actual_duration = time.perf_counter() - start_time
    container_stats_post = await collect_container_stats()
    outbox_stats_post = await collect_outbox_queue_metrics()

    # Final summary calculations
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    manifest = {
        "run_id": run_id,
        "test_type": "30_minute_sustained_load_soak_test",
        "git_commit_sha": git_sha,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "host": {
            "os": platform.system(),
            "kernel": platform.release(),
            "cpu_architecture": platform.machine(),
            "cpu_cores": os.cpu_count(),
        },
        "target": GATEWAY_URL,
        "target_concurrency": CONCURRENCY,
        "duration_seconds_target": DURATION_SECONDS,
        "duration_seconds_actual": actual_duration,
        "total_completed_requests": total_completed,
        "overall_rps": total_completed / actual_duration,
        "container_stats_pre": container_stats_pre,
        "container_stats_post": container_stats_post,
        "outbox_stats_pre": outbox_stats_pre,
        "outbox_stats_post": outbox_stats_post,
    }

    summary = {
        "run_id": run_id,
        "duration_seconds": actual_duration,
        "concurrency": CONCURRENCY,
        "total_requests": total_completed,
        "overall_achieved_rps": total_completed / actual_duration,
        "status_distribution": all_status_counts,
        "errors": all_errors,
        "minute_window_samples": minute_samples,
    }

    with open(os.path.join(harness.out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    with open(os.path.join(harness.out_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

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

    print(f"\n[DONE] 30-minute soak test completed: {run_id}")
    return run_id


if __name__ == "__main__":
    asyncio.run(run_soak_test())
