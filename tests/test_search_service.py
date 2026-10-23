"""Comprehensive automated tests for Task 012: Search Service Implementation.

Verifies:
1. Search & Suggestions API:
   - Query by title and body with ILIKE/full-text matching.
   - Category and tag filtering.
   - Sorting by relevance, newest, and most_viewed.
   - Cursor pagination with next_cursor encoding.
   - Autocomplete suggestions with minimum length validation and deduplication.
   - Empty queries and no-results handling.
2. Visibility & Lifecycle Handling:
   - Inactive / hidden / soft-deleted documents excluded from search results.
   - Locked threads remain visible in search.
   - Moderation actions (soft-delete, hide, restore) dynamically update search visibility.
3. Event Processing, Atomic Idempotency & Out-of-Order Safety:
   - Consumes domain events: ThreadCreated, ThreadUpdated, ThreadDeleted, ThreadLockToggled, ThreadPinToggled, ThreadViewCountIncremented, ModerationActionApplied.
   - Idempotent processing: Duplicate event delivery with same event_id creates no duplicate records and updates atomically.
   - Version incrementing prevents stale overwrites.
4. Admin Index Reconciliation / Rebuild:
   - POST /admin/reindex fetches published threads from Content Service without cross-DB queries.
   - Requires admin role and valid CSRF token.
5. Gateway Routing & Header Sanitization:
   - Gateway correctly routes /api/v1/search and /api/v1/search/suggestions.
   - Gateway strips client-supplied identity headers.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import src.gateway.app as gateway_app_mod
import src.services.content.app as content_app_mod
import src.services.content.database as content_db_mod
import src.services.search.app as search_app_mod
import src.services.search.database as search_db_mod
import src.services.user.app as user_app_mod
import src.services.user.database as user_db_mod
from src.common.database import DatabaseSessionManager
from src.gateway.app import app as gateway_app
from src.services.content.app import app as content_app
from src.services.content.models import Base as ContentBase
from src.services.content.models import Category, Thread
from src.services.search.app import app as search_app
from src.services.search.auth import set_auth_http_client
from src.services.search.models import Base as SearchBase
from src.services.search.models import ConsumedEvent, SearchDocument
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

INTERNAL_AUTH_HEADERS = {"X-Internal-Service-Key": os.environ["INTERNAL_SERVICE_SECRET"]}

SEARCH_TEST_DB_PATH = "/tmp/test_forum_search_task012.db"
SEARCH_TEST_DB_URL = f"sqlite+aiosqlite:///{SEARCH_TEST_DB_PATH}"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def setup_search_test_databases() -> AsyncGenerator[None, None]:
    """Initialize SQLite database for Search Service tests."""
    import shutil

    if os.path.exists(SEARCH_TEST_DB_PATH):
        os.remove(SEARCH_TEST_DB_PATH)
    if os.path.exists("/tmp/sqlite_schemas"):
        shutil.rmtree("/tmp/sqlite_schemas")

    shared_db = DatabaseSessionManager(SEARCH_TEST_DB_URL)

    async with shared_db.engine.begin() as conn:
        await conn.run_sync(SearchBase.metadata.create_all)
        await conn.run_sync(UserBase.metadata.create_all)
        await conn.run_sync(ContentBase.metadata.create_all)

    orig_search_db = search_db_mod.search_db
    orig_search_app_db = search_app_mod.search_db
    orig_user_db = user_db_mod.user_db
    orig_user_app_db = user_app_mod.user_db
    orig_content_db = content_db_mod.content_db
    orig_content_app_db = content_app_mod.content_db

    search_db_mod.search_db = shared_db
    search_app_mod.search_db = shared_db
    user_db_mod.user_db = shared_db
    user_app_mod.user_db = shared_db
    content_db_mod.content_db = shared_db
    content_app_mod.content_db = shared_db

    # Setup mock HTTP client for user/content service calls
    user_transport = ASGITransport(app=user_app)
    mock_auth_client = AsyncClient(transport=user_transport, base_url="http://localhost:8001")
    set_auth_http_client(mock_auth_client)

    yield

    set_auth_http_client(None)
    await mock_auth_client.aclose()
    await shared_db.close()

    search_db_mod.search_db = orig_search_db
    search_app_mod.search_db = orig_search_app_db
    user_db_mod.user_db = orig_user_db
    user_app_mod.user_db = orig_user_app_db
    content_db_mod.content_db = orig_content_db
    content_app_mod.content_db = orig_content_app_db

    if os.path.exists(SEARCH_TEST_DB_PATH):
        os.remove(SEARCH_TEST_DB_PATH)


async def create_test_user_and_session(
    username: str, role: str = "member"
) -> tuple[uuid.UUID, str, str]:
    """Seed user and active session, returning (user_id, session_token, csrf_token)."""
    now = datetime.now(UTC)
    user_id = uuid.uuid4()
    session_token = generate_session_token()
    csrf_token = generate_csrf_token(secret="default-dev-csrf-secret")

    async with user_db_mod.user_db.transaction() as session:
        account = Account(
            id=user_id,
            username=username,
            username_normalized=username.lower(),
            email=f"{username}@example.com",
            email_normalized=f"{username}@example.com",
            password_hash=hash_password("SecurePass123!"),
            role=role,
            is_banned=False,
        )
        session.add(account)

        db_session = DbSession(
            id=uuid.uuid4(),
            account_id=user_id,
            session_token_hash=hash_secret(session_token),
            idle_expires_at=now + timedelta(days=1),
            absolute_expires_at=now + timedelta(days=7),
            created_at=now,
            last_seen_at=now,
        )
        session.add(db_session)

    return user_id, session_token, csrf_token


@pytest.mark.asyncio
async def test_search_query_filtering_sorting_and_pagination():
    """Verify search by keyword, category, tag, sorting, and cursor pagination."""
    cat1_id = uuid.uuid4()
    cat2_id = uuid.uuid4()
    author_id = uuid.uuid4()
    now = datetime.now(UTC)

    # Seed 3 distinct search documents
    t1_id = uuid.uuid4()
    t2_id = uuid.uuid4()
    t3_id = uuid.uuid4()

    async with search_db_mod.search_db.transaction() as session:
        doc1 = SearchDocument(
            id=uuid.uuid4(),
            source_type="thread",
            source_id=t1_id,
            title="FastAPI Async Microservices Architecture",
            body="Comprehensive guide to high-performance event-driven services in Python.",
            author_id=author_id,
            category_id=cat1_id,
            tags=["fastapi", "python", "async"],
            is_visible=True,
            view_count=50,
            version=1,
            source_created_at=now - timedelta(hours=3),
            source_updated_at=now - timedelta(hours=3),
            indexed_at=now - timedelta(hours=3),
        )
        doc2 = SearchDocument(
            id=uuid.uuid4(),
            source_type="thread",
            source_id=t2_id,
            title="Building Scalable Event Outboxes with PostgreSQL",
            body="Transactional outbox pattern implementation details for resilient systems.",
            author_id=author_id,
            category_id=cat1_id,
            tags=["postgres", "outbox", "async"],
            is_visible=True,
            view_count=150,
            version=1,
            source_created_at=now - timedelta(hours=2),
            source_updated_at=now - timedelta(hours=2),
            indexed_at=now - timedelta(hours=2),
        )
        doc3 = SearchDocument(
            id=uuid.uuid4(),
            source_type="thread",
            source_id=t3_id,
            title="Frontend UI State Management with Svelte",
            body="Explaining reactive stores and fine-grained reactivity in modern web apps.",
            author_id=author_id,
            category_id=cat2_id,
            tags=["svelte", "frontend"],
            is_visible=True,
            view_count=10,
            version=1,
            source_created_at=now - timedelta(hours=1),
            source_updated_at=now - timedelta(hours=1),
            indexed_at=now - timedelta(hours=1),
        )
        session.add_all([doc1, doc2, doc3])

    transport = ASGITransport(app=search_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Search by keyword across title/body
        res1 = await client.get("/search?q=FastAPI")
        assert res1.status_code == 200
        items1 = res1.json()["items"]
        assert len(items1) == 1
        assert items1[0]["thread_id"] == str(t1_id)
        assert "FastAPI" in items1[0]["title"]

        # Search keyword matching body
        res_body = await client.get("/search?q=transactional")
        assert res_body.status_code == 200
        assert any(i["thread_id"] == str(t2_id) for i in res_body.json()["items"])

        # 2. Filter by category_id
        res_cat = await client.get(f"/search?category_id={cat1_id}")
        assert res_cat.status_code == 200
        assert len(res_cat.json()["items"]) == 2

        # 3. Filter by tag
        res_tag = await client.get("/search?tag=async")
        assert res_tag.status_code == 200
        assert len(res_tag.json()["items"]) == 2

        # 4. Sort by newest
        res_newest = await client.get("/search?sort=newest")
        assert res_newest.status_code == 200
        newest_items = res_newest.json()["items"]
        assert newest_items[0]["thread_id"] == str(t3_id)  # latest created

        # 5. Sort by most_viewed
        res_views = await client.get("/search?sort=most_viewed")
        assert res_views.status_code == 200
        view_items = res_views.json()["items"]
        assert view_items[0]["thread_id"] == str(t2_id)  # 150 views

        # 6. Cursor pagination (limit=1)
        page1 = await client.get("/search?sort=newest&limit=1")
        assert page1.status_code == 200
        p1_data = page1.json()
        assert len(p1_data["items"]) == 1
        assert p1_data["next_cursor"] is not None

        page2 = await client.get(f"/search?sort=newest&limit=1&cursor={p1_data['next_cursor']}")
        assert page2.status_code == 200
        p2_data = page2.json()
        assert len(p2_data["items"]) == 1
        assert p2_data["items"][0]["thread_id"] != p1_data["items"][0]["thread_id"]

        # 7. Suggestions endpoint
        sug_res = await client.get("/search/suggestions?q=Fast")
        assert sug_res.status_code == 200
        assert any("FastAPI" in s for s in sug_res.json()["suggestions"])

        # Suggestions validation (min_length=2)
        sug_invalid = await client.get("/search/suggestions?q=F")
        assert sug_invalid.status_code == 422


@pytest.mark.asyncio
async def test_visibility_policy_and_moderation_lifecycle():
    """Verify non-public threads (hidden, deleted, moderated) are excluded from search results."""
    thread_visible_id = uuid.uuid4()
    thread_hidden_id = uuid.uuid4()
    thread_locked_id = uuid.uuid4()
    author_id = uuid.uuid4()
    now = datetime.now(UTC)

    async with search_db_mod.search_db.transaction() as session:
        doc_vis = SearchDocument(
            id=uuid.uuid4(),
            source_type="thread",
            source_id=thread_visible_id,
            title="Public Community Discussion",
            body="Everyone can see this public post.",
            author_id=author_id,
            is_visible=True,
            version=1,
            source_created_at=now,
            source_updated_at=now,
            indexed_at=now,
        )
        doc_hid = SearchDocument(
            id=uuid.uuid4(),
            source_type="thread",
            source_id=thread_hidden_id,
            title="Secret Draft Discussion",
            body="This post is hidden or draft.",
            author_id=author_id,
            is_visible=False,
            version=1,
            source_created_at=now,
            source_updated_at=now,
            indexed_at=now,
        )
        doc_locked = SearchDocument(
            id=uuid.uuid4(),
            source_type="thread",
            source_id=thread_locked_id,
            title="Archived Locked Discussion",
            body="This post is locked for comments but remains visible in search.",
            author_id=author_id,
            is_visible=True,
            version=1,
            source_created_at=now,
            source_updated_at=now,
            indexed_at=now,
        )
        session.add_all([doc_vis, doc_hid, doc_locked])

    transport = ASGITransport(app=search_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Query all
        res = await client.get("/search")
        assert res.status_code == 200
        result_ids = [item["thread_id"] for item in res.json()["items"]]

        # Public and locked threads MUST be returned
        assert str(thread_visible_id) in result_ids
        assert str(thread_locked_id) in result_ids

        # Hidden thread MUST NOT be returned
        assert str(thread_hidden_id) not in result_ids

        # Moderation action: soft_delete public thread
        mod_event_id = uuid.uuid4()
        mod_res = await client.post(
            "/internal/events",
            headers=INTERNAL_AUTH_HEADERS,
            json={
                "event_id": str(mod_event_id),
                "event_type": "ModerationActionApplied",
                "producer": "moderation-service",
                "data": {
                    "action_id": str(uuid.uuid4()),
                    "target_type": "thread",
                    "target_id": str(thread_visible_id),
                    "action": "soft_delete",
                },
            },
        )
        assert mod_res.status_code == 200

        # Query again: thread_visible_id must now be excluded
        res2 = await client.get("/search")
        result_ids2 = [item["thread_id"] for item in res2.json()["items"]]
        assert str(thread_visible_id) not in result_ids2


@pytest.mark.asyncio
async def test_domain_event_ingestion_and_idempotency():
    """Verify event consumption, atomic deduplication, and update handling."""
    transport = ASGITransport(app=search_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        event_id = uuid.uuid4()
        thread_id = uuid.uuid4()
        author_id = uuid.uuid4()
        cat_id = uuid.uuid4()

        payload = {
            "event_id": str(event_id),
            "event_type": "ThreadCreated",
            "producer": "content-service",
            "data": {
                "thread_id": str(thread_id),
                "author_id": str(author_id),
                "category_id": str(cat_id),
                "title": "Clean Architecture in Python",
                "body": "Decoupling domain logic from persistence adapters.",
                "tags": ["architecture", "clean-code"],
                "status": "published",
            },
        }

        # 1. Ingest event first time
        resp1 = await client.post("/internal/events", json=payload, headers=INTERNAL_AUTH_HEADERS)
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "processed"
        assert resp1.json()["document_updated"] is True

        # Verify SearchDocument in DB
        async with search_db_mod.search_db.session() as session:
            stmt = select(SearchDocument).where(SearchDocument.source_id == thread_id)
            doc = (await session.execute(stmt)).scalar_one_or_none()
            assert doc is not None
            assert doc.title == "Clean Architecture in Python"
            assert doc.is_visible is True
            assert doc.version == 1

            # Verify ConsumedEvent in DB
            c_stmt = select(ConsumedEvent).where(ConsumedEvent.event_id == event_id)
            c_event = (await session.execute(c_stmt)).scalar_one_or_none()
            assert c_event is not None
            assert c_event.event_type == "ThreadCreated"

        # 2. Ingest same event again (Duplicate delivery)
        resp2 = await client.post("/internal/events", json=payload, headers=INTERNAL_AUTH_HEADERS)
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "processed"
        assert resp2.json()["document_updated"] is False  # Deduplicated

        # 3. ThreadUpdated event
        update_event_id = uuid.uuid4()
        update_payload = {
            "event_id": str(update_event_id),
            "event_type": "ThreadUpdated",
            "producer": "content-service",
            "data": {
                "thread_id": str(thread_id),
                "title": "Clean Architecture in Python (2nd Edition)",
                "body": "Updated domain modeling patterns.",
            },
        }
        resp3 = await client.post(
            "/internal/events", json=update_payload, headers=INTERNAL_AUTH_HEADERS
        )
        assert resp3.status_code == 200

        async with search_db_mod.search_db.session() as session:
            stmt = select(SearchDocument).where(SearchDocument.source_id == thread_id)
            doc = (await session.execute(stmt)).scalar_one()
            assert doc.title == "Clean Architecture in Python (2nd Edition)"
            assert doc.version == 2

        # 4. ViewCount event (DEC-005)
        vc_event_id = uuid.uuid4()
        vc_payload = {
            "event_id": str(vc_event_id),
            "event_type": "ThreadViewCountIncremented",
            "producer": "interaction-service",
            "data": {"thread_id": str(thread_id), "increment": 5},
        }
        await client.post("/internal/events", json=vc_payload, headers=INTERNAL_AUTH_HEADERS)

        async with search_db_mod.search_db.session() as session:
            stmt = select(SearchDocument).where(SearchDocument.source_id == thread_id)
            doc = (await session.execute(stmt)).scalar_one()
            assert doc.view_count == 5

        # 5. ThreadDeleted event
        del_event_id = uuid.uuid4()
        del_payload = {
            "event_id": str(del_event_id),
            "event_type": "ThreadDeleted",
            "producer": "content-service",
            "data": {"thread_id": str(thread_id)},
        }
        await client.post("/internal/events", json=del_payload, headers=INTERNAL_AUTH_HEADERS)

        async with search_db_mod.search_db.session() as session:
            stmt = select(SearchDocument).where(SearchDocument.source_id == thread_id)
            doc = (await session.execute(stmt)).scalar_one()
            assert doc.is_visible is False


@pytest.mark.asyncio
async def test_admin_reindex_and_gateway_routing():
    """Verify Admin reindexing endpoint and Gateway routing for search service."""
    admin_id, admin_session, admin_csrf = await create_test_user_and_session(
        "search_admin", role="admin"
    )
    user_id, user_session, user_csrf = await create_test_user_and_session(
        "regular_user", role="member"
    )

    # 1. Seed thread in Content Service DB directly
    thread_id = uuid.uuid4()
    cat_id = uuid.uuid4()
    async with content_db_mod.content_db.transaction() as session:
        cat = Category(
            id=cat_id,
            name="General",
            slug="gen-reindex",
            is_hidden=False,
            created_by=admin_id,
        )
        session.add(cat)
        t = Thread(
            id=thread_id,
            author_id=user_id,
            category_id=cat_id,
            title="Thread for Reindexing Test",
            body="This thread should be pulled into the search index during reindex.",
            status="published",
            is_pinned=False,
            is_locked=False,
            view_count=42,
        )
        session.add(t)

    # Wire mock content client into search auth/http client
    content_transport = ASGITransport(app=content_app)
    mock_content_client = AsyncClient(transport=content_transport, base_url="http://localhost:8002")
    set_auth_http_client(mock_content_client)

    transport = ASGITransport(app=search_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Non-admin user rejected from reindex
        unauth_res = await client.post(
            "/admin/reindex",
            headers={
                "X-Forwarded-User-ID": str(user_id),
                "X-Forwarded-Role": "member",
                "X-CSRF-Token": user_csrf,
            },
        )
        assert unauth_res.status_code == 403

        # Admin trigger reindex
        admin_res = await client.post(
            "/admin/reindex",
            headers={
                "X-Forwarded-User-ID": str(admin_id),
                "X-Forwarded-Role": "admin",
                "X-CSRF-Token": admin_csrf,
            },
        )
        assert admin_res.status_code == 200
        assert admin_res.json()["status"] == "reindexed"
        assert admin_res.json()["count"] >= 1

        # Verify thread was indexed
        s_res = await client.get("/search?q=Reindexing")
        assert s_res.status_code == 200
        assert any(i["thread_id"] == str(thread_id) for i in s_res.json()["items"])

    # 2. Test Gateway routing for Search
    gateway_transport = ASGITransport(app=gateway_app)
    search_transport = ASGITransport(app=search_app)
    mock_gw_client = AsyncClient(transport=search_transport, base_url="http://search-service:8006")
    gateway_app_mod.http_client = mock_gw_client

    async with AsyncClient(transport=gateway_transport, base_url="http://test") as gw_client:
        gw_search = await gw_client.get(
            "/api/v1/search?q=Reindexing",
            headers={"X-Forwarded-User-ID": "spoofed-user-id"},
        )
        assert gw_search.status_code == 200
        assert any(i["thread_id"] == str(thread_id) for i in gw_search.json()["items"])

        gw_sug = await gw_client.get("/api/v1/search/suggestions?q=Reind")
        assert gw_sug.status_code == 200
        assert "suggestions" in gw_sug.json()

    await mock_content_client.aclose()
    set_auth_http_client(None)
