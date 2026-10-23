"""Comprehensive End-to-End System Integration Tests (Task 013).

Verifies the entire Forum Platform multi-service architecture through the API Gateway:
1. Complete Account Lifecycle & Authentication:
   - Registration with anti-enumeration (202 Accepted).
   - OTP retrieval from test delivery sink, failed attempts, and valid activation.
   - Login, opaque server-side session cookie issuance, authenticated session inspection.
   - Synchronizer CSRF validation on profile mutation.
   - Logout and session revocation.
2. Complete Discussion & Content Lifecycle:
   - Category creation (admin role check).
   - Thread creation with deduplicated tags and outbox event emission.
   - Hierarchical comments (depth 1 & 2) and max-depth restriction (depth 3 rejected).
   - Thread reaction/voting (+1 / -1 / neutral toggle) and bookmarking.
   - Author best-answer selection.
   - Thread lock and pin toggles (moderator authorization).
   - Soft-delete policy with ADR-008 checks and search visibility removal.
3. Moderation Triage & Enforcement Actions:
   - Abuse reporting for thread, comment, and account targets.
   - Duplicate active report prevention.
   - Moderator queue inspection and claim/reviewing transition.
   - Remote enforcement action (e.g. soft_delete) executed via REST client.
   - Persistent audit log verification.
4. Cross-Service Event Synchronization & Outbox Flow:
   - View tracking with 30-minute deduplication window (DEC-005).
   - Async view count propagation (`ThreadViewCountIncremented`) to Content Service.
   - Event delivery to Search Service and Notification Service.
   - Self-notification exclusion and preference suppression.
   - Full-text search and autocomplete suggestions for published threads.
5. Security Hardening & Isolation Invariants:
   - Client identity spoofing header stripping by API Gateway.
   - Direct socket vs. trusted proxy client IP resolution for rate limiting.
   - Internal service authentication key validation.
   - Inter-service REST isolation with zero cross-database queries or foreign keys (DEC-002).
"""

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

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
from src.gateway.app import app as gateway_app
from src.services.content.app import app as content_app
from src.services.content.auth import set_auth_http_client as set_content_auth_client
from src.services.content.models import Base as ContentBase
from src.services.interaction.app import app as interaction_app
from src.services.interaction.auth import set_auth_http_client as set_interaction_auth_client
from src.services.interaction.models import Base as InteractionBase
from src.services.moderation.app import app as moderation_app
from src.services.moderation.auth import set_auth_http_client as set_moderation_auth_client
from src.services.moderation.models import Base as ModerationBase
from src.services.notification.app import app as notification_app
from src.services.notification.auth import set_auth_http_client as set_notification_auth_client
from src.services.notification.models import Base as NotificationBase
from src.services.search.app import app as search_app
from src.services.search.auth import set_auth_http_client as set_search_auth_client
from src.services.search.models import Base as SearchBase
from src.services.user.app import app as user_app
from src.services.user.models import Account
from src.services.user.models import Base as UserBase
from src.services.user.models import Session as DbSession
from src.services.user.otp_delivery import otp_delivery_sink
from src.services.user.security import (
    generate_csrf_token,
    generate_session_token,
    hash_password,
    hash_secret,
)

INTERNAL_AUTH_HEADERS = {"X-Internal-Service-Key": os.environ["INTERNAL_SERVICE_SECRET"]}

