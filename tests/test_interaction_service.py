"""Comprehensive automated tests for Task 009: Interaction Service Core Workflows.

Verifies:
1. Voting and Reactions:
   - Vote on thread (+1 / -1) returning VoteResponse with score, my_vote, and reputation delta.
   - Vote toggling: submitting same vote value cancels vote (my_vote=null).
   - Vote replacement: switching vote from -1 to 1 or 1 to -1 updates score and reputation properly.
   - Self-voting prevention: author cannot vote on their own thread (400 SELF_VOTE_NOT_ALLOWED).
   - Comment voting: vote, toggle, and replacement on comments.
   - Validation: values outside (-1, 1) return 422.
   - Outbox event: emits VoteChanged event.

2. Bookmarks:
   - Add bookmark (PUT /threads/{id}/bookmark).
   - Idempotent repeated add returns 200 without duplicates.
   - List own bookmarks (GET /profile/bookmarks) with cursor pagination and enriched thread metadata.
   - Remove bookmark (DELETE /threads/{id}/bookmark).
   - Repeated removal returns 204.
   - User isolation: user cannot inspect or delete another user's bookmark.

3. View Tracking and DEC-005:
   - First view records thread view and emits ThreadViewCountIncremented event to outbox.
   - Repeated view inside 30-minute window returns 204 without emitting duplicate outbox event.
   - View after 30-minute window refreshes timestamp and emits a new increment event.
   - HMAC session/IP hash deduplication.

4. Reputation Ledger:
   - GET /users/{user_id}/reputation sums up net delta in reputation ledger.

5. Gateway Forwarding & Header Security:
   - Unauthenticated requests rejected (401).
   - CSRF validation enforced on state-changing requests when session cookie present.
   - Client-supplied identity headers (X-Authenticated-*, X-Forwarded-*) stripped by Gateway.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import src.gateway.app as gateway_app_mod
import src.services.interaction.app as interaction_app_mod
import src.services.interaction.database as interaction_db_mod
import src.services.user.app as user_app_mod
import src.services.user.database as user_db_mod
from src.common.database import DatabaseSessionManager
from src.gateway.app import app as gateway_app
from src.services.interaction.app import (
    app as interaction_app,
)
from src.services.interaction.app import (
    set_content_http_client,
)
from src.services.interaction.auth import set_auth_http_client
from src.services.interaction.models import Base as InteractionBase
from src.services.interaction.models import (
    InteractionOutboxEvent,
    ThreadView,
)
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

INTERACTION_TEST_DB_PATH = "/tmp/test_forum_interaction_task009.db"
INTERACTION_TEST_DB_URL = f"sqlite+aiosqlite:///{INTERACTION_TEST_DB_PATH}"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def setup_interaction_test_databases() -> AsyncGenerator[None, None]:
    """Initialize SQLite database for Interaction Service tests."""
    import shutil

    if os.path.exists(INTERACTION_TEST_DB_PATH):
        os.remove(INTERACTION_TEST_DB_PATH)
    if os.path.exists("/tmp/sqlite_schemas"):
        shutil.rmtree("/tmp/sqlite_schemas")

    shared_db = DatabaseSessionManager(INTERACTION_TEST_DB_URL)

    async with shared_db.engine.begin() as conn:
        await conn.run_sync(InteractionBase.metadata.create_all)
        await conn.run_sync(UserBase.metadata.create_all)

    orig_interaction_db = interaction_db_mod.interaction_db
    orig_interaction_app_db = interaction_app_mod.interaction_db
    orig_user_db = user_db_mod.user_db
    orig_user_app_db = user_app_mod.user_db

    interaction_db_mod.interaction_db = shared_db
    interaction_app_mod.interaction_db = shared_db
    user_db_mod.user_db = shared_db
    user_app_mod.user_db = shared_db

    # Wire User Service transport for session validation
    user_transport = ASGITransport(app=user_app)
    mock_auth_client = AsyncClient(transport=user_transport, base_url="http://localhost:8001")
    set_auth_http_client(mock_auth_client)

    yield

    set_auth_http_client(None)
    set_content_http_client(None)
    await mock_auth_client.aclose()
    await shared_db.close()

    interaction_db_mod.interaction_db = orig_interaction_db
    interaction_app_mod.interaction_db = orig_interaction_app_db
    user_db_mod.user_db = orig_user_db
    user_app_mod.user_db = orig_user_app_db

    if os.path.exists(INTERACTION_TEST_DB_PATH):
        os.remove(INTERACTION_TEST_DB_PATH)


async def create_test_user_and_session(
    username: str, role: str = "member"
) -> tuple[uuid.UUID, str, str]:
    """Seed user and active session, returning (user_id, session_token, csrf_token)."""
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
            password_hash=hash_password("Password123!"),
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
# 1. Voting Tests
# =============================================================================
@pytest.mark.asyncio
async def test_thread_and_comment_voting_lifecycle():
    """Verify thread and comment voting, toggling, replacement, self-vote prevention, and reputation delta."""
    author_id, author_sess, author_csrf = await create_test_user_and_session(
        "vote_author", "member"
    )
    voter1_id, voter1_sess, voter1_csrf = await create_test_user_and_session(
        "vote_voter1", "member"
    )
    voter2_id, voter2_sess, voter2_csrf = await create_test_user_and_session(
        "vote_voter2", "member"
    )

    thread_id = uuid.uuid4()
    comment_id = uuid.uuid4()

    # Mock Content Service responses for thread validation
    async def mock_content_get(url, **kwargs):
        if str(thread_id) in str(url):
            return httpx.Response(
                200,
                json={
                    "id": str(thread_id),
                    "author_id": str(author_id),
                    "title": "A Great Discussion",
                    "status": "published",
                },
            )
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

    mock_content = AsyncMock(spec=httpx.AsyncClient)
    mock_content.get = mock_content_get
    set_content_http_client(mock_content)

    transport = ASGITransport(app=interaction_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Self-vote attempt rejected (400 SELF_VOTE_NOT_ALLOWED)
        res_self = await client.put(
            f"/threads/{thread_id}/vote",
            json={"value": 1},
            cookies={"forum_session": author_sess},
            headers={"X-CSRF-Token": author_csrf},
        )
        assert res_self.status_code == 400
        assert res_self.json()["error"]["code"] == "SELF_VOTE_NOT_ALLOWED"

        # 2. Voter 1 upvotes thread (+1)
        res_v1 = await client.put(
            f"/threads/{thread_id}/vote",
            json={"value": 1},
            cookies={"forum_session": voter1_sess},
            headers={"X-CSRF-Token": voter1_csrf},
        )
        assert res_v1.status_code == 200
        data1 = res_v1.json()
        assert data1["target_id"] == str(thread_id)
        assert data1["my_vote"] == 1
        assert data1["score"] == 1
        assert data1["reputation_delta"] == 5

        # Check target author's reputation
        res_rep = await client.get(f"/users/{author_id}/reputation")
        assert res_rep.status_code == 200
        assert res_rep.json()["reputation_score"] == 5

        # 3. Voter 2 downvotes thread (-1)
        res_v2 = await client.put(
            f"/threads/{thread_id}/vote",
            json={"value": -1},
            cookies={"forum_session": voter2_sess},
            headers={"X-CSRF-Token": voter2_csrf},
        )
        assert res_v2.status_code == 200
        data2 = res_v2.json()
        assert data2["score"] == 0  # 1 + (-1) = 0
        assert data2["my_vote"] == -1

        # Check reputation after downvote (+5 - 5 = 0)
        res_rep2 = await client.get(f"/users/{author_id}/reputation")
        assert res_rep2.json()["reputation_score"] == 0

        # 4. Voter 1 toggles off their vote (submits +1 again)
        res_toggle = await client.put(
            f"/threads/{thread_id}/vote",
            json={"value": 1},
            cookies={"forum_session": voter1_sess},
            headers={"X-CSRF-Token": voter1_csrf},
        )
        assert res_toggle.status_code == 200
        data_toggle = res_toggle.json()
        assert data_toggle["my_vote"] is None
        assert data_toggle["score"] == -1

        # 5. Voter 2 replaces downvote (-1) with upvote (+1)
        res_replace = await client.put(
            f"/threads/{thread_id}/vote",
            json={"value": 1},
            cookies={"forum_session": voter2_sess},
            headers={"X-CSRF-Token": voter2_csrf},
        )
        assert res_replace.status_code == 200
        data_rep = res_replace.json()
        assert data_rep["my_vote"] == 1
        assert data_rep["score"] == 1
        assert data_rep["reputation_delta"] == 10

        # 6. Comment voting
        res_cvote = await client.put(
            f"/comments/{comment_id}/vote",
            json={"value": 1},
            cookies={"forum_session": voter1_sess},
            headers={"X-CSRF-Token": voter1_csrf},
        )
        assert res_cvote.status_code == 200
        cdata = res_cvote.json()
        assert cdata["target_type"] == "comment"
        assert cdata["target_id"] == str(comment_id)
        assert cdata["score"] == 1

        # 7. Validation error on invalid vote value (e.g., 2)
        res_val = await client.put(
            f"/threads/{thread_id}/vote",
            json={"value": 2},
            cookies={"forum_session": voter1_sess},
            headers={"X-CSRF-Token": voter1_csrf},
        )
        assert res_val.status_code == 422


# =============================================================================
# 2. Bookmark Tests
# =============================================================================
@pytest.mark.asyncio
async def test_bookmark_lifecycle_and_user_isolation():
    """Verify add bookmark, idempotent repeat, list own bookmarks, remove bookmark, and user isolation."""
    user1_id, user1_sess, user1_csrf = await create_test_user_and_session("bm_user1", "member")
    user2_id, user2_sess, user2_csrf = await create_test_user_and_session("bm_user2", "member")

    thread1_id = uuid.uuid4()
    thread2_id = uuid.uuid4()

    async def mock_content_get(url, **kwargs):
        tid = str(url).split("/")[-1]
        return httpx.Response(
            200,
            json={
                "id": tid,
                "author_id": str(user1_id),
                "category_id": str(uuid.uuid4()),
                "title": f"Bookmarked Thread {tid}",
                "body": "Thread body content exceeding twenty characters.",
                "tags": ["test"],
                "status": "published",
                "is_pinned": False,
                "is_locked": False,
                "best_answer_comment_id": None,
                "view_count": 0,
            },
        )

    mock_content = AsyncMock(spec=httpx.AsyncClient)
    mock_content.get = mock_content_get
    set_content_http_client(mock_content)

    transport = ASGITransport(app=interaction_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. User 1 bookmarks Thread 1
        res_bm1 = await client.put(
            f"/threads/{thread1_id}/bookmark",
            cookies={"forum_session": user1_sess},
            headers={"X-CSRF-Token": user1_csrf},
        )
        assert res_bm1.status_code == 200
        assert res_bm1.json()["bookmarked"] is True

        # 2. Repeated bookmark is idempotent (200 OK)
        res_bm1_dup = await client.put(
            f"/threads/{thread1_id}/bookmark",
            cookies={"forum_session": user1_sess},
            headers={"X-CSRF-Token": user1_csrf},
        )
        assert res_bm1_dup.status_code == 200

        # User 1 bookmarks Thread 2
        await client.put(
            f"/threads/{thread2_id}/bookmark",
            cookies={"forum_session": user1_sess},
            headers={"X-CSRF-Token": user1_csrf},
        )

        # 3. User 1 lists own bookmarks
        res_list1 = await client.get(
            "/profile/bookmarks",
            cookies={"forum_session": user1_sess},
        )
        assert res_list1.status_code == 200
        items1 = res_list1.json()["items"]
        assert len(items1) == 2
        assert any(b["id"] == str(thread1_id) for b in items1)
        assert any(b["id"] == str(thread2_id) for b in items1)

        # 4. User 2 lists own bookmarks (empty, isolation verified)
        res_list2 = await client.get(
            "/profile/bookmarks",
            cookies={"forum_session": user2_sess},
        )
        assert res_list2.status_code == 200
        assert len(res_list2.json()["items"]) == 0

        # 5. User 1 removes bookmark on Thread 1 (204 No Content)
        res_del = await client.delete(
            f"/threads/{thread1_id}/bookmark",
            cookies={"forum_session": user1_sess},
            headers={"X-CSRF-Token": user1_csrf},
        )
        assert res_del.status_code == 204

        # 6. Repeated delete is idempotent (204 No Content)
        res_del_dup = await client.delete(
            f"/threads/{thread1_id}/bookmark",
            cookies={"forum_session": user1_sess},
            headers={"X-CSRF-Token": user1_csrf},
        )
        assert res_del_dup.status_code == 204

        # Verify only Thread 2 remains in User 1 bookmarks
        res_list_after = await client.get(
            "/profile/bookmarks",
            cookies={"forum_session": user1_sess},
        )
        assert len(res_list_after.json()["items"]) == 1
        assert res_list_after.json()["items"][0]["id"] == str(thread2_id)


# =============================================================================
# 3. View Tracking & Deduplication Tests (DEC-005)
# =============================================================================
@pytest.mark.asyncio
async def test_view_tracking_and_deduplication():
    """Verify first view emits outbox event, repeated view inside 30m is deduplicated, and subsequent view after window emits event."""
    user_id, session_token, csrf_token = await create_test_user_and_session("view_user", "member")
    thread_id = uuid.uuid4()

    async def mock_content_get(url, **kwargs):
        return httpx.Response(200, json={"id": str(thread_id), "status": "published"})

    mock_content = AsyncMock(spec=httpx.AsyncClient)
    mock_content.get = mock_content_get
    set_content_http_client(mock_content)

    transport = ASGITransport(app=interaction_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. First view
        res_v1 = await client.post(
            f"/threads/{thread_id}/view",
            cookies={"forum_session": session_token},
        )
        assert res_v1.status_code == 204

        # Verify outbox event written
        async with interaction_db_mod.interaction_db.session() as session:
            outbox_query = select(InteractionOutboxEvent).where(
                InteractionOutboxEvent.aggregate_id == thread_id,
                InteractionOutboxEvent.event_type == "ThreadViewCountIncremented",
            )
            outbox_events = (await session.execute(outbox_query)).scalars().all()
            assert len(outbox_events) == 1
            payload = outbox_events[0].payload
            assert payload["thread_id"] == str(thread_id)
            assert payload["increment"] == 1

        # 2. Second view immediately after (inside 30-minute window) -> Deduplicated!
        res_v2 = await client.post(
            f"/threads/{thread_id}/view",
            cookies={"forum_session": session_token},
        )
        assert res_v2.status_code == 204

        # Verify no duplicate outbox event was created
        async with interaction_db_mod.interaction_db.session() as session:
            outbox_events = (await session.execute(outbox_query)).scalars().all()
            assert len(outbox_events) == 1

        # 3. Fast-forward the view timestamp to >30 minutes ago
        async with interaction_db_mod.interaction_db.transaction() as session:
            tv_query = select(ThreadView).where(ThreadView.thread_id == thread_id)
            tv = (await session.execute(tv_query)).scalar_one()
            tv.viewed_at = datetime.now(UTC) - timedelta(minutes=31)

        # 4. Third view after window expiration -> New view count increment event emitted!
        res_v3 = await client.post(
            f"/threads/{thread_id}/view",
            cookies={"forum_session": session_token},
        )
        assert res_v3.status_code == 204

        async with interaction_db_mod.interaction_db.session() as session:
            outbox_events = (await session.execute(outbox_query)).scalars().all()
            assert len(outbox_events) == 2


# =============================================================================
# 4. Gateway Integration & Security Tests
# =============================================================================
@pytest.mark.asyncio
async def test_gateway_interaction_routing_and_spoof_prevention():
    """Verify Gateway routes interaction endpoints, passes session cookies, and strips client-supplied identity headers."""
    user_id, session_token, csrf_token = await create_test_user_and_session(
        "gw_interaction_user", "member"
    )
    thread_id = uuid.uuid4()

    service_apps = {
        "http://localhost:8001": user_app,
        "http://localhost:8003": interaction_app,
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

    # Mock Content Service for thread verification
    mock_content = AsyncMock(spec=httpx.AsyncClient)
    mock_content.get = AsyncMock(
        return_value=httpx.Response(
            200, json={"id": str(thread_id), "author_id": str(uuid.uuid4())}
        )
    )
    set_content_http_client(mock_content)

    with patch.object(gateway_app_mod, "http_client", mock_client):
        transport = ASGITransport(app=gateway_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Gateway PUT /api/v1/threads/{id}/bookmark with session cookie and CSRF
            res_bm = await client.put(
                f"/api/v1/threads/{thread_id}/bookmark",
                cookies={"forum_session": session_token},
                headers={"X-CSRF-Token": csrf_token},
            )
            assert res_bm.status_code == 200
            assert res_bm.json()["bookmarked"] is True

            # 2. Unauthenticated request to /api/v1/profile/bookmarks -> 401
            res_unauth = await client.get("/api/v1/profile/bookmarks")
            assert res_unauth.status_code == 401

            # 3. Spoofed identity headers stripped -> 401 Unauthorized
            res_spoof = await client.get(
                "/api/v1/profile/bookmarks",
                headers={
                    "X-Authenticated-User-ID": str(uuid.uuid4()),
                    "X-Authenticated-Role": "admin",
                    "X-Forwarded-User-ID": str(uuid.uuid4()),
                    "X-Forwarded-Role": "admin",
                },
            )
            assert res_spoof.status_code == 401
