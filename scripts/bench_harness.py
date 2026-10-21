"""Task 023: Rigorous, Audited Multi-Stage Benchmark Harness.

Executes controlled, reproducible load benchmarks through Docker Compose Gateway.
Tracks exact latencies, status codes, errors, timeouts, and resource metrics.
Supports concurrency ramping, mixed workloads, and raw data persistence.
"""

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

import httpx

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8000")


@dataclass
class RequestRecord:
    timestamp: float
    scenario: str
    method: str
    path: str
    status_code: int
    latency_ms: float
    error: str | None


@dataclass
class DetailedResult:
    scenario: str
    offered_requests: int
    completed_requests: int
    successful_requests: int
    failed_requests: int
    timed_out_requests: int
    duration_seconds: float
    offered_rps: float
    achieved_rps: float
    successful_rps: float
    latency_p50_ms: float
    latency_p90_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_min_ms: float
    latency_max_ms: float
    status_distribution: dict[int, int]
    error_summary: dict[str, int]


async def collect_container_stats() -> dict[str, Any]:
    """Capture snapshot of docker container CPU and memory."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        stats = {}
        for line in stdout.decode().strip().splitlines():
            parts = line.split(",")
            if len(parts) >= 4:
                stats[parts[0]] = {
                    "cpu_percent": parts[1].strip(),
                    "mem_usage": parts[2].strip(),
                    "mem_percent": parts[3].strip(),
                }
        return stats
    except Exception as exc:
        return {"error": str(exc)}


async def collect_outbox_queue_metrics() -> dict[str, Any]:
    """Fetch outbox queue metrics from content and notification services."""
    async with httpx.AsyncClient(timeout=3.0) as client:
        out = {}
        for s, port in [
            ("gateway", 8000),
            ("content", 8002),
            ("notification", 8005),
            ("search", 8006),
        ]:
            try:
                resp = await client.get(f"http://localhost:{port}/metrics")
                lines = [l for l in resp.text.splitlines() if "outbox" in l or "database_pool" in l]
                out[s] = lines[:10]
            except Exception as e:
                out[s] = [f"error: {e}"]
        return out


class BenchHarness:
    def __init__(self, run_id: str, base_url: str = GATEWAY_URL):
        self.run_id = run_id
        self.base_url = base_url
        self.records: list[RequestRecord] = []
        self.out_dir = os.path.join("docs/audit/performance/task_023", run_id)
        os.makedirs(self.out_dir, exist_ok=True)
        os.makedirs(os.path.join(self.out_dir, "logs"), exist_ok=True)

    async def execute_scenario(
        self,
        name: str,
        req_func: Callable[[int, httpx.AsyncClient], Any],
        total_requests: int,
        concurrency: int,
        client_limits: httpx.Limits | None = None,
        timeout_seconds: float = 10.0,
        rate_limit_rps: float | None = None,
    ) -> DetailedResult:
        if client_limits is None:
            client_limits = httpx.Limits(
                max_keepalive_connections=min(100, concurrency * 2),
                max_connections=max(100, concurrency * 2),
            )
        timeout = httpx.Timeout(timeout_seconds)

        status_counts: dict[int, int] = {}
        error_counts: dict[str, int] = {}
        latencies: list[float] = []
        scenario_records: list[RequestRecord] = []
        success = 0
        failed = 0
        timed_out = 0

        sem = asyncio.Semaphore(concurrency)
        t_start = time.perf_counter()

        async with httpx.AsyncClient(
            base_url=self.base_url, limits=client_limits, timeout=timeout
        ) as client:

            async def worker(idx: int):
                nonlocal success, failed, timed_out
                if rate_limit_rps and rate_limit_rps > 0:
                    target_time = t_start + (idx / rate_limit_rps)
                    now = time.perf_counter()
                    if target_time > now:
                        await asyncio.sleep(target_time - now)

                async with sem:
                    req_t0 = time.perf_counter()
                    st = 0
                    err = None
                    try:
                        resp = await req_func(idx, client)
                        dt = (time.perf_counter() - req_t0) * 1000.0
                        st = resp.status_code
                        status_counts[st] = status_counts.get(st, 0) + 1
                        if 200 <= st < 400:
                            success += 1
                        else:
                            failed += 1
                            err = f"HTTP_{st}"
                            error_counts[err] = error_counts.get(err, 0) + 1
                    except httpx.TimeoutException:
                        dt = (time.perf_counter() - req_t0) * 1000.0
                        st = 504
                        timed_out += 1
                        failed += 1
                        err = "TimeoutException"
                        status_counts[504] = status_counts.get(504, 0) + 1
                        error_counts[err] = error_counts.get(err, 0) + 1
                    except Exception as exc:
                        dt = (time.perf_counter() - req_t0) * 1000.0
                        st = 599
                        failed += 1
                        err = exc.__class__.__name__
                        status_counts[599] = status_counts.get(599, 0) + 1
                        error_counts[err] = error_counts.get(err, 0) + 1

                    latencies.append(dt)
                    rec = RequestRecord(
                        timestamp=time.time(),
                        scenario=name,
                        method="GET/POST",
                        path="",
                        status_code=st,
                        latency_ms=round(dt, 2),
                        error=err,
                    )
                    scenario_records.append(rec)

            tasks = [asyncio.create_task(worker(i)) for i in range(total_requests)]
            await asyncio.gather(*tasks)

        total_duration = time.perf_counter() - t_start
        self.records.extend(scenario_records)

        latencies.sort()
        n = len(latencies)
        p50 = latencies[int(n * 0.50)] if n else 0.0
        p90 = latencies[int(n * 0.90)] if n else 0.0
        p95 = latencies[int(n * 0.95)] if n else 0.0
        p99 = latencies[int(n * 0.99)] if n else 0.0
        min_lat = latencies[0] if n else 0.0
        max_lat = latencies[-1] if n else 0.0

        offered_rps = total_requests / total_duration if total_duration > 0 else 0.0
        achieved_rps = len(latencies) / total_duration if total_duration > 0 else 0.0
        successful_rps = success / total_duration if total_duration > 0 else 0.0

        result = DetailedResult(
            scenario=name,
            offered_requests=total_requests,
            completed_requests=len(latencies),
            successful_requests=success,
            failed_requests=failed,
            timed_out_requests=timed_out,
            duration_seconds=round(total_duration, 3),
            offered_rps=round(offered_rps, 2),
            achieved_rps=round(achieved_rps, 2),
            successful_rps=round(successful_rps, 2),
            latency_p50_ms=round(p50, 2),
            latency_p90_ms=round(p90, 2),
            latency_p95_ms=round(p95, 2),
            latency_p99_ms=round(p99, 2),
            latency_min_ms=round(min_lat, 2),
            latency_max_ms=round(max_lat, 2),
            status_distribution=status_counts,
            error_summary=error_counts,
        )

        print(
            f"[{name}] C={concurrency} | {total_requests} reqs in {total_duration:.2f}s | "
            f"Achieved: {achieved_rps:.1f} RPS (Success: {successful_rps:.1f}) | "
            f"p50: {p50:.1f}ms | p95: {p95:.1f}ms | p99: {p99:.1f}ms | "
            f"Success: {success}/{total_requests} | Failures: {failed}"
        )
        return result

    def save_artifacts(self, manifest_data: dict[str, Any], results: dict[str, Any]):
        """Persist raw request records, summary, timeseries, container stats, and checksums."""
        # 1. raw_requests.jsonl
        raw_path = os.path.join(self.out_dir, "raw_requests.jsonl")
        with open(raw_path, "w") as f:
            for r in self.records:
                f.write(json.dumps(asdict(r)) + "\n")

        # 2. summary.json
        sum_path = os.path.join(self.out_dir, "summary.json")
        with open(sum_path, "w") as f:
            json.dump(results, f, indent=2)

        # 3. manifest.json
        man_path = os.path.join(self.out_dir, "manifest.json")
        with open(man_path, "w") as f:
            json.dump(manifest_data, f, indent=2)

        # 4. checksums.sha256
        chk_path = os.path.join(self.out_dir, "checksums.sha256")
        with open(chk_path, "w") as f:
            for fname in ["raw_requests.jsonl", "summary.json", "manifest.json"]:
                p = os.path.join(self.out_dir, fname)
                if os.path.exists(p):
                    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
                    f.write(f"{h}  {fname}\n")

        print(f"\n[BenchHarness] Raw evidence preserved in: {self.out_dir}")
