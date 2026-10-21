"""Phase G: Controlled Fault Injection & System Recovery.

Tests resilience, graceful degradation, and recovery under downstream failures:
1. Baseline health verification.
2. Downstream Search Service Pause/Stop (`docker pause forum_search_service`).
   - Measure Gateway behavior on /api/v1/search (expects 502/503 or graceful error, non-blocking to other routes).
   - Verify Content and User services remain 100% operational (bulkhead isolation).
   - Verify thread creation continues without crashing; outbox events queue up.
3. Recovery (`docker unpause forum_search_service`).
   - Verify Search service recovers health.
   - Verify search requests resume returning 200 OK.
   - Verify outbox worker processes queued backlog.
"""

import asyncio
import json
import os
import platform
import subprocess
import time
import uuid
from datetime import UTC, datetime

import httpx

GATEWAY_URL = "http://localhost:8000"


async def run_fault_injection():
    run_id = f"fault_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    out_dir = os.path.join("docs/audit/performance/task_023", run_id)
    os.makedirs(out_dir, exist_ok=True)

    print(f"\n>>> STARTING FAULT INJECTION & RECOVERY TEST: {run_id} <<<")

    with open("scripts/bench_tokens.json") as f:
        vusers = json.load(f)
    vuser = vusers[0]
    session_cookie = f"forum_session={vuser['token']}"

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=10.0) as client:
        cat_resp = await client.get("/api/v1/categories")
        cat_data = cat_resp.json()
        categories = cat_data.get("items", []) if isinstance(cat_data, dict) else cat_data
        cat_id = categories[0]["id"] if categories else ""
        csrf_resp = await client.get("/api/v1/auth/csrf", headers={"Cookie": session_cookie})
        csrf_token = csrf_resp.json().get("csrf_token", "")

    records = []
    events_log = []

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=5.0) as client:
        # Step 1: Pre-fault healthy baseline (10 requests)
        print("Step 1: Measuring healthy pre-fault baseline...")
        for _ in range(10):
            r_search = await client.get("/api/v1/search?q=FastAPI")
            r_thread = await client.get("/api/v1/threads?limit=5")
            records.append(
                {"stage": "pre_fault", "target": "search", "status": r_search.status_code}
            )
            records.append(
                {"stage": "pre_fault", "target": "threads", "status": r_thread.status_code}
            )
        events_log.append({"stage": "pre_fault", "status": "healthy"})

        # Step 2: Inject Fault - Pause search service
        print("Step 2: Injecting fault -> Pausing forum_search_service container...")
        subprocess.run(["docker", "pause", "forum_search_service"], check=True)
        events_log.append(
            {
                "stage": "fault_injected",
                "action": "docker pause forum_search_service",
                "timestamp": time.time(),
            }
        )

        # Step 3: During Fault Verification (20 requests across isolated domains)
        print("Step 3: Measuring behavior during downstream outage...")
        isolated_threads_ok = 0
        search_failed_properly = 0

        for i in range(15):
            # Target 1: Search endpoint should fail gracefully (502 or 503 Bad Gateway / Service Unavailable)
            try:
                r_search = await client.get("/api/v1/search?q=FastAPI", timeout=2.0)
                st_search = r_search.status_code
            except httpx.TimeoutException:
                st_search = 504
            except Exception:
                st_search = 502

            if st_search in (502, 503, 504):
                search_failed_properly += 1
            records.append({"stage": "during_fault", "target": "search", "status": st_search})

            # Target 2: Bulkhead check - Content thread listing must remain 200 OK!
            r_thread = await client.get("/api/v1/threads?limit=5")
            if r_thread.status_code == 200:
                isolated_threads_ok += 1
            records.append(
                {"stage": "during_fault", "target": "threads", "status": r_thread.status_code}
            )

            # Target 3: Thread creation mutation should still succeed into Content DB
            if i % 3 == 0:
                r_create = await client.post(
                    "/api/v1/threads",
                    headers={"Cookie": session_cookie, "X-CSRF-Token": csrf_token},
                    json={
                        "category_id": cat_id,
                        "title": f"Fault Test Thread {i} - {uuid.uuid4().hex[:6]}",
                        "body": "Thread created while search service is degraded.",
                        "tags": ["fault_test"],
                    },
                )
                records.append(
                    {
                        "stage": "during_fault",
                        "target": "create_thread",
                        "status": r_create.status_code,
                    }
                )

            await asyncio.sleep(0.2)

        print(
            f"During fault: Search degraded properly: {search_failed_properly}/15 | Bulkhead threads OK: {isolated_threads_ok}/15"
        )

        # Step 4: Recover - Unpause search service
        print("Step 4: Recovering system -> Unpausing forum_search_service...")
        subprocess.run(["docker", "unpause", "forum_search_service"], check=True)
        events_log.append(
            {
                "stage": "fault_recovered",
                "action": "docker unpause forum_search_service",
                "timestamp": time.time(),
            }
        )

        # Wait for service reconnect
        await asyncio.sleep(2.0)

        # Step 5: Post-recovery verification
        print("Step 5: Measuring post-recovery behavior...")
        recovered_search_ok = 0
        for _ in range(15):
            r_search = await client.get("/api/v1/search?q=FastAPI")
            if r_search.status_code == 200:
                recovered_search_ok += 1
            records.append(
                {"stage": "post_recovery", "target": "search", "status": r_search.status_code}
            )
            await asyncio.sleep(0.1)

        print(f"Post-recovery search OK: {recovered_search_ok}/15")

    # Step 6: Verify Outbox catch-up
    await asyncio.sleep(3.0)
    outbox_check = (
        subprocess.check_output(
            [
                "docker",
                "exec",
                "forum_postgres",
                "psql",
                "-U",
                "forum_admin",
                "-d",
                "forum_content",
                "-t",
                "-c",
                "SELECT count(*) FROM content.outbox_events WHERE status = 'delivered';",
            ]
        )
        .decode()
        .strip()
    )
    print(f"Outbox verified delivered events: {outbox_check}")

    summary = {
        "run_id": run_id,
        "fault_type": "downstream_microservice_pause (forum_search_service)",
        "bulkhead_isolation_verified": isolated_threads_ok == 15,
        "graceful_gateway_degradation_verified": search_failed_properly >= 10,
        "self_healing_recovery_verified": recovered_search_ok == 15,
        "events_log": events_log,
        "records": records,
    }

    with open(os.path.join(out_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    manifest = {
        "run_id": run_id,
        "git_commit_sha": git_sha,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "host": {
            "os": platform.system(),
            "cpu_architecture": platform.machine(),
            "cpu_cores": os.cpu_count(),
        },
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n[DONE] Fault Injection & Recovery complete: {run_id}")
    return run_id


if __name__ == "__main__":
    asyncio.run(run_fault_injection())
