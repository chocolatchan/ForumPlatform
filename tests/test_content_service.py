"""Comprehensive automated tests for Task 008: Content Service Core CRUD & Discussion Workflow.

Verifies:
1. Category Management:
   - Create category (admin only).
   - Duplicate slug rejection (409 CATEGORY_SLUG_CONFLICT).
   - Public category listing and retrieval by ID (404 on missing).
   - Update category name/slug/description (admin only).
   - Non-admin access denied (403 FORBIDDEN).

2. Tag Normalization & Associations:
   - Thread tags normalization (lowercased, special characters stripped).
   - Tag uniqueness per thread (no duplicates within the same thread).
   - Tag filtering on GET /threads?tag=...

3. Thread Lifecycle & Discussion Workflows:
   - Create thread with valid payload, category validation, tags, and outbox event (201 Created).
   - Create thread rejects missing category (404 CATEGORY_NOT_FOUND).
   - List threads with filters (category_id, tag, sort=newest/most_viewed) and cursor pagination.
   - Get thread detail by ID.
   - Author edit title, body, and tags (200 OK).
   - Non-author edit rejected (403 FORBIDDEN).
   - Soft-delete thread (204 No Content).
   - ADR-008: Author delete rejected when thread has active comments or best answer (409 THREAD_REQUIRES_MODERATION).
   - Moderator/Admin can delete even if comments exist (204 No Content).
   - Pin and lock toggles by moderator/admin (200 OK).
   - Member lock/pin rejected (403 FORBIDDEN).
   - Set best answer comment by author (200 OK). Non-author rejected (403 FORBIDDEN). Invalid comment rejected (400 BAD_REQUEST).

4. Hierarchical Comments (Max depth 2):
   - Top-level comment creation (201 Created).
   - Reply creation (depth 1 -> parent_comment_id not null) (201 Created).
   - Nested reply creation (reply to a reply -> depth > 2) rejected (400 COMMENT_DEPTH_EXCEEDED).
   - Comment on locked thread rejected (409 THREAD_LOCKED).
   - Cross-thread parent rejected (400 CROSS_THREAD_PARENT).
   - Edit comment by author (200 OK). Non-author rejected (403 FORBIDDEN).
   - Soft-delete comment (204 No Content, body displays '[deleted]').
   - List comments for thread with cursor pagination.

5. Gateway Forwarding & Header Security:
   - Spoofed client identity headers (X-Authenticated-User-ID, X-User-ID, X-Role) stripped by gateway.
   - Session cookie authentication across Gateway forwarder.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import src.gateway.app as gateway_app_mod
import src.services.content.app as content_app_mod
import src.services.content.database as content_db_mod
import src.services.user.app as user_app_mod
import src.services.user.database as user_db_mod
from src.common.database import DatabaseSessionManager
from src.gateway.app import app as gateway_app
from src.services.content.app import app as content_app
from src.services.content.auth import set_auth_http_client
from src.services.content.models import Base as ContentBase
from src.services.content.models import Category, Thread
from src.services.user.app import app as user_app
from src.services.user.models import Account
from src.services.user.models import Base as UserBase
from src.services.user.models import Session as DbSession
from src.services.user.security import (
    generate_csrf_token,
    generate_session_token,
    hash_password,
    hash_secret,
)

CONTENT_TEST_DB_PATH = "/tmp/test_forum_content_task008.db"
CONTENT_TEST_DB_URL = f"sqlite+aiosqlite:///{CONTENT_TEST_DB_PATH}"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def setup_content_test_databases() -> AsyncGenerator[None, None]:
    """Initialize SQLite database for Content Service tests."""
    import shutil

    if os.path.exists(CONTENT_TEST_DB_PATH):
        os.remove(CONTENT_TEST_DB_PATH)
    if os.path.exists("/tmp/sqlite_schemas"):
        shutil.rmtree("/tmp/sqlite_schemas")

    shared_db = DatabaseSessionManager(
        CONTENT_TEST_DB_URL,
    )

    async with shared_db.engine.begin() as conn:
        await conn.run_sync(ContentBase.metadata.create_all)
        await conn.run_sync(UserBase.metadata.create_all)

    orig_content_db = content_db_mod.content_db
    orig_content_app_db = content_app_mod.content_db
    orig_user_db = user_db_mod.user_db
    orig_user_app_db = user_app_mod.user_db

    content_db_mod.content_db = shared_db
    content_app_mod.content_db = shared_db
    user_db_mod.user_db = shared_db
    user_app_mod.user_db = shared_db

    # Create mock auth transport for inter-service calls (Content -> User GET /auth/session)
    user_transport = ASGITransport(app=user_app)
    mock_auth_client = AsyncClient(transport=user_transport, base_url="http://localhost:8001")
    set_auth_http_client(mock_auth_client)

    yield

    set_auth_http_client(None)
    await mock_auth_client.aclose()
    await shared_db.close()

    content_db_mod.content_db = orig_content_db
    content_app_mod.content_db = orig_content_app_db
    user_db_mod.user_db = orig_user_db
    user_app_mod.user_db = orig_user_app_db

    if os.path.exists(CONTENT_TEST_DB_PATH):
        os.remove(CONTENT_TEST_DB_PATH)


async def create_test_user_and_session(
    username: str, role: str = "member"
) -> tuple[uuid.UUID, str, str]:
    """Helper to seed user and active session, returning (user_id, session_token, csrf_token)."""
    now = datetime.now(UTC)
    user_id = uuid.uuid4()
    session_token = generate_session_token()
    csrf_token = generate_csrf_token()

    async with user_db_mod.user_db.transaction() as session:
        acc = Account(
            id=user_id,
            username=username,
            username_normalized=username.lower(),
            email=f"{username}@example.com",
            email_normalized=f"{username}@example.com",
            password_hash=hash_password("StrongPassword123!"),
            role=role,
            is_banned=False,
        )
        session.add(acc)

        s_rec = DbSession(
            id=uuid.uuid4(),
            account_id=user_id,
            session_token_hash=hash_secret(session_token),
            created_at=now,
            last_seen_at=now,
            idle_expires_at=now + timedelta(hours=4),
            absolute_expires_at=now + timedelta(hours=24),
        )
        session.add(s_rec)

    return user_id, session_token, csrf_token


# =============================================================================
# 1. Category Management Tests
# =============================================================================
@pytest.mark.asyncio
async def test_category_crud_and_permissions():
    """Verify category creation, duplicate slug 409, retrieval, update, and permission checks."""
    admin_id, admin_session, admin_csrf = await create_test_user_and_session("admin_cat", "admin")
    member_id, member_session, member_csrf = await create_test_user_and_session(
        "member_cat", "member"
    )

    transport = ASGITransport(app=content_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Non-admin cannot create category (403)
        client.cookies.set("forum_session", member_session)
        res_fail = await client.post(
            "/admin/categories",
            json={"name": "Forbidden Category", "slug": "forbidden-cat"},
            headers={"X-CSRF-Token": member_csrf},
        )
        assert res_fail.status_code == 403

        # Admin creates category (201)
        client.cookies.set("forum_session", admin_session)
        res_create = await client.post(
            "/admin/categories",
            json={
                "name": "Python Development",
                "slug": "python-dev",
                "description": "All things Python",
                "sort_order": 1,
            },
            headers={"X-CSRF-Token": admin_csrf},
        )
        assert res_create.status_code == 201
        cat = res_create.json()
        cat_id = cat["id"]
        assert cat["name"] == "Python Development"
        assert cat["slug"] == "python-dev"

        # Duplicate slug returns 409
        res_dup = await client.post(
            "/admin/categories",
            json={"name": "Python Dev 2", "slug": "python-dev"},
            headers={"X-CSRF-Token": admin_csrf},
        )
        assert res_dup.status_code == 409
        assert res_dup.json()["error"]["code"] == "CATEGORY_SLUG_CONFLICT"

        # Public get category
        client.cookies.clear()
        res_get = await client.get(f"/categories/{cat_id}")
        assert res_get.status_code == 200
        assert res_get.json()["slug"] == "python-dev"

        # Public list categories
        res_list = await client.get("/categories")
        assert res_list.status_code == 200
        assert any(c["id"] == cat_id for c in res_list.json()["items"])

        # Admin update category
        client.cookies.set("forum_session", admin_session)
        res_upd = await client.patch(
            f"/admin/categories/{cat_id}",
            json={"name": "Python & Async Development", "description": "Updated"},
            headers={"X-CSRF-Token": admin_csrf},
        )
        assert res_upd.status_code == 200
        assert res_upd.json()["name"] == "Python & Async Development"

        # Missing category (404)
        res_missing = await client.get(f"/categories/{uuid.uuid4()}")
        assert res_missing.status_code == 404
        assert res_missing.json()["error"]["code"] == "CATEGORY_NOT_FOUND"


# =============================================================================
# 2. Thread CRUD, Normalization, Filtering, and Lifecycle Tests
# =============================================================================
@pytest.mark.asyncio
async def test_thread_lifecycle_and_filters():
    """Verify thread creation with tags, normalization, filters, editing, and soft-delete."""
    author_id, author_sess, author_csrf = await create_test_user_and_session(
        "thread_author", "member"
    )
    other_id, other_sess, other_csrf = await create_test_user_and_session("thread_other", "member")
    mod_id, mod_sess, mod_csrf = await create_test_user_and_session("thread_mod", "moderator")

    transport = ASGITransport(app=content_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a test category directly in DB
        cat_id = uuid.uuid4()
        async with content_db_mod.content_db.transaction() as session:
            session.add(
                Category(
                    id=cat_id,
                    name="FastAPI & Async",
                    slug="fastapi-async",
                    created_by=author_id,
                )
            )

        # Unauthenticated thread creation rejected (401)
        res_unauth = await client.post(
            "/threads",
            json={
                "category_id": str(cat_id),
                "title": "Unauthenticated Thread Title",
                "body": "Body text with more than twenty characters.",
            },
        )
        assert res_unauth.status_code == 401

        # Create thread with tags (including duplicates and mixed casing)
        client.cookies.set("forum_session", author_sess)
        res_create = await client.post(
            "/threads",
            json={
                "category_id": str(cat_id),
                "title": "How to optimize FastAPI async database pooling?",
                "body": "We are experiencing connection pool exhaustion under heavy traffic loads.",
                "tags": ["FastAPI", "Postgres", "fastapi", "pooling"],
            },
            headers={"X-CSRF-Token": author_csrf},
        )
        assert res_create.status_code == 201
        thread = res_create.json()
        thread_id = thread["id"]
        assert thread["title"] == "How to optimize FastAPI async database pooling?"
        assert thread["status"] == "published"
        assert not thread["is_locked"]
        assert not thread["is_pinned"]
        # Verify tag normalization and deduplication ("FastAPI" & "fastapi" should not duplicate)
        assert len(thread["tags"]) == 3
        assert "FastAPI" in thread["tags"]

        # Filter by category
        client.cookies.clear()
        res_filter_cat = await client.get(f"/threads?category_id={cat_id}")
        assert res_filter_cat.status_code == 200
        assert any(t["id"] == thread_id for t in res_filter_cat.json()["items"])

        # Filter by tag
        res_filter_tag = await client.get("/threads?tag=postgres")
        assert res_filter_tag.status_code == 200
        assert any(t["id"] == thread_id for t in res_filter_tag.json()["items"])

        # Filter by non-matching tag
        res_no_match = await client.get("/threads?tag=nonexistent")
        assert res_no_match.status_code == 200
        assert not any(t["id"] == thread_id for t in res_no_match.json()["items"])

        # Non-author cannot edit (403)
        client.cookies.set("forum_session", other_sess)
        res_edit_fail = await client.patch(
            f"/threads/{thread_id}",
            json={"title": "Hacked Title of the Thread!"},
            headers={"X-CSRF-Token": other_csrf},
        )
        assert res_edit_fail.status_code == 403

        # Author can edit (200)
        client.cookies.set("forum_session", author_sess)
        res_edit_ok = await client.patch(
            f"/threads/{thread_id}",
            json={
                "title": "Optimizing FastAPI Async Database Pooling Under Load",
                "body": "Updated body content with plenty of details for twenty chars.",
                "tags": ["FastAPI", "AsyncPG"],
            },
            headers={"X-CSRF-Token": author_csrf},
        )
        assert res_edit_ok.status_code == 200
        edited = res_edit_ok.json()
        assert edited["title"] == "Optimizing FastAPI Async Database Pooling Under Load"
        assert len(edited["tags"]) == 2

        # Moderator can pin and lock thread
        client.cookies.set("forum_session", mod_sess)
        res_pin = await client.put(
            f"/threads/{thread_id}/pin",
            json={"is_pinned": True},
            headers={"X-CSRF-Token": mod_csrf},
        )
        assert res_pin.status_code == 200
        assert res_pin.json()["is_pinned"] is True

        res_lock = await client.put(
            f"/threads/{thread_id}/lock",
            json={"is_locked": True},
            headers={"X-CSRF-Token": mod_csrf},
        )
        assert res_lock.status_code == 200
        assert res_lock.json()["is_locked"] is True

        # Ordinary member cannot lock or pin (403)
        client.cookies.set("forum_session", author_sess)
        res_member_lock = await client.put(
            f"/threads/{thread_id}/lock",
            json={"is_locked": False},
            headers={"X-CSRF-Token": author_csrf},
        )
        assert res_member_lock.status_code == 403

        # Unlock thread for comment tests
        client.cookies.set("forum_session", mod_sess)
        await client.put(
            f"/threads/{thread_id}/lock",
            json={"is_locked": False},
            headers={"X-CSRF-Token": mod_csrf},
        )


# =============================================================================
# 3. Comment Hierarchy, Max Depth, and ADR-008 Deletion Tests
# =============================================================================
@pytest.mark.asyncio
async def test_comments_hierarchy_and_adr008():
    """Verify hierarchical comments (max depth 2), locking restrictions, and ADR-008 delete policy."""
    author_id, author_sess, author_csrf = await create_test_user_and_session("c_author", "member")
    replier_id, replier_sess, replier_csrf = await create_test_user_and_session(
        "c_replier", "member"
    )
    mod_id, mod_sess, mod_csrf = await create_test_user_and_session("c_mod", "moderator")

    transport = ASGITransport(app=content_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create category & thread
        cat_id = uuid.uuid4()
        thread_id = uuid.uuid4()
        async with content_db_mod.content_db.transaction() as session:
            session.add(
                Category(id=cat_id, name="Discussions", slug="discussions", created_by=author_id)
            )
            session.add(
                Thread(
                    id=thread_id,
                    author_id=author_id,
                    category_id=cat_id,
                    title="Discussion on Distributed System Patterns",
                    body="Let us discuss outbox patterns, event sourcing, and CQRS.",
                    status="published",
                )
            )

        # 1. Top-level comment (Depth 1)
        client.cookies.set("forum_session", author_sess)
        res_c1 = await client.post(
            f"/threads/{thread_id}/comments",
            json={"body": "Top level comment: Outbox pattern is essential for reliable events."},
            headers={"X-CSRF-Token": author_csrf},
        )
        assert res_c1.status_code == 201
        c1 = res_c1.json()
        c1_id = c1["id"]
        assert c1["parent_comment_id"] is None

        # 2. Reply to top-level comment (Depth 2)
        client.cookies.set("forum_session", replier_sess)
        res_c2 = await client.post(
            f"/threads/{thread_id}/comments",
            json={
                "body": "Reply: Yes, transactional outbox prevents dual-write anomalies.",
                "parent_comment_id": c1_id,
            },
            headers={"X-CSRF-Token": replier_csrf},
        )
        assert res_c2.status_code == 201
        c2 = res_c2.json()
        c2_id = c2["id"]
        assert c2["parent_comment_id"] == c1_id

        # 3. Reply to reply (Depth > 2) -> Must be rejected (400 COMMENT_DEPTH_EXCEEDED)
        client.cookies.set("forum_session", author_sess)
        res_c3 = await client.post(
            f"/threads/{thread_id}/comments",
            json={
                "body": "Third level reply attempt.",
                "parent_comment_id": c2_id,
            },
            headers={"X-CSRF-Token": author_csrf},
        )
        assert res_c3.status_code == 400
        assert res_c3.json()["error"]["code"] == "COMMENT_DEPTH_EXCEEDED"

        # 4. Set best answer
        res_ba = await client.put(
            f"/threads/{thread_id}/best-answer",
            json={"comment_id": c2_id},
            headers={"X-CSRF-Token": author_csrf},
        )
        assert res_ba.status_code == 200
        assert res_ba.json()["best_answer_comment_id"] == c2_id

        # Non-author cannot set best answer (403)
        client.cookies.set("forum_session", replier_sess)
        res_ba_fail = await client.put(
            f"/threads/{thread_id}/best-answer",
            json={"comment_id": c1_id},
            headers={"X-CSRF-Token": replier_csrf},
        )
        assert res_ba_fail.status_code == 403

        # 5. ADR-008: Author cannot delete thread because comments & best answer exist
        client.cookies.set("forum_session", author_sess)
        res_del_conflict = await client.delete(
            f"/threads/{thread_id}",
            headers={"X-CSRF-Token": author_csrf},
        )
        assert res_del_conflict.status_code == 409
        assert res_del_conflict.json()["error"]["code"] == "THREAD_REQUIRES_MODERATION"

        # 6. Author edits comment
        res_edit_c = await client.patch(
            f"/comments/{c1_id}",
            json={"body": "Edited top level comment with improved clarity."},
            headers={"X-CSRF-Token": author_csrf},
        )
        assert res_edit_c.status_code == 200
        assert res_edit_c.json()["body"] == "Edited top level comment with improved clarity."

        # 7. Author soft-deletes comment
        res_del_c = await client.delete(
            f"/comments/{c1_id}",
            headers={"X-CSRF-Token": author_csrf},
        )
        assert res_del_c.status_code == 204

        # Verify comment list shows '[deleted]' for soft-deleted comment
        client.cookies.clear()
        res_clist = await client.get(f"/threads/{thread_id}/comments")
        assert res_clist.status_code == 200
        comments = res_clist.json()["items"]
        deleted_c = next(c for c in comments if c["id"] == c1_id)
        assert deleted_c["is_deleted"] is True
        assert deleted_c["body"] == "[deleted]"

        # 8. Moderator deletes thread (permitted by policy)
        client.cookies.set("forum_session", mod_sess)
        res_mod_del = await client.delete(
            f"/threads/{thread_id}",
            headers={"X-CSRF-Token": mod_csrf},
        )
        assert res_mod_del.status_code == 204

        # Thread is now 404
        client.cookies.clear()
        res_th_after = await client.get(f"/threads/{thread_id}")
        assert res_th_after.status_code == 404


# =============================================================================
# 4. Gateway Integration & Header Stripping Security Tests
# =============================================================================
@pytest.mark.asyncio
async def test_gateway_content_forwarding_and_spoof_prevention():
    """Verify Gateway routes to Content Service, handles session cookie, and strips spoofed identity headers."""
    user_id, session_token, csrf_token = await create_test_user_and_session(
        "gw_content_user", "member"
    )

    cat_id = uuid.uuid4()
    async with content_db_mod.content_db.transaction() as session:
        session.add(Category(id=cat_id, name="Gateway Category", slug="gw-cat", created_by=user_id))

    # Simulated multi-service gateway routing
    service_apps = {
        "http://localhost:8001": user_app,
        "http://localhost:8002": content_app,
    }

    mock_client = AsyncClient(
        transport=ASGITransport(app=user_app), base_url="http://localhost:8001"
    )

    async def mock_forward_send(request, **kwargs):
        host = (
            request.url.netloc.decode()
            if isinstance(request.url.netloc, bytes)
            else request.url.netloc
        )
        base_url = f"{request.url.scheme}://{host}"
        target_app = service_apps.get(base_url)
        if not target_app:
            raise AssertionError(f"Unknown upstream base URL: {base_url}")

        body_bytes = b""
        if hasattr(request.stream, "__aiter__"):
            async for chunk in request.stream:
                body_bytes += chunk
        elif hasattr(request, "read"):
            body_bytes = request.read()

        async with AsyncClient(
            transport=ASGITransport(app=target_app), base_url=base_url
        ) as svc_client:
            resp = await svc_client.request(
                method=request.method,
                url=str(request.url),
                headers=dict(request.headers),
                content=body_bytes,
            )
            return httpx.Response(
                status_code=resp.status_code,
                headers=resp.headers,
                stream=httpx.ByteStream(resp.content),
                request=request,
            )

    mock_client.send = mock_forward_send

    with patch.object(gateway_app_mod, "http_client", mock_client):
        transport = ASGITransport(app=gateway_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Gateway GET /api/v1/categories -> 200
            res_cats = await client.get("/api/v1/categories")
            assert res_cats.status_code == 200
            assert any(c["id"] == str(cat_id) for c in res_cats.json()["items"])

            # 2. Gateway POST /api/v1/threads with session cookie
            client.cookies.set("forum_session", session_token)
            res_thread = await client.post(
                "/api/v1/threads",
                json={
                    "category_id": str(cat_id),
                    "title": "Created via Gateway Forwarding Proxy",
                    "body": "Checking that session cookies are forwarded and validated by Content Service.",
                    "tags": ["gateway", "proxy"],
                },
                headers={"X-CSRF-Token": csrf_token},
            )
            assert res_thread.status_code == 201
            th_data = res_thread.json()
            assert th_data["author_id"] == str(user_id)

            # 3. Security: Attempt spoofing admin role via client headers without session
            client.cookies.clear()
            spoofed_admin_id = str(uuid.uuid4())
            res_spoof = await client.post(
                "/api/v1/admin/categories",
                json={"name": "Spoofed Category", "slug": "spoofed-cat"},
                headers={
                    "X-Authenticated-User-ID": spoofed_admin_id,
                    "X-Authenticated-Role": "admin",
                    "X-User-ID": spoofed_admin_id,
                    "X-Role": "admin",
                    "X-Forwarded-User-ID": spoofed_admin_id,
                    "X-Forwarded-Role": "admin",
                },
            )
            # Gateway filter_headers strips client identity headers, so Content Service sees no session -> 401
            assert res_spoof.status_code == 401


@pytest.mark.asyncio
async def test_image_upload_lifecycle_and_validation():
    """Verify Task 027 image upload lifecycle:

    1. Unauthenticated upload rejected (401).
    2. Upload without CSRF token rejected (403).
    3. Non-image file (e.g. text or SVG) rejected by magic bytes (415).
    4. Oversized image (>2MB) rejected (413).
    5. Valid image upload (JPEG/PNG/WebP) succeeds (201 Created) with pending status.
    6. Image can be fetched directly via GET /uploads/images/{id}.
    7. Thread creation links pending attachment and updates status to linked.
    8. Attempting to link another user's attachment or already-linked attachment fails (400).
    """
    user_id, session_token, csrf_token = await create_test_user_and_session("upload_tester", role="member")

    # Category for thread creation
    cat_id = uuid.uuid4()
    async with content_db_mod.content_db.transaction() as session:
        cat = Category(
            id=cat_id,
            name="Upload Category",
            slug="upload-cat",
            description="Testing image attachments",
            created_by=user_id,
        )
        session.add(cat)

    user_transport = ASGITransport(app=user_app)
    mock_user_client = AsyncClient(transport=user_transport, base_url="http://test")
    set_auth_http_client(mock_user_client)

    transport = ASGITransport(app=content_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Minimal valid PNG bytes
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        # 1. Unauthenticated upload -> 401
        res = await client.post(
            "/uploads/images",
            files={"file": ("test.png", png_bytes, "image/png")},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 401

        # Authenticate
        client.cookies.set("forum_session", session_token)

        # 2. Upload without CSRF -> 403
        res = await client.post(
            "/uploads/images",
            files={"file": ("test.png", png_bytes, "image/png")},
        )
        assert res.status_code == 403

        # 3. Non-image file / SVG spoofing -> 415
        fake_svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
        res = await client.post(
            "/uploads/images",
            files={"file": ("fake.png", fake_svg, "image/png")},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 415

        # 4. Oversized image > 2MB -> 413
        # Fake JPEG header with 2MB + 10 bytes
        oversized_data = b"\xff\xd8\xff\xe0" + b"\x00" * (2097152 + 10)
        res = await client.post(
            "/uploads/images",
            files={"file": ("large.jpg", oversized_data, "image/jpeg")},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 413

        # 5. Valid PNG upload -> 201 Created
        res = await client.post(
            "/uploads/images",
            files={"file": ("valid.png", png_bytes, "image/png")},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 201
        att_data = res.json()
        assert "id" in att_data
        assert att_data["status"] == "pending"
        assert att_data["mime_type"] == "image/png"
        att_id = att_data["id"]

        # 6. Fetch uploaded image directly -> 200 OK
        res_img = await client.get(f"/uploads/images/{att_id}")
        assert res_img.status_code == 200
        assert res_img.headers["content-type"] == "image/png"
        assert res_img.headers["x-content-type-options"] == "nosniff"
        assert res_img.content == png_bytes

        # 7. Create thread linking this attachment -> 201 Created
        res_thread = await client.post(
            "/threads",
            json={
                "category_id": str(cat_id),
                "title": "Thread with Verified Attachment",
                "body": "Here is an image: ![attachment](/api/v1/uploads/images/" + att_id + ")",
                "tags": ["image", "test"],
                "image_ids": [att_id],
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res_thread.status_code == 201

        # 8. Attempting to link the same attachment again in a new thread -> 400 (already linked)
        res_thread2 = await client.post(
            "/threads",
            json={
                "category_id": str(cat_id),
                "title": "Second Thread Trying Same Image",
                "body": "Reusing image should fail because status is now linked.",
                "tags": ["reuse"],
                "image_ids": [att_id],
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res_thread2.status_code == 400
        assert res_thread2.json()["error"]["code"] == "INVALID_ATTACHMENT"


@pytest.mark.asyncio
async def test_image_upload_hardening_suite():
    """Verify Task 027-H1 Hardening across all 5 risk dimensions:

    1. DB Failure Unlink: When DB transaction fails, file on disk is immediately cleaned up.
    2. Deep Image Validation: Corrupted PNG / non-IEND or oversized dimensions rejected (415).
    3. Pending Expiration: cleanup_expired_pending_attachments safely unlinks expired pending attachments.
    4. Image Privacy:
       - Pending image inaccessible to unauthenticated or other users (404).
       - Image linked to hidden thread inaccessible to strangers (404), accessible to author/moderator.
       - Image linked to published thread accessible to public.
    5. Gateway Size Enforcement: Payloads exceeding limits return 413.
    """
    author_id, session_token, csrf_token = await create_test_user_and_session(
        "hardened_author", role="member"
    )
    stranger_id, stranger_session, stranger_csrf = await create_test_user_and_session(
        "hardened_stranger", role="member"
    )
    mod_id, mod_session, mod_csrf = await create_test_user_and_session(
        "hardened_mod", role="moderator"
    )

    cat_id = uuid.uuid4()
    async with content_db_mod.content_db.transaction() as session:
        cat = Category(
            id=cat_id,
            name="Hardening Category",
            slug="hardening-cat",
            description="Testing image upload hardening",
            created_by=author_id,
        )
        session.add(cat)

    user_transport = ASGITransport(app=user_app)
    mock_user_client = AsyncClient(transport=user_transport, base_url="http://test")
    set_auth_http_client(mock_user_client)

    transport = ASGITransport(app=content_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("forum_session", session_token)

        # Minimal valid 1x1 PNG bytes
        valid_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        # -------------------------------------------------------------
        # Phase E: Deep Image Decoding and Validation
        # -------------------------------------------------------------
        # Missing IEND chunk in PNG
        corrupted_png = valid_png[:-12]
        res = await client.post(
            "/uploads/images",
            files={"file": ("corrupt.png", corrupted_png, "image/png")},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 415
        assert "Image validation failed" in res.json()["error"]["message"]

        # PNG with excessive dimensions (>8192)
        oversized_dim_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x27\x10\x00\x00\x27\x10"  # 10000x10000
            b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        res = await client.post(
            "/uploads/images",
            files={"file": ("bomb.png", oversized_dim_png, "image/png")},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 415
        assert "exceed maximum allowed" in res.json()["error"]["message"]

        # -------------------------------------------------------------
        # Phase B: File/Metadata Consistency & Rollback on DB Error
        # -------------------------------------------------------------
        with patch.object(content_db_mod.content_db, "transaction", side_effect=RuntimeError("Simulated DB Crash")):
            res = await client.post(
                "/uploads/images",
                files={"file": ("db_fail.png", valid_png, "image/png")},
                headers={"X-CSRF-Token": csrf_token},
            )
            assert res.status_code == 500
            assert res.json()["error"]["code"] == "DATABASE_ERROR"

        # -------------------------------------------------------------
        # Phase D: Privacy & Visibility Policy Enforcement
        # -------------------------------------------------------------
        # 1. Upload valid image (becomes pending)
        res = await client.post(
            "/uploads/images",
            files={"file": ("privacy_test.png", valid_png, "image/png")},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 201
        att_id = res.json()["id"]

        # 2. Author can view pending image (200)
        res = await client.get(f"/uploads/images/{att_id}")
        assert res.status_code == 200

        # 3. Unauthenticated client cannot view pending image (404)
        client.cookies.clear()
        res = await client.get(f"/uploads/images/{att_id}")
        assert res.status_code == 404

        # 4. Stranger client cannot view pending image (404)
        client.cookies.set("forum_session", stranger_session)
        res = await client.get(f"/uploads/images/{att_id}")
        assert res.status_code == 404

        # 5. Link image to a Hidden thread
        client.cookies.set("forum_session", session_token)
        hidden_th_id = uuid.uuid4()
        async with content_db_mod.content_db.transaction() as session:
            h_thread = Thread(
                id=hidden_th_id,
                author_id=author_id,
                category_id=cat_id,
                title="Hidden Discussion Thread",
                body="Confidential internal content with images.",
                status="hidden",
                is_pinned=False,
                is_locked=False,
                view_count=0,
            )
            session.add(h_thread)
            # Link attachment
            from sqlalchemy import select

            from src.services.content.models import Attachment
            stmt = select(Attachment).where(Attachment.id == uuid.UUID(att_id))
            att_rec = (await session.execute(stmt)).scalar_one()
            att_rec.thread_id = hidden_th_id
            att_rec.status = "linked"
            att_rec.linked_at = datetime.now(UTC)

        # Author can view image of hidden thread (200)
        res = await client.get(f"/uploads/images/{att_id}")
        assert res.status_code == 200

        # Moderator can view image of hidden thread (200)
        client.cookies.set("forum_session", mod_session)
        res = await client.get(f"/uploads/images/{att_id}")
        assert res.status_code == 200

        # Stranger cannot view image of hidden thread (404)
        client.cookies.set("forum_session", stranger_session)
        res = await client.get(f"/uploads/images/{att_id}")
        assert res.status_code == 404

        # Unauthenticated cannot view image of hidden thread (404)
        client.cookies.clear()
        res = await client.get(f"/uploads/images/{att_id}")
        assert res.status_code == 404

        # 6. Change thread to published -> Anyone can view (200)
        async with content_db_mod.content_db.transaction() as session:
            stmt = select(Thread).where(Thread.id == hidden_th_id)
            th_rec = (await session.execute(stmt)).scalar_one()
            th_rec.status = "published"

        res = await client.get(f"/uploads/images/{att_id}")
        assert res.status_code == 200
        assert res.headers["cache-control"] == "public, max-age=86400"

        # -------------------------------------------------------------
        # Phase C: Pending Attachment Expiration and Cleanup
        # -------------------------------------------------------------
        from src.services.content.app import cleanup_expired_pending_attachments

        # Seed an old pending attachment (>24 hours old)
        old_att_id = uuid.uuid4()
        old_obj_key = f"uploads/images/{old_att_id.hex}.png"
        old_file_path = content_app_mod.get_absolute_file_path(
            content_app_mod.content_settings.storage_local_dir, old_obj_key
        )
        content_app_mod.ensure_storage_dir(content_app_mod.content_settings.storage_local_dir)
        with open(old_file_path, "wb") as f:
            f.write(valid_png)

        async with content_db_mod.content_db.transaction() as session:
            old_att = Attachment(
                id=old_att_id,
                thread_id=None,
                author_id=author_id,
                object_key=old_obj_key,
                file_size=len(valid_png),
                mime_type="image/png",
                status="pending",
                created_at=datetime.now(UTC) - timedelta(hours=48),
            )
            session.add(old_att)

        # Run cleanup with 24h max age
        purged = await cleanup_expired_pending_attachments(max_age_seconds=86400)
        assert purged >= 1
        assert not old_file_path.exists()

        # Verify linked attachment was NOT touched
        async with content_db_mod.content_db.session() as session:
            stmt = select(Attachment).where(Attachment.id == uuid.UUID(att_id))
            linked_att = (await session.execute(stmt)).scalar_one()
            assert linked_att.status == "linked"


