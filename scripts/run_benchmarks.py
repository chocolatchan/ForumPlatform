"""Benchmark runner executing multi-stage load, saturation, and resilience benchmarks."""

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import httpx
from httpx import ASGITransport, AsyncClient

# Import microservice applications and DB managers
import src.gateway.app as gateway_app_mod
import src.services.content.app as content_app_mod
import src.services.content.database as content_db_mod
import src.services.interaction.app as interaction_app_mod
import src.services.interaction.database as interaction_db_mod
import src.services.moderation.app as moderation_app_mod
import src.services.moderation.database as moderation_db_mod
import src.services.notification.app as notification_app_mod
import src.services.notification.database as notification_db_mod
import src.services.search.app as search_app_mod
import src.services.search.database as search_db_mod
import src.services.user.app as user_app_mod
import src.services.user.database as user_db_mod
from src.common.database import DatabaseSessionManager
from src.services.content.auth import set_auth_http_client as set_content_auth_client
from src.services.content.models import Base as ContentBase
from src.services.content.models import Category, Comment, Thread
from src.services.interaction.auth import set_auth_http_client as set_interaction_auth_client
from src.services.interaction.models import Base as InteractionBase
from src.services.moderation.auth import set_auth_http_client as set_moderation_auth_client
from src.services.moderation.models import Base as ModerationBase
from src.services.notification.auth import set_auth_http_client as set_notification_auth_client
from src.services.notification.models import Base as NotificationBase
from src.services.search.auth import set_auth_http_client as set_search_auth_client
from src.services.search.models import Base as SearchBase
from src.services.user.models import Account
from src.services.user.models import Base as UserBase
from src.services.user.models import Session as DbSession
from src.services.user.security import generate_csrf_token, hash_secret


@dataclass
class MetricSummary:
    scenario: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    throttled_requests: int
    duration_seconds: float
    throughput_rps: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_min_ms: float
    latency_max_ms: float
    status_distribution: dict[int, int]
    error_details: dict[str, int]