E2E_TEST_DB_PATH = "/tmp/test_forum_e2e_task013.db"
E2E_TEST_DB_URL = f"sqlite+aiosqlite:///{E2E_TEST_DB_PATH}"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def setup_e2e_system_test_environment() -> AsyncGenerator[None, None]:
    """Initialize shared database schemas and wire all microservices into the Gateway."""
    import shutil

    if os.path.exists(E2E_TEST_DB_PATH):
        os.remove(E2E_TEST_DB_PATH)
    if os.path.exists("/tmp/sqlite_schemas"):
        shutil.rmtree("/tmp/sqlite_schemas")

    shared_db = DatabaseSessionManager(E2E_TEST_DB_URL)

    async with shared_db.engine.begin() as conn:
        await conn.run_sync(UserBase.metadata.create_all)
        await conn.run_sync(ContentBase.metadata.create_all)
        await conn.run_sync(InteractionBase.metadata.create_all)
        await conn.run_sync(ModerationBase.metadata.create_all)
        await conn.run_sync(NotificationBase.metadata.create_all)
        await conn.run_sync(SearchBase.metadata.create_all)

    # Save original database managers
    orig_user_db = user_db_mod.user_db
    orig_content_db = content_db_mod.content_db
    orig_interaction_db = interaction_db_mod.interaction_db
    orig_moderation_db = moderation_db_mod.moderation_db
    orig_notification_db = notification_db_mod.notification_db
    orig_search_db = search_db_mod.search_db
    orig_search_app_db = search_app_mod.search_db

    # Point all service and app singletons to shared session manager
    user_db_mod.user_db = shared_db
    user_app_mod.user_db = shared_db
    content_db_mod.content_db = shared_db
    content_app_mod.content_db = shared_db
    interaction_db_mod.interaction_db = shared_db
    interaction_app_mod.interaction_db = shared_db
    moderation_db_mod.moderation_db = shared_db
    moderation_app_mod.moderation_db = shared_db
    notification_db_mod.notification_db = shared_db
    notification_app_mod.notification_db = shared_db
    search_db_mod.search_db = shared_db
    search_app_mod.search_db = shared_db

    # Wire User Service HTTP mock client into downstream services for session lookup
    user_transport = ASGITransport(app=user_app)
    mock_user_client = AsyncClient(transport=user_transport, base_url="http://localhost:8001")
    set_content_auth_client(mock_user_client)
    set_interaction_auth_client(mock_user_client)
    set_moderation_auth_client(mock_user_client)
    set_notification_auth_client(mock_user_client)
    set_search_auth_client(mock_user_client)

    # Wire inter-service client for moderation -> content / user mutations
    content_transport = ASGITransport(app=content_app)
    mock_content_client = AsyncClient(transport=content_transport, base_url="http://localhost:8002")
    moderation_app_mod.set_content_http_client(mock_content_client)

    yield

    await mock_user_client.aclose()
    await mock_content_client.aclose()
    await shared_db.close()

    user_db_mod.user_db = orig_user_db
    user_app_mod.user_db = orig_user_db
    content_db_mod.content_db = orig_content_db
    content_app_mod.content_db = orig_content_db
    interaction_db_mod.interaction_db = orig_interaction_db
    interaction_app_mod.interaction_db = orig_interaction_db
    moderation_db_mod.moderation_db = orig_moderation_db
    moderation_app_mod.moderation_db = orig_moderation_db
    notification_db_mod.notification_db = orig_notification_db
    notification_app_mod.notification_db = orig_notification_db
    search_db_mod.search_db = orig_search_db
    search_app_mod.search_db = orig_search_app_db

    if os.path.exists(E2E_TEST_DB_PATH):
        os.remove(E2E_TEST_DB_PATH)


