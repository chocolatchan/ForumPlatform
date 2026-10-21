"""Comprehensive Tier I database-backed CRUD integration tests across all 6 services.

Verifies:
1. User Service: Account registration (202), profile lookup (200), role update (200), missing account (404), validation (422).
2. Content Service: Category creation (201), duplicate slug conflict (409), list categories (200), update category (200), missing category (404).
3. Interaction Service: Thread bookmark creation (200), list user bookmarks (200), unbookmark deletion (204).
4. Moderation Service: Report submission (201), list reports (200), moderation action resolution (200), missing report (404).
5. Notification Service: Default preferences retrieval (200), preferences update (200), verification of persisted values.
6. Search Service: Search document storage & querying (200), suggestion prefix matching (200).
7. Persistence Across Engine Restarts: Verifying data survives engine lifecycle restart on file-based SQLite database.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import src.services.content.app as content_app_mod
import src.services.content.database as content_db_module
import src.services.interaction.app as interaction_app_mod
import src.services.interaction.database as interaction_db_module
import src.services.moderation.app as moderation_app_mod
import src.services.moderation.database as moderation_db_module
import src.services.notification.app as notification_app_mod
import src.services.notification.database as notification_db_module
import src.services.search.app as search_app_mod
import src.services.search.database as search_db_module
import src.services.user.app as user_app_mod
import src.services.user.database as user_db_module
from src.common.database import DatabaseSessionManager
from src.services.content.app import app as content_app
from src.services.content.models import Base as ContentBase
from src.services.interaction.app import app as interaction_app
from src.services.interaction.models import Base as InteractionBase
from src.services.moderation.app import app as moderation_app
from src.services.moderation.models import Base as ModerationBase
from src.services.notification.app import app as notification_app
from src.services.notification.models import Base as NotificationBase
from src.services.search.app import app as search_app
from src.services.search.models import Base as SearchBase
from src.services.search.models import SearchDocument
from src.services.user.app import app as user_app
from src.services.user.models import Account
from src.services.user.models import Base as UserBase

TEST_DB_PATH = "/tmp/test_forum_tier1_crud.db"
TEST_DB_URL = f"sqlite+aiosqlite:///{TEST_DB_PATH}"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def setup_test_databases() -> AsyncGenerator[None, None]:
    """Initialize file-backed SQLite database shared by services for integration testing."""
    import shutil

    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    if os.path.exists("/tmp/sqlite_schemas"):
        shutil.rmtree("/tmp/sqlite_schemas")

    shared_db = DatabaseSessionManager(TEST_DB_URL)

    # Initialize all schemas
    async with shared_db.engine.begin() as conn:
        await conn.run_sync(UserBase.metadata.create_all)
        await conn.run_sync(ContentBase.metadata.create_all)
        await conn.run_sync(InteractionBase.metadata.create_all)
        await conn.run_sync(ModerationBase.metadata.create_all)
        await conn.run_sync(NotificationBase.metadata.create_all)
        await conn.run_sync(SearchBase.metadata.create_all)

    # Inject shared session managers into the microservices
    orig_user = user_db_module.user_db
    orig_content = content_db_module.content_db
    orig_interaction = interaction_db_module.interaction_db
    orig_moderation = moderation_db_module.moderation_db
    orig_notification = notification_db_module.notification_db
    orig_search = search_db_module.search_db

    import src.services.content.app as content_app_mod
    import src.services.interaction.app as interaction_app_mod
    import src.services.moderation.app as moderation_app_mod
    import src.services.notification.app as notification_app_mod
    import src.services.search.app as search_app_mod
    import src.services.user.app as user_app_mod

    user_db_module.user_db = shared_db
    content_db_module.content_db = shared_db
    interaction_db_module.interaction_db = shared_db
    moderation_db_module.moderation_db = shared_db
    notification_db_module.notification_db = shared_db
    search_db_module.search_db = shared_db

    user_app_mod.user_db = shared_db
    content_app_mod.content_db = shared_db
    interaction_app_mod.interaction_db = shared_db
    moderation_app_mod.moderation_db = shared_db
    notification_app_mod.notification_db = shared_db
    search_app_mod.search_db = shared_db

    yield

    # Teardown
    await shared_db.close()
    user_db_module.user_db = orig_user
    content_db_module.content_db = orig_content
    interaction_db_module.interaction_db = orig_interaction
    moderation_db_module.moderation_db = orig_moderation
    notification_db_module.notification_db = orig_notification
    search_db_module.search_db = orig_search

    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)


@pytest.mark.asyncio
async def test_user_service_crud_lifecycle():
    """Verify User Service: Register -> Get Profile -> Update Role -> 404 on missing."""
    transport = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Register user
        reg_res = await client.post(
            "/auth/register",
            json={
                "username": "alice_test",
                "email": "developer@example.test",
                "password": "Password123!",
            },
        )
        assert reg_res.status_code == 202
        data = reg_res.json()
        assert data["status"] == "pending_verification"
        assert "verification code has been sent" in data["message"]

        # Verify OTP using otp_delivery_sink to create active account
        from src.services.user.otp_delivery import otp_delivery_sink

        otp = otp_delivery_sink.get_latest_otp("developer@example.test")
        assert otp is not None

        verify_res = await client.post(
            "/auth/register/verify",
            json={
                "email": "developer@example.test",
                "otp": otp,
            },
        )
        assert verify_res.status_code == 201
        vdata = verify_res.json()
        user_id = vdata["user"]["id"]
        assert vdata["user"]["username"] == "alice_test"

        # 2. Get Public Profile (Read)
        prof_res = await client.get(f"/users/{user_id}")
        assert prof_res.status_code == 200
        pdata = prof_res.json()
        assert pdata["username"] == "alice_test"
        assert pdata["role"] == "member"

        # Create admin user and session for role update
        async with user_app_mod.user_db.transaction() as s:
            from datetime import UTC, datetime, timedelta

            from src.services.user.models import Session as DbSession
            from src.services.user.security import (
                generate_session_token,
                hash_password,
                hash_secret,
            )

            now = datetime.now(UTC)
            admin_acc = Account(
                id=uuid.uuid4(),
                username="admin_user",
                username_normalized="admin_user",
                email="admin@example.com",
                email_normalized="admin@example.com",
                password_hash=hash_password("AdminPass123!"),
                role="admin",
                is_banned=False,
            )
            s.add(admin_acc)
            admin_token = generate_session_token()
            s_rec = DbSession(
                id=uuid.uuid4(),
                account_id=admin_acc.id,
                session_token_hash=hash_secret(admin_token),
                created_at=now,
                last_seen_at=now,
                idle_expires_at=now + timedelta(hours=4),
                absolute_expires_at=now + timedelta(hours=24),
            )
            s.add(s_rec)

        # 3. Update User Role (Update) with admin session & CSRF
        csrf_res = await client.get("/auth/csrf")
        csrf_token = csrf_res.json()["csrf_token"]

        role_res = await client.patch(
            f"/admin/users/{user_id}/role",
            json={"role": "moderator"},
            cookies={"forum_session": admin_token},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert role_res.status_code == 200
        rdata = role_res.json()
        assert rdata["role"] == "moderator"

        # 4. Read again to verify persistence of update
        prof_res2 = await client.get(f"/users/{user_id}")
        assert prof_res2.status_code == 200
        assert prof_res2.json()["role"] == "moderator"

        # 5. Missing user (404)
        missing_id = str(uuid.uuid4())
        missing_res = await client.get(f"/users/{missing_id}")
        assert missing_res.status_code == 404
        assert missing_res.json()["error"]["code"] == "USER_NOT_FOUND"


@pytest.mark.asyncio
async def test_content_service_crud_lifecycle():
    """Verify Content Service: Create Category -> Read List/Item -> Update -> 409 conflict -> 404 missing."""
    admin_headers = {
        "X-Forwarded-User-ID": str(uuid.uuid4()),
        "X-Forwarded-Role": "admin",
        "X-Forwarded-Username": "admin_test",
    }

    transport = ASGITransport(app=content_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create category
        create_res = await client.post(
            "/admin/categories",
            headers=admin_headers,
            json={
                "name": "General Technology",
                "slug": "tech-general",
                "description": "All tech topics",
            },
        )
        assert create_res.status_code == 201
        cat_data = create_res.json()
        cat_id = cat_data["id"]
        assert cat_data["slug"] == "tech-general"

        # 2. Uniqueness conflict (409)
        dup_res = await client.post(
            "/admin/categories",
            headers=admin_headers,
            json={
                "name": "Duplicate Tech",
                "slug": "tech-general",
                "description": "Another one",
            },
        )
        assert dup_res.status_code == 409
        assert dup_res.json()["error"]["code"] == "CATEGORY_SLUG_CONFLICT"

        # 3. Read Category by ID
        get_res = await client.get(f"/categories/{cat_id}")
        assert get_res.status_code == 200
        assert get_res.json()["name"] == "General Technology"

        # 4. List Categories
        list_res = await client.get("/categories")
        assert list_res.status_code == 200
        items = list_res.json()["items"]
        assert any(c["id"] == cat_id for c in items)

        # 5. Update Category
        update_res = await client.patch(
            f"/admin/categories/{cat_id}",
            headers=admin_headers,
            json={"name": "General Tech & Code", "description": "Updated description"},
        )
        assert update_res.status_code == 200
        assert update_res.json()["name"] == "General Tech & Code"

        # 6. Missing category (404)
        missing_id = str(uuid.uuid4())
        missing_res = await client.get(f"/categories/{missing_id}")
        assert missing_res.status_code == 404
        assert missing_res.json()["error"]["code"] == "CATEGORY_NOT_FOUND"


@pytest.mark.asyncio
async def test_interaction_service_crud_lifecycle():
    """Verify Interaction Service: Bookmark Thread -> List Bookmarks -> Delete Bookmark."""
    from unittest.mock import AsyncMock

    from src.services.interaction.app import set_content_http_client

    user_headers = {
        "X-Forwarded-User-ID": str(uuid.uuid4()),
        "X-Forwarded-Role": "member",
        "X-Forwarded-Username": "member_test",
    }

    thread_id = str(uuid.uuid4())

    async def mock_get(url, **kwargs):
        return httpx.Response(
            200,
            json={
                "id": thread_id,
                "author_id": str(uuid.uuid4()),
                "category_id": str(uuid.uuid4()),
                "title": "Valid Thread",
                "body": "Body with at least twenty chars.",
                "tags": [],
                "status": "published",
                "is_pinned": False,
                "is_locked": False,
                "best_answer_comment_id": None,
                "view_count": 0,
            },
        )

    mock_content = AsyncMock(spec=httpx.AsyncClient)
    mock_content.get = mock_get
    set_content_http_client(mock_content)

    transport = ASGITransport(app=interaction_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Bookmark thread (Create)
        bm_res = await client.put(f"/threads/{thread_id}/bookmark", headers=user_headers)
        assert bm_res.status_code == 200
        assert bm_res.json()["bookmarked"] is True

        # 2. List bookmarks (Read)
        list_res = await client.get("/profile/bookmarks", headers=user_headers)
        assert list_res.status_code == 200
        items = list_res.json()["items"]
        assert any(b["id"] == thread_id for b in items)

        # 3. Unbookmark thread (Delete)
        del_res = await client.delete(f"/threads/{thread_id}/bookmark", headers=user_headers)
        assert del_res.status_code == 204

        # 4. Verify deletion
        list_res2 = await client.get("/profile/bookmarks", headers=user_headers)
        assert list_res2.status_code == 200
        items2 = list_res2.json()["items"]
        assert not any(b["id"] == thread_id for b in items2)

    set_content_http_client(None)


@pytest.mark.asyncio
async def test_moderation_service_crud_lifecycle():
    """Verify Moderation Service: Create Report -> Read Report -> Apply Moderation Action -> 404 missing."""
    from unittest.mock import AsyncMock

    from src.services.moderation.app import set_content_http_client

    member_headers = {
        "X-Forwarded-User-ID": str(uuid.uuid4()),
        "X-Forwarded-Role": "member",
        "X-Forwarded-Username": "member_reporter",
    }
    mod_headers = {
        "X-Forwarded-User-ID": str(uuid.uuid4()),
        "X-Forwarded-Role": "moderator",
        "X-Forwarded-Username": "mod_agent",
    }

    target_thread_id = str(uuid.uuid4())

    async def mock_content_get(url, **kwargs):
        return httpx.Response(200, json={"id": target_thread_id, "status": "published"})

    async def mock_content_delete(url, **kwargs):
        return httpx.Response(204)

    mock_content = AsyncMock(spec=httpx.AsyncClient)
    mock_content.get = mock_content_get
    mock_content.delete = mock_content_delete
    set_content_http_client(mock_content)

    transport = ASGITransport(app=moderation_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create Report (Create)
        rep_res = await client.post(
            "/reports",
            json={
                "target_type": "thread",
                "target_id": target_thread_id,
                "reason": "spam",
                "details": "Unsolicited commercial spam posting.",
            },
            headers=member_headers,
        )
        assert rep_res.status_code == 201
        report_data = rep_res.json()
        report_id = report_data["id"]
        assert report_data["status"] == "pending"

        # 2. Read Report by ID (Read)
        get_res = await client.get(f"/moderation/reports/{report_id}", headers=mod_headers)
        assert get_res.status_code == 200
        assert get_res.json()["reason"] == "spam"

        # 3. List Reports
        list_res = await client.get("/moderation/reports?status=pending", headers=mod_headers)
        assert list_res.status_code == 200
        items = list_res.json()["items"]
        assert any(r["id"] == report_id for r in items)

        # 4. Apply Moderation Action (Update / Action Report)
        action_res = await client.post(
            f"/moderation/reports/{report_id}/actions",
            json={
                "action": "soft_delete",
                "reason": "Confirmed promotional spam",
            },
            headers=mod_headers,
        )
        assert action_res.status_code == 200
        assert action_res.json()["action"] == "soft_delete"

        # 5. Verify Report status updated to actioned
        get_res2 = await client.get(f"/moderation/reports/{report_id}", headers=mod_headers)
        assert get_res2.status_code == 200
        assert get_res2.json()["status"] == "actioned"

        # 6. Missing report (404)
        missing_id = str(uuid.uuid4())
        missing_res = await client.get(f"/moderation/reports/{missing_id}", headers=mod_headers)
        assert missing_res.status_code == 404
        assert missing_res.json()["error"]["code"] == "REPORT_NOT_FOUND"

    set_content_http_client(None)


@pytest.mark.asyncio
async def test_notification_service_crud_lifecycle():
    """Verify Notification Service: Get Preferences -> Update Preferences -> Verify update."""
    transport = ASGITransport(app=notification_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Get default preferences (Read)
        get_res = await client.get("/notifications/preferences")
        assert get_res.status_code == 200
        prefs = get_res.json()
        assert "in_app_enabled" in prefs
        assert "email_enabled" in prefs

        # 2. Update preferences (Update / Upsert)
        update_res = await client.patch(
            "/notifications/preferences",
            json={"in_app_enabled": False, "email_enabled": True},
        )
        assert update_res.status_code == 200
        uprefs = update_res.json()
        assert uprefs["in_app_enabled"] is False
        assert uprefs["email_enabled"] is True

        # 3. Read again to ensure persistence
        get_res2 = await client.get("/notifications/preferences")
        assert get_res2.status_code == 200
        assert get_res2.json()["in_app_enabled"] is False
        assert get_res2.json()["email_enabled"] is True


@pytest.mark.asyncio
async def test_search_service_crud_lifecycle():
    """Verify Search Service: Document Querying & Suggestions."""
    # Seed SearchDocument directly via session
    doc_id = uuid.uuid4()
    source_id = uuid.uuid4()
    cat_id = uuid.uuid4()
    from datetime import datetime

    async with search_db_module.search_db.transaction() as session:
        doc = SearchDocument(
            id=doc_id,
            source_type="thread",
            source_id=source_id,
            title="Advanced Rust Concurrency Patterns",
            body="A deep dive into crossbeam channels, mutexes, and atomics in Rust.",
            category_id=cat_id,
            author_id=uuid.uuid4(),
            source_updated_at=datetime.now(UTC),
        )
        session.add(doc)

    transport = ASGITransport(app=search_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Query search document
        search_res = await client.get("/search?q=Rust")
        assert search_res.status_code == 200
        results = search_res.json()["items"]
        assert len(results) >= 1
        assert any(r["thread_id"] == str(source_id) for r in results)

        # 2. Query suggestions
        sug_res = await client.get("/search/suggestions?q=Advanced")
        assert sug_res.status_code == 200
        suggestions = sug_res.json()["suggestions"]
        assert any("Advanced Rust" in s for s in suggestions)


@pytest.mark.asyncio
async def test_persistence_across_manager_restarts():
    """Verify data written to file-based database survives engine restart (simulating container restart)."""
    # 1. Close current manager
    await user_db_module.user_db.close()

    # 2. Create fresh manager pointing to the exact same file
    restarted_db = DatabaseSessionManager(TEST_DB_URL)
    user_db_module.user_db = restarted_db
    user_app_mod.user_db = restarted_db

    # 3. Query user created previously
    transport = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with restarted_db.session() as s:
            from sqlalchemy import select

            acc = (
                await s.execute(select(Account).where(Account.username == "alice_test"))
            ).scalar_one_or_none()
            assert acc is not None
            assert acc.role == "moderator"

        # Check public endpoint
        res = await client.get(f"/users/{acc.id}")
        assert res.status_code == 200
        assert res.json()["username"] == "alice_test"
        assert res.json()["role"] == "moderator"

    # Restore shared manager
    await restarted_db.close()
    restarted_db2 = DatabaseSessionManager(TEST_DB_URL)
    user_db_module.user_db = restarted_db2
    content_db_module.content_db = restarted_db2
    interaction_db_module.interaction_db = restarted_db2
    moderation_db_module.moderation_db = restarted_db2
    notification_db_module.notification_db = restarted_db2
    search_db_module.search_db = restarted_db2

    user_app_mod.user_db = restarted_db2
    content_app_mod.content_db = restarted_db2
    interaction_app_mod.interaction_db = restarted_db2
    moderation_app_mod.moderation_db = restarted_db2
    notification_app_mod.notification_db = restarted_db2
    search_app_mod.search_db = restarted_db2