class UnifiedDispatcher(httpx.AsyncBaseTransport):
    """Multiplexes Gateway requests to appropriate FastAPI microservices in-process."""

    def __init__(self) -> None:
        self.transports = {
            "http://localhost:8001": ASGITransport(app=user_app_mod.app),
            "http://localhost:8002": ASGITransport(app=content_app_mod.app),
            "http://localhost:8003": ASGITransport(app=interaction_app_mod.app),
            "http://localhost:8004": ASGITransport(app=moderation_app_mod.app),
            "http://localhost:8005": ASGITransport(app=notification_app_mod.app),
            "http://localhost:8006": ASGITransport(app=search_app_mod.app),
            "http://user-service:8001": ASGITransport(app=user_app_mod.app),
            "http://content-service:8002": ASGITransport(app=content_app_mod.app),
            "http://interaction-service:8003": ASGITransport(app=interaction_app_mod.app),
            "http://moderation-service:8004": ASGITransport(app=moderation_app_mod.app),
            "http://notification-service:8005": ASGITransport(app=notification_app_mod.app),
            "http://search-service:8006": ASGITransport(app=search_app_mod.app),
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        netloc = (
            request.url.netloc.decode("ascii")
            if isinstance(request.url.netloc, bytes)
            else str(request.url.netloc)
        )
        base_origin = f"{request.url.scheme}://{netloc}"
        transport = self.transports.get(base_origin)
        if not transport:
            return httpx.Response(status_code=503, content=b"Gateway Transport Missing")

        async with AsyncClient(transport=transport, base_url=base_origin, timeout=30.0) as client:
            resp = await client.request(
                method=request.method,
                url=str(request.url),
                headers=dict(request.headers),
                content=await request.aread(),
            )
            return httpx.Response(
                status_code=resp.status_code,
                headers=resp.headers,
                stream=httpx.ByteStream(resp.content),
                request=request,
            )


class BenchmarkHarness:
    """Orchestrates test data preparation and asynchronous multi-client benchmarking."""

    def __init__(self, db_path: str = "benchmark_store.db") -> None:
        self.db_path = db_path
        self.db_url = f"sqlite+aiosqlite:///{os.path.abspath(db_path)}"
        self.shared_db = DatabaseSessionManager(self.db_url, connect_args={"timeout": 30.0})
        self.dispatcher = UnifiedDispatcher()
        self.gateway_client: AsyncClient | None = None
        self.test_users: list[dict] = []
        self.test_categories: list[dict] = []
        self.test_threads: list[dict] = []
        self.csrf_token: str = ""

    async def setup(
        self, num_categories: int = 5, num_threads: int = 30, num_users: int = 10
    ) -> None:
        """Initialize database schemas, wire mocks, and seed test data."""
        if os.path.exists(self.db_path):
            with contextlib.suppress(OSError):
                os.remove(self.db_path)

        # Create all tables in single sqlite instance
        async with self.shared_db.engine.begin() as conn:
            await conn.run_sync(UserBase.metadata.create_all)
            await conn.run_sync(ContentBase.metadata.create_all)
            await conn.run_sync(InteractionBase.metadata.create_all)
            await conn.run_sync(ModerationBase.metadata.create_all)
            await conn.run_sync(NotificationBase.metadata.create_all)
            await conn.run_sync(SearchBase.metadata.create_all)

        # Wire shared database to all service singletons
        for mod in (
            user_db_mod,
            user_app_mod,
            content_db_mod,
            content_app_mod,
            interaction_db_mod,
            interaction_app_mod,
            moderation_db_mod,
            moderation_app_mod,
            notification_db_mod,
            notification_app_mod,
            search_db_mod,
            search_app_mod,
        ):
            if hasattr(mod, "user_db"):
                mod.user_db = self.shared_db
            if hasattr(mod, "content_db"):
                mod.content_db = self.shared_db
            if hasattr(mod, "interaction_db"):
                mod.interaction_db = self.shared_db
            if hasattr(mod, "moderation_db"):
                mod.moderation_db = self.shared_db
            if hasattr(mod, "notification_db"):
                mod.notification_db = self.shared_db
            if hasattr(mod, "search_db"):
                mod.search_db = self.shared_db

        # Wire inter-service clients
        user_transport = ASGITransport(app=user_app_mod.app)
        mock_user_client = AsyncClient(
            transport=user_transport, base_url="http://localhost:8001", timeout=30.0
        )
        set_content_auth_client(mock_user_client)
        set_interaction_auth_client(mock_user_client)
        set_moderation_auth_client(mock_user_client)
        set_notification_auth_client(mock_user_client)
        set_search_auth_client(mock_user_client)

        inter_client = AsyncClient(transport=self.dispatcher, timeout=30.0)
        interaction_app_mod.set_content_http_client(inter_client)
        moderation_app_mod.set_content_http_client(inter_client)
        moderation_app_mod.set_user_http_client(inter_client)

        gateway_app_mod.http_client = AsyncClient(transport=self.dispatcher, timeout=30.0)
        gw_transport = ASGITransport(app=gateway_app_mod.app)
        self.gateway_client = AsyncClient(
            transport=gw_transport, base_url="http://localhost:8000", timeout=30.0
        )

        # Generate persistent CSRF token
        self.csrf_token = generate_csrf_token(user_app_mod.user_settings.csrf_secret)

        # Seed test users & active sessions
        run_id = uuid.uuid4().hex[:6]
        now = datetime.now(UTC)
        async with self.shared_db.session() as session:
            for i in range(num_users):
                uid = uuid.uuid4()
                username = f"bench_user_{run_id}_{i}"
                token = f"bench_session_token_{run_id}_{i}"
                acc = Account(
                    id=uid,
                    username=username,
                    username_normalized=username,
                    email=f"bench_{run_id}_{i}@example.test",
                    email_normalized=f"bench_{run_id}_{i}@example.test",
                    password_hash="mock_hash",
                    role="admin" if i == 0 else "member",
                    is_banned=False,
                )
                session.add(acc)
                sess = DbSession(
                    id=uuid.uuid4(),
                    account_id=uid,
                    session_token_hash=hash_secret(token),
                    idle_expires_at=now + timedelta(days=2),
                    absolute_expires_at=now + timedelta(days=7),
                    created_at=now,
                    last_seen_at=now,
                )
                session.add(sess)
                self.test_users.append(
                    {
                        "id": uid,
                        "username": username,
                        "token": token,
                        "cookies": {"forum_session": token},
                        "role": acc.role,
                    }
                )

            # Seed Categories
            for c in range(num_categories):
                cat_id = uuid.uuid4()
                name = f"Benchmark Category {run_id} {c}"
                cat = Category(
                    id=cat_id,
                    name=name,
                    slug=f"benchmark-cat-{run_id}-{c}",
                    description="Performance evaluation discussions",
                    sort_order=c,
                    is_hidden=False,
                    created_by=self.test_users[0]["id"],
                )
                session.add(cat)
                self.test_categories.append({"id": cat_id, "name": name, "slug": cat.slug})

            # Seed Threads & initial Comments
            for t in range(num_threads):
                th_id = uuid.uuid4()
                author = self.test_users[t % len(self.test_users)]
                cat = self.test_categories[t % len(self.test_categories)]
                thread = Thread(
                    id=th_id,
                    category_id=cat["id"],
                    author_id=author["id"],
                    title=f"Benchmark Thread Subject Number {t:03d} Performance Test",
                    body="This is an extensive discussion body engineered for benchmarking database reads and full-text search indexing.",
                    status="published",
                    is_pinned=(t == 0),
                    is_locked=False,
                    view_count=t * 10,
                )
                session.add(thread)
                self.test_threads.append(
                    {"id": th_id, "title": thread.title, "author_id": author["id"]}
                )

                # Seed comment
                comm = Comment(
                    id=uuid.uuid4(),
                    thread_id=th_id,
                    author_id=self.test_users[(t + 1) % len(self.test_users)]["id"],
                    parent_comment_id=None,
                    body=f"Comment reply to benchmark thread {t} discussing capacity planning.",
                    is_deleted=False,
                )
                session.add(comm)

            await session.commit()

        # Seed Search Documents for seeded threads
        search_transport = ASGITransport(app=search_app_mod.app)
        async with AsyncClient(transport=search_transport, base_url="http://test") as sc:
            for th in self.test_threads:
                await sc.post(
                    "/internal/events",
                    headers={
                        "X-Internal-Service-Key": "default-internal-service-secret-key-32chars"
                    },
                    json={
                        "event_id": str(uuid.uuid4()),
                        "event_type": "ThreadCreated",
                        "producer": "content-service",
                        "data": {
                            "thread_id": str(th["id"]),
                            "author_id": str(th["author_id"]),
                            "category_id": str(self.test_categories[0]["id"]),
                            "title": th["title"],
                            "body": "This is an extensive discussion body engineered for benchmarking database reads and full-text search indexing.",
                            "tags": ["performance", "benchmark", "fastapi"],
                            "status": "published",
                            "created_at": now.isoformat(),
                        },
                    },
                )

    async def execute_scenario(
        self,
        name: str,
        request_func,
        concurrency: int,
        total_requests: int,
    ) -> MetricSummary:
        """Run scenario concurrently with bounded concurrency workers."""
        latencies: list[float] = []
        statuses: dict[int, int] = {}
        errors: dict[str, int] = {}
        throttled_count = 0
        success_count = 0

        queue: asyncio.Queue[int] = asyncio.Queue()
        for i in range(total_requests):
            queue.put_nowait(i)

        start_time = time.perf_counter()

        async def worker() -> None:
            nonlocal throttled_count, success_count
            while not queue.empty():
                try:
                    item_idx = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                t0 = time.perf_counter()
                try:
                    resp = await request_func(item_idx)
                    elapsed = (time.perf_counter() - t0) * 1000.0
                    latencies.append(elapsed)
                    status_code = resp.status_code
                    statuses[status_code] = statuses.get(status_code, 0) + 1
                    if status_code == 429:
                        throttled_count += 1
                    elif 200 <= status_code < 400:
                        success_count += 1
                    else:
                        errors[f"HTTP_{status_code}"] = errors.get(f"HTTP_{status_code}", 0) + 1
                except Exception as exc:
                    elapsed = (time.perf_counter() - t0) * 1000.0
                    latencies.append(elapsed)
                    err_name = type(exc).__name__
                    errors[err_name] = errors.get(err_name, 0) + 1
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await asyncio.gather(*workers)
        duration = time.perf_counter() - start_time

        latencies.sort()
        n = len(latencies)
        p50 = latencies[int(n * 0.50)] if n else 0.0
        p95 = latencies[int(n * 0.95)] if n else 0.0
        p99 = latencies[int(n * 0.99)] if n else 0.0
        min_l = latencies[0] if n else 0.0
        max_l = latencies[-1] if n else 0.0
        rps = (n / duration) if duration > 0 else 0.0

        return MetricSummary(
            scenario=name,
            total_requests=n,
            successful_requests=success_count,
            failed_requests=n - success_count - throttled_count,
            throttled_requests=throttled_count,
            duration_seconds=round(duration, 3),
            throughput_rps=round(rps, 2),
            latency_p50_ms=round(p50, 2),
            latency_p95_ms=round(p95, 2),
            latency_p99_ms=round(p99, 2),
            latency_min_ms=round(min_l, 2),
            latency_max_ms=round(max_l, 2),
            status_distribution=statuses,
            error_details=errors,
        )

    async def teardown(self) -> None:
        """Clean up shared resources."""
        if self.gateway_client:
            await self.gateway_client.aclose()
        await self.shared_db.close()
        if os.path.exists(self.db_path):
            with contextlib.suppress(OSError):
                os.remove(self.db_path)


async def run_all_benchmarks(output_file: str = "docs/benchmarks/benchmark_results.json") -> None:
    """Execute complete 6-stage performance evaluation suite."""
    harness = BenchmarkHarness()
    print("[1/7] Setting up benchmark test store and seeding test fixtures...")
    await harness.setup(num_categories=5, num_threads=50, num_users=20)
    gw = harness.gateway_client
    assert gw is not None

    auth_user = harness.test_users[1]

    all_results: dict[str, list[dict]] = {
        "public_reads": [],
        "authenticated_workflows": [],
        "event_heavy_workloads": [],
        "progression_stages": [],
        "resilience_recovery": [],
    }

    print("\n[2/7] Running Public Read Workloads...")
    # 1. Public reads: Categories
    res_cats = await harness.execute_scenario(
        "List Categories",
        lambda idx: gw.get("/api/v1/categories"),
        concurrency=10,
        total_requests=200,
    )
    all_results["public_reads"].append(asdict(res_cats))

    # 2. Public reads: Threads with Pagination
    res_threads = await harness.execute_scenario(
        "List Threads Paginated",
        lambda idx: gw.get("/api/v1/threads?limit=10"),
        concurrency=10,
        total_requests=200,
    )
    all_results["public_reads"].append(asdict(res_threads))

    # 3. Public reads: Read Thread and Comments
    res_detail = await harness.execute_scenario(
        "Read Thread Detail & Comments",
        lambda idx: gw.get(
            f"/api/v1/threads/{harness.test_threads[idx % len(harness.test_threads)]['id']}"
        ),
        concurrency=10,
        total_requests=200,
    )
    all_results["public_reads"].append(asdict(res_detail))

    # 4. Public reads: Search by Keyword
    res_search = await harness.execute_scenario(
        "Search Keyword (FastAPI)",
        lambda idx: gw.get("/api/v1/search?q=FastAPI"),
        concurrency=10,
        total_requests=200,
    )
    all_results["public_reads"].append(asdict(res_search))

    # 5. Public reads: Search Autocomplete Suggestions
    res_sug = await harness.execute_scenario(
        "Search Autocomplete Suggestions",
        lambda idx: gw.get("/api/v1/search/suggestions?q=bench"),
        concurrency=10,
        total_requests=200,
    )
    all_results["public_reads"].append(asdict(res_sug))

    print("\n[3/7] Running Authenticated Workloads...")
    # 6. Authenticated: Read Session Profile
    res_session = await harness.execute_scenario(
        "Authenticated Profile Inspection",
        lambda idx: gw.get(
            "/api/v1/profile", cookies=harness.test_users[idx % len(harness.test_users)]["cookies"]
        ),
        concurrency=10,
        total_requests=200,
    )
    all_results["authenticated_workflows"].append(asdict(res_session))

    # 7. Authenticated: Read Notifications & Unread Count
    res_notif = await harness.execute_scenario(
        "List Notifications & Unread Count",
        lambda idx: gw.get(
            "/api/v1/notifications",
            cookies=harness.test_users[idx % len(harness.test_users)]["cookies"],
        ),
        concurrency=10,
        total_requests=200,
    )
    all_results["authenticated_workflows"].append(asdict(res_notif))

    # 8. Authenticated: Bookmark Thread
    res_bm = await harness.execute_scenario(
        "Bookmark Thread (Mutating with CSRF)",
        lambda idx: gw.put(
            f"/api/v1/threads/{harness.test_threads[idx % len(harness.test_threads)]['id']}/bookmark",
            cookies=harness.test_users[idx % len(harness.test_users)]["cookies"],
            headers={"X-CSRF-Token": harness.csrf_token},
        ),
        concurrency=1,
        total_requests=25,
    )
    all_results["authenticated_workflows"].append(asdict(res_bm))

    # 9. Authenticated: Vote on Thread
    res_vote = await harness.execute_scenario(
        "Vote on Thread (+1)",
        lambda idx: gw.put(
            f"/api/v1/threads/{harness.test_threads[idx % len(harness.test_threads)]['id']}/vote",
            cookies=harness.test_users[(idx + 1) % len(harness.test_users)]["cookies"],
            headers={"X-CSRF-Token": harness.csrf_token},
            json={"value": 1},
        ),
        concurrency=1,
        total_requests=25,
    )
    all_results["authenticated_workflows"].append(asdict(res_vote))

    # 10. Authenticated: Controlled Comment Creation
    res_comm = await harness.execute_scenario(
        "Create Comment at Controlled Rate",
        lambda idx: gw.post(
            f"/api/v1/threads/{harness.test_threads[idx % len(harness.test_threads)]['id']}/comments",
            cookies=harness.test_users[idx % len(harness.test_users)]["cookies"],
            headers={"X-CSRF-Token": harness.csrf_token},
            json={"body": f"Benchmarking comment insertion concurrency item {idx}."},
        ),
        concurrency=1,
        total_requests=25,
    )
    all_results["authenticated_workflows"].append(asdict(res_comm))

    print("\n[4/7] Running Event-Heavy Workloads & View Deduplication...")
    # 11. Event-Heavy: View Tracking & 30-min Deduplication
    res_view = await harness.execute_scenario(
        "Thread View Deduplication (DEC-005)",
        lambda idx: gw.post(
            f"/api/v1/threads/{harness.test_threads[idx % len(harness.test_threads)]['id']}/view",
            cookies=harness.test_users[idx % len(harness.test_users)]["cookies"],
        ),
        concurrency=1,
        total_requests=40,
    )
    all_results["event_heavy_workloads"].append(asdict(res_view))

    # 12. Event-Heavy: Ingest Events into Search Service
    search_transport = ASGITransport(app=search_app_mod.app)
    async with AsyncClient(transport=search_transport, base_url="http://test") as sc:
        res_search_ingest = await harness.execute_scenario(
            "Search Event Ingestion Burst & Deduplication",
            lambda idx: sc.post(
                "/internal/events",
                headers={"X-Internal-Service-Key": "default-internal-service-secret-key-32chars"},
                json={
                    "event_id": str(uuid.uuid4()),
                    "event_type": "ThreadCreated",
                    "producer": "content-service",
                    "data": {
                        "thread_id": str(uuid.uuid4()),
                        "author_id": str(auth_user["id"]),
                        "category_id": str(harness.test_categories[0]["id"]),
                        "title": f"Asynchronous High Volume Ingestion Thread Subject {idx}",
                        "body": "Ingestion content verification payload for search indexing.",
                        "tags": ["events", "search"],
                        "status": "published",
                        "created_at": datetime.now(UTC).isoformat(),
                    },
                },
            ),
            concurrency=10,
            total_requests=100,
        )
        all_results["event_heavy_workloads"].append(asdict(res_search_ingest))

    print("\n[5/7] Executing Multi-Stage Concurrency Progression (Stages 1 to 5)...")
    concurrency_levels = [1, 5, 20, 50, 100]
    stage_names = [
        "Stage 1: Smoke Test (C=1)",
        "Stage 2: Baseline (C=5)",
        "Stage 3: Step-Load (C=20)",
        "Stage 4: Stress/Saturation (C=50)",
        "Stage 5: High Concurrency (C=100)",
    ]
    for c, stage_label in zip(concurrency_levels, stage_names, strict=False):
        res_stage = await harness.execute_scenario(
            stage_label,
            lambda idx: gw.get("/api/v1/categories"),
            concurrency=c,
            total_requests=300,
        )
        all_results["progression_stages"].append(asdict(res_stage))

    print("\n[6/7] Testing Fault Injection, Saturation & Recovery (Stage 6)...")
    orig_transport = harness.dispatcher.transports["http://localhost:8002"]

    class FailureTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=503, content=b'{"error": "Service Down"}')

    harness.dispatcher.transports["http://localhost:8002"] = FailureTransport()
    res_fail = await harness.execute_scenario(
        "Temporary Content Service Outage (Failure Injection)",
        lambda idx: gw.get("/api/v1/categories"),
        concurrency=5,
        total_requests=50,
    )
    all_results["resilience_recovery"].append(asdict(res_fail))

    # Restore healthy transport
    harness.dispatcher.transports["http://localhost:8002"] = orig_transport
    res_recover = await harness.execute_scenario(
        "Content Service Immediate Recovery",
        lambda idx: gw.get("/api/v1/categories"),
        concurrency=5,
        total_requests=50,
    )
    all_results["resilience_recovery"].append(asdict(res_recover))

    print("\n[7/7] Writing benchmark results to disk...")
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "environment": {
                    "platform": sys.platform,
                    "python_version": sys.version,
                    "database": "sqlite+aiosqlite (in-memory/file pool)",
                },
                "results": all_results,
            },
            f,
            indent=2,
        )

    await harness.teardown()
    print(f"Benchmark run complete. Report saved to {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Forum Platform Benchmark Runner")
    parser.add_argument(
        "--output", default="docs/benchmarks/benchmark_results.json", help="Path to output JSON"
    )
    args = parser.parse_args()
    asyncio.run(run_all_benchmarks(output_file=args.output))