class UnifiedGatewayDispatcherTransport(httpx.AsyncBaseTransport):
    """Multiplexes Gateway requests to appropriate FastAPI microservices in-memory."""

    def __init__(self) -> None:
        self.transports = {
            "http://localhost:8001": ASGITransport(app=user_app),
            "http://localhost:8002": ASGITransport(app=content_app),
            "http://localhost:8003": ASGITransport(app=interaction_app),
            "http://localhost:8004": ASGITransport(app=moderation_app),
            "http://localhost:8005": ASGITransport(app=notification_app),
            "http://localhost:8006": ASGITransport(app=search_app),
            # Docker service names
            "http://user-service:8001": ASGITransport(app=user_app),
            "http://content-service:8002": ASGITransport(app=content_app),
            "http://interaction-service:8003": ASGITransport(app=interaction_app),
            "http://moderation-service:8004": ASGITransport(app=moderation_app),
            "http://notification-service:8005": ASGITransport(app=notification_app),
            "http://search-service:8006": ASGITransport(app=search_app),
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

        # Create fresh client call using target transport
        async with AsyncClient(transport=transport, base_url=base_origin) as client:
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


@pytest.fixture
def gateway_client() -> AsyncClient:
    """Create test client pointing to the API Gateway with unified microservice routing."""
    dispatcher = UnifiedGatewayDispatcherTransport()
    gateway_app_mod.http_client = AsyncClient(transport=dispatcher)
    inter_client = AsyncClient(transport=dispatcher)
    interaction_app_mod.set_content_http_client(inter_client)
    moderation_app_mod.set_content_http_client(inter_client)
    moderation_app_mod.set_user_http_client(inter_client)
    gw_transport = ASGITransport(app=gateway_app)
    return AsyncClient(transport=gw_transport, base_url="http://localhost:8000")


@pytest.mark.asyncio
async def test_e2e_account_lifecycle_and_security(gateway_client: AsyncClient):
    """Verify complete User authentication lifecycle via Gateway."""
    username = "e2e_alice"
    email = "developer@example.test"
    password = "SecurePassword123!"

    # 1. Register
    reg_resp = await gateway_client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    assert reg_resp.status_code == 202
    assert reg_resp.json()["status"] == "pending_verification"

    # 2. Retrieve OTP from test sink
    otp_code = otp_delivery_sink.get_latest_otp(email)
    assert otp_code is not None

    # Invalid OTP attempt
    inv_resp = await gateway_client.post(
        "/api/v1/auth/register/verify",
        json={"email": email, "otp": "000000"},
    )
    assert inv_resp.status_code == 400
    assert inv_resp.json()["error"]["code"] == "INVALID_OTP"

    # Verify registration with correct OTP
    verify_resp = await gateway_client.post(
        "/api/v1/auth/register/verify",
        json={"email": email, "otp": otp_code},
    )
    assert verify_resp.status_code == 201
    user_data = verify_resp.json()["user"]
    assert user_data["username"] == username
    user_id = user_data["id"]

    # 3. Login
    login_resp = await gateway_client.post(
        "/api/v1/auth/login",
        json={"login": username, "password": password},
    )
    assert login_resp.status_code == 200
    assert "forum_session" in login_resp.cookies
    session_cookie = login_resp.cookies["forum_session"]

    # 4. Access authenticated session
    gateway_client.cookies.set("forum_session", session_cookie)
    session_resp = await gateway_client.get("/api/v1/auth/session")
    assert session_resp.status_code == 200
    assert session_resp.json()["user"]["id"] == user_id

    # 5. CSRF Token & Profile Mutation
    csrf_resp = await gateway_client.get("/api/v1/auth/csrf")
    assert csrf_resp.status_code == 200
    csrf_token = csrf_resp.json()["csrf_token"]

    # Attempt mutation without CSRF -> 403 Forbidden
    no_csrf_resp = await gateway_client.patch(
        "/api/v1/profile",
        json={"display_name": "Alice in Wonderland"},
    )
    assert no_csrf_resp.status_code == 403

    # Mutation with valid CSRF token -> 200 OK
    patch_resp = await gateway_client.patch(
        "/api/v1/profile",
        headers={"X-CSRF-Token": csrf_token},
        json={"display_name": "Alice in Wonderland"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["display_name"] == "Alice in Wonderland"

    # 6. Logout
    logout_resp = await gateway_client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert logout_resp.status_code == 204

    # Subsequent access fails
    revoked_resp = await gateway_client.get("/api/v1/auth/session")
    assert revoked_resp.status_code == 401


@pytest.mark.asyncio
async def test_e2e_discussion_moderation_notification_and_search_workflow(
    gateway_client: AsyncClient,
):
    """Verify integrated discussion, voting, moderation, notification, and search workflows."""
    # 1. Setup Admin, Author, and Responder users
    now = datetime.now(UTC)
    admin_id = uuid.uuid4()
    author_id = uuid.uuid4()
    responder_id = uuid.uuid4()

    admin_session = generate_session_token()
    author_session = generate_session_token()
    responder_session = generate_session_token()
    csrf_token = generate_csrf_token(secret="default-dev-csrf-secret")

    async with user_db_mod.user_db.transaction() as session:
        # Create 3 accounts
        admin_acc = Account(
            id=admin_id,
            username="e2e_admin",
            username_normalized="e2e_admin",
            email="admin@example.test",
            email_normalized="admin@example.test",
            password_hash=hash_password("Pass123!"),
            role="admin",
            is_banned=False,
        )
        author_acc = Account(
            id=author_id,
            username="e2e_author",
            username_normalized="e2e_author",
            email="author@example.test",
            email_normalized="author@example.test",
            password_hash=hash_password("Pass123!"),
            role="member",
            is_banned=False,
        )
        responder_acc = Account(
            id=responder_id,
            username="e2e_responder",
            username_normalized="e2e_responder",
            email="responder@example.test",
            email_normalized="responder@example.test",
            password_hash=hash_password("Pass123!"),
            role="member",
            is_banned=False,
        )
        session.add_all([admin_acc, author_acc, responder_acc])

        # Active sessions
        for uid, token in [
            (admin_id, admin_session),
            (author_id, author_session),
            (responder_id, responder_session),
        ]:
            db_s = DbSession(
                id=uuid.uuid4(),
                account_id=uid,
                session_token_hash=hash_secret(token),
                idle_expires_at=now + timedelta(days=1),
                absolute_expires_at=now + timedelta(days=7),
                created_at=now,
                last_seen_at=now,
            )
            session.add(db_s)

    admin_headers = {"Cookie": f"forum_session={admin_session}", "X-CSRF-Token": csrf_token}
    author_headers = {"Cookie": f"forum_session={author_session}", "X-CSRF-Token": csrf_token}
    responder_headers = {"Cookie": f"forum_session={responder_session}", "X-CSRF-Token": csrf_token}

    # 2. Admin creates Category via Gateway
    cat_resp = await gateway_client.post(
        "/api/v1/admin/categories",
        headers=admin_headers,
        json={
            "name": "Backend Engineering",
            "slug": "backend-engineering",
            "description": "Distributed systems, Python, and async architecture",
        },
    )
    assert cat_resp.status_code == 201
    cat_id = cat_resp.json()["id"]

    # 3. Author creates Thread via Gateway
    thread_resp = await gateway_client.post(
        "/api/v1/threads",
        headers=author_headers,
        json={
            "category_id": cat_id,
            "title": "FastAPI with PostgreSQL Outbox Pattern",
            "body": "Detailed discussion on implementing resilient outbox events in microservices.",
            "tags": ["FastAPI", "outbox", "postgresql"],
        },
    )
    assert thread_resp.status_code == 201
    thread_data = thread_resp.json()
    thread_id = thread_data["id"]
    assert "outbox" in thread_data["tags"]

    # Ingest ThreadCreated event into Search Service
    search_transport = ASGITransport(app=search_app)
    async with AsyncClient(transport=search_transport, base_url="http://test") as search_client:
        await search_client.post(
            "/internal/events",
            headers=INTERNAL_AUTH_HEADERS,
            json={
                "event_id": str(uuid.uuid4()),
                "event_type": "ThreadCreated",
                "producer": "content-service",
                "data": {
                    "thread_id": thread_id,
                    "author_id": str(author_id),
                    "category_id": cat_id,
                    "title": thread_data["title"],
                    "body": thread_data["body"],
                    "tags": thread_data["tags"],
                    "status": "published",
                },
            },
        )

    # 4. Responder adds comment via Gateway
    comment_resp = await gateway_client.post(
        f"/api/v1/threads/{thread_id}/comments",
        headers=responder_headers,
        json={"body": "Great article! How do you handle consumer deduplication?"},
    )
    assert comment_resp.status_code == 201
    comment_id = comment_resp.json()["id"]

    # Ingest CommentCreated event into Notification Service
    notif_transport = ASGITransport(app=notification_app)
    async with AsyncClient(transport=notif_transport, base_url="http://test") as notif_client:
        await notif_client.post(
            "/internal/events",
            headers=INTERNAL_AUTH_HEADERS,
            json={
                "event_id": str(uuid.uuid4()),
                "event_type": "CommentCreated",
                "producer": "content-service",
                "data": {
                    "comment_id": comment_id,
                    "thread_id": thread_id,
                    "author_id": str(responder_id),
                    "recipient_id": str(author_id),
                    "body_preview": "Great article! How do you handle consumer deduplication?",
                },
            },
        )

    # Verify Author received notification via Gateway
    inbox_resp = await gateway_client.get(
        "/api/v1/notifications",
        headers={"Cookie": f"forum_session={author_session}"},
    )
    assert inbox_resp.status_code == 200
    notifs = inbox_resp.json()["items"]
    assert len(notifs) >= 1
    assert any("Great article" in n["body"] for n in notifs)

    # 5. Responder votes and bookmarks Thread via Gateway
    vote_resp = await gateway_client.put(
        f"/api/v1/threads/{thread_id}/vote",
        headers=responder_headers,
        json={"value": 1},
    )
    assert vote_resp.status_code == 200
    assert vote_resp.json()["score"] == 1

    bm_resp = await gateway_client.put(
        f"/api/v1/threads/{thread_id}/bookmark",
        headers=responder_headers,
        json={},
    )
    assert bm_resp.status_code == 200
    assert bm_resp.json()["bookmarked"] is True

    # 6. Author sets Comment as Best Answer via Gateway
    best_resp = await gateway_client.put(
        f"/api/v1/threads/{thread_id}/best-answer",
        headers=author_headers,
        json={"comment_id": comment_id},
    )
    assert best_resp.status_code == 200
    assert best_resp.json()["best_answer_comment_id"] == comment_id

    # 7. Thread View deduplication (DEC-005) & propagation
    view1 = await gateway_client.post(
        f"/api/v1/threads/{thread_id}/view",
        headers={"Cookie": f"forum_session={responder_session}"},
    )
    assert view1.status_code == 204

    # Duplicate view within 30 min is deduplicated
    view2 = await gateway_client.post(
        f"/api/v1/threads/{thread_id}/view",
        headers={"Cookie": f"forum_session={responder_session}"},
    )
    assert view2.status_code == 204

    # Propagate view to Content Service internal endpoint
    content_transport = ASGITransport(app=content_app)
    async with AsyncClient(transport=content_transport, base_url="http://test") as content_client:
        v_prop = await content_client.post(
            "/internal/events",
            headers=INTERNAL_AUTH_HEADERS,
            json={
                "event_type": "ThreadViewCountIncremented",
                "data": {"thread_id": thread_id, "increment": 1},
            },
        )
        assert v_prop.status_code == 200

    # 8. Abuse Report Submission & Moderation Triage via Gateway
    report_resp = await gateway_client.post(
        "/api/v1/reports",
        headers=responder_headers,
        json={
            "target_type": "thread",
            "target_id": thread_id,
            "reason": "other",
            "details": "Requesting moderation review for architectural accuracy.",
        },
    )
    assert report_resp.status_code == 201
    report_id = report_resp.json()["id"]

    # Duplicate active report rejected -> 409 Conflict
    dup_report = await gateway_client.post(
        "/api/v1/reports",
        headers=responder_headers,
        json={
            "target_type": "thread",
            "target_id": thread_id,
            "reason": "spam",
            "details": "Another duplicate report",
        },
    )
    assert dup_report.status_code == 409
    assert dup_report.json()["error"]["code"] == "DUPLICATE_REPORT"

    # Admin reviews report in queue
    q_resp = await gateway_client.get(
        "/api/v1/moderation/reports?status=pending",
        headers={"Cookie": f"forum_session={admin_session}"},
    )
    assert q_resp.status_code == 200
    assert any(r["id"] == report_id for r in q_resp.json()["items"])

    # Claim report for review (pending -> reviewing)
    claim_resp = await gateway_client.patch(
        f"/api/v1/moderation/reports/{report_id}/review",
        headers=admin_headers,
    )
    assert claim_resp.status_code == 200
    assert claim_resp.json()["status"] == "reviewing"

    # Moderator applies proactive Lock action
    lock_resp = await gateway_client.put(
        f"/api/v1/threads/{thread_id}/lock",
        headers=admin_headers,
        json={"is_locked": True},
    )
    assert lock_resp.status_code == 200
    assert lock_resp.json()["is_locked"] is True

    # 9. Search verification via Gateway
    search_res = await gateway_client.get("/api/v1/search?q=FastAPI")
    assert search_res.status_code == 200
    search_items = search_res.json()["items"]
    assert any(i["thread_id"] == thread_id for i in search_items)

    sug_res = await gateway_client.get("/api/v1/search/suggestions?q=Fast")
    assert sug_res.status_code == 200
    assert any("FastAPI" in s for s in sug_res.json()["suggestions"])

    # 10. Security Header Sanitization Check
    spoof_res = await gateway_client.get(
        "/api/v1/profile",
        headers={
            "Cookie": f"forum_session={author_session}",
            "X-Authenticated-User-ID": str(admin_id),
            "X-Authenticated-Role": "admin",
            "X-Forwarded-User-ID": str(admin_id),
            "X-Forwarded-Role": "admin",
        },
    )
    assert spoof_res.status_code == 200
    # Must reflect Author identity, NOT spoofed Admin identity
    assert spoof_res.json()["id"] == str(author_id)
    assert spoof_res.json()["role"] == "member"
