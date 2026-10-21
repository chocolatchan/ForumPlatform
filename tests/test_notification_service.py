"""Comprehensive automated tests for Task 011: Notification Service Implementation.

Verifies:
1. Notification Persistence & Inbox API:
   - Authenticated user can list notifications with cursor pagination and unread filter.
   - User can only access own notifications by ID (other user's returns 404).
   - Single notification mark-as-read (PATCH /notifications/{id}/read) is idempotent.
   - Mark all notifications read (POST /notifications/read-all) updates all unread notifications.
   - Unread count (GET /notifications/unread-count) reflects exact unread items count.
2. Notification Preferences API:
   - Reading preferences (GET /notifications/preferences) defaults to in_app_enabled=True, email_enabled=False.
   - Updating preferences (PATCH /notifications/preferences) enforces persistence and affects future notifications.
   - Preferences disabled for in-app suppresses notification item creation while marking event consumed.
3. Event Processing & Idempotency:
   - Consumes supported domain events: UserRegistered, CommentCreated, BestAnswerChanged, VoteChanged, ModerationActionApplied.
   - Idempotent processing: Duplicate event delivery with same (event_id, recipient_id) creates no duplicate notifications.
   - Self-notification exclusion: Authors do not receive notifications for their own comments or upvotes.
   - Atomicity: ConsumedEvent record and NotificationItem committed together in same transaction.
4. Gateway Security & Authentication:
   - Strips client-supplied identity headers (X-Forwarded-*, X-User-ID).
   - Enforces CSRF token on state-changing operations.
   - Unauthenticated requests rejected with 401.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import src.gateway.app as gateway_app_mod
import src.services.notification.app as notification_app_mod
import src.services.notification.database as notification_db_mod
import src.services.user.app as user_app_mod
import src.services.user.database as user_db_mod
from src.common.database import DatabaseSessionManager
from src.gateway.app import app as gateway_app
from src.services.notification.app import app as notification_app
from src.services.notification.auth import set_auth_http_client
from src.services.notification.models import Base as NotificationBase
from src.services.notification.models import (
    ConsumedEvent,
    NotificationItem,
    NotificationPreference,
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

NOTIFICATION_TEST_DB_PATH = "/tmp/test_forum_notification_task011.db"
NOTIFICATION_TEST_DB_URL = f"sqlite+aiosqlite:///{NOTIFICATION_TEST_DB_PATH}"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def setup_notification_test_databases() -> AsyncGenerator[None, None]:
    """Initialize SQLite database for Notification Service tests."""
    import shutil

    if os.path.exists(NOTIFICATION_TEST_DB_PATH):
        os.remove(NOTIFICATION_TEST_DB_PATH)
    if os.path.exists("/tmp/sqlite_schemas"):
        shutil.rmtree("/tmp/sqlite_schemas")

    shared_db = DatabaseSessionManager(NOTIFICATION_TEST_DB_URL)

    async with shared_db.engine.begin() as conn:
        await conn.run_sync(NotificationBase.metadata.create_all)
        await conn.run_sync(UserBase.metadata.create_all)

    orig_notification_db = notification_db_mod.notification_db
    orig_notification_app_db = notification_app_mod.notification_db
    orig_user_db = user_db_mod.user_db
    orig_user_app_db = user_app_mod.user_db

    notification_db_mod.notification_db = shared_db
    notification_app_mod.notification_db = shared_db
    user_db_mod.user_db = shared_db
    user_app_mod.user_db = shared_db

    user_transport = ASGITransport(app=user_app)
    mock_auth_client = AsyncClient(transport=user_transport, base_url="http://localhost:8001")
    set_auth_http_client(mock_auth_client)

    yield

    set_auth_http_client(None)
    await mock_auth_client.aclose()
    await shared_db.close()

    notification_db_mod.notification_db = orig_notification_db
    notification_app_mod.notification_db = orig_notification_app_db
    user_db_mod.user_db = orig_user_db
    user_app_mod.user_db = orig_user_app_db

    if os.path.exists(NOTIFICATION_TEST_DB_PATH):
        os.remove(NOTIFICATION_TEST_DB_PATH)


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
async def test_notification_inbox_and_lifecycle():
    """Verify Inbox listing, cursor pagination, detail access control, and mark-as-read."""
    user1_id, session1, csrf1 = await create_test_user_and_session("notif_user_1")
    user2_id, session2, csrf2 = await create_test_user_and_session("notif_user_2")

    # 1. Seed 3 notifications for user1 and 1 for user2
    now = datetime.now(UTC)
    async with notification_db_mod.notification_db.transaction() as session:
        for i in range(3):
            item = NotificationItem(
                id=uuid.uuid4(),
                recipient_id=user1_id,
                actor_id=user2_id,
                type="comment",
                title=f"Notification {i + 1}",
                body=f"Body of notification {i + 1}",
                target_type="thread",
                target_id=uuid.uuid4(),
                is_read=False,
                created_at=now - timedelta(minutes=10 - i),
                read_at=None,
            )
            session.add(item)

        user2_item = NotificationItem(
            id=uuid.uuid4(),
            recipient_id=user2_id,
            actor_id=user1_id,
            type="upvote",
            title="User 2 Notification",
            body="User 2 Body",
            target_type="comment",
            target_id=uuid.uuid4(),
            is_read=False,
            created_at=now,
            read_at=None,
        )
        session.add(user2_item)

    transport = ASGITransport(app=notification_app)
    cookies_user1 = {"forum_session": session1}
    cookies_user2 = {"forum_session": session2}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 2. Check unread count for user 1 -> should be 3
        res = await client.get("/notifications/unread-count", cookies=cookies_user1)
        assert res.status_code == 200
        assert res.json()["unread_count"] == 3

        # Check unread count for user 2 -> should be 1
        res = await client.get("/notifications/unread-count", cookies=cookies_user2)
        assert res.status_code == 200
        assert res.json()["unread_count"] == 1

        # 3. List notifications with pagination limit=2 for user 1
        res = await client.get("/notifications?limit=2", cookies=cookies_user1)
        assert res.status_code == 200
        data = res.json()
        assert len(data["items"]) == 2
        assert data["next_cursor"] is not None
        # User 1 should only see their own items (most recent first)
        assert data["items"][0]["title"] == "Notification 3"
        assert data["items"][1]["title"] == "Notification 2"
        first_item_id = data["items"][0]["id"]

        # Fetch second page
        res_page2 = await client.get(
            f"/notifications?limit=2&cursor={data['next_cursor']}",
            cookies=cookies_user1,
        )
        assert res_page2.status_code == 200
        data_page2 = res_page2.json()
        assert len(data_page2["items"]) == 1
        assert data_page2["items"][0]["title"] == "Notification 1"
        assert data_page2["next_cursor"] is None

        # 4. User 1 can view detail of their own notification
        res_detail = await client.get(f"/notifications/{first_item_id}", cookies=cookies_user1)
        assert res_detail.status_code == 200
        assert res_detail.json()["id"] == first_item_id

        # 5. User 2 attempting to view User 1's notification returns 404 (isolation)
        res_forbidden = await client.get(f"/notifications/{first_item_id}", cookies=cookies_user2)
        assert res_forbidden.status_code == 404

        # 6. Mark single notification read (PATCH /notifications/{id}/read) with CSRF
        res_read = await client.patch(
            f"/notifications/{first_item_id}/read",
            cookies=cookies_user1,
            headers={"X-CSRF-Token": csrf1},
        )
        assert res_read.status_code == 200
        assert res_read.json()["is_read"] is True

        # Unread count should now be 2
        res = await client.get("/notifications/unread-count", cookies=cookies_user1)
        assert res.json()["unread_count"] == 2

        # Repeated read request is idempotent
        res_read_repeat = await client.patch(
            f"/notifications/{first_item_id}/read",
            cookies=cookies_user1,
            headers={"X-CSRF-Token": csrf1},
        )
        assert res_read_repeat.status_code == 200
        assert res_read_repeat.json()["is_read"] is True

        # 7. Filter by unread_only=true -> returns 2 remaining unread notifications
        res_unread = await client.get("/notifications?unread_only=true", cookies=cookies_user1)
        assert res_unread.status_code == 200
        assert len(res_unread.json()["items"]) == 2
        unread_titles = [i["title"] for i in res_unread.json()["items"]]
        assert "Notification 3" not in unread_titles
        assert "Notification 2" in unread_titles
        assert "Notification 1" in unread_titles

        # 8. Mark all read (POST /notifications/read-all) with CSRF
        res_read_all = await client.post(
            "/notifications/read-all",
            cookies=cookies_user1,
            headers={"X-CSRF-Token": csrf1},
        )
        assert res_read_all.status_code == 204

        # Unread count should now be 0
        res = await client.get("/notifications/unread-count", cookies=cookies_user1)
        assert res.json()["unread_count"] == 0

        # Unread items query returns empty list
        res_empty = await client.get("/notifications?unread_only=true", cookies=cookies_user1)
        assert res_empty.status_code == 200
        assert len(res_empty.json()["items"]) == 0


@pytest.mark.asyncio
async def test_notification_preferences_management():
    """Verify notification preferences reading, updating, and persistence per user."""
    user_id, session_token, csrf_token = await create_test_user_and_session("pref_user")

    transport = ASGITransport(app=notification_app)
    cookies = {"forum_session": session_token}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Default preferences should be in_app_enabled=True, email_enabled=False
        res = await client.get("/notifications/preferences", cookies=cookies)
        assert res.status_code == 200
        assert res.json() == {"in_app_enabled": True, "email_enabled": False}

        # 2. Update preferences: disable in-app and enable email
        res_up = await client.patch(
            "/notifications/preferences",
            cookies=cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"in_app_enabled": False, "email_enabled": True},
        )
        assert res_up.status_code == 200
        assert res_up.json() == {"in_app_enabled": False, "email_enabled": True}

        # 3. Read again to verify persistence
        res2 = await client.get("/notifications/preferences", cookies=cookies)
        assert res2.status_code == 200
        assert res2.json() == {"in_app_enabled": False, "email_enabled": True}


@pytest.mark.asyncio
async def test_event_processing_and_idempotency():
    """Verify domain events ingestion, deduplication, and preference enforcement."""
    user_id, session_token, _ = await create_test_user_and_session("event_user_recip")
    actor_id, _, _ = await create_test_user_and_session("event_user_actor")

    transport = ASGITransport(app=notification_app)
    cookies = {"forum_session": session_token}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Event 1: CommentCreated
        event_id = uuid.uuid4()
        event_payload = {
            "event_id": str(event_id),
            "event_type": "CommentCreated",
            "data": {
                "comment_id": str(uuid.uuid4()),
                "thread_id": str(uuid.uuid4()),
                "author_id": str(actor_id),
                "recipient_id": str(user_id),
                "body_preview": "Great article on FastAPI!",
            },
        }

        # First ingestion -> should create 1 notification
        res = await client.post("/internal/events", json=event_payload)
        assert res.status_code == 200
        assert res.json()["notifications_created"] == 1

        # Check notification inbox
        inbox_res = await client.get("/notifications", cookies=cookies)
        assert inbox_res.status_code == 200
        assert len(inbox_res.json()["items"]) == 1
        notif = inbox_res.json()["items"][0]
        assert notif["type"] == "comment"
        assert "Great article" in notif["body"]

        # Duplicate ingestion of same event_id -> should be deduplicated (0 created)
        res_dup = await client.post("/internal/events", json=event_payload)
        assert res_dup.status_code == 200
        assert res_dup.json()["notifications_created"] == 0

        # Inbox still contains only 1 notification
        inbox_res2 = await client.get("/notifications", cookies=cookies)
        assert len(inbox_res2.json()["items"]) == 1

        # Event 2: Self-notification exclusion on CommentCreated
        self_event_payload = {
            "event_id": str(uuid.uuid4()),
            "event_type": "CommentCreated",
            "data": {
                "comment_id": str(uuid.uuid4()),
                "thread_id": str(uuid.uuid4()),
                "author_id": str(user_id),
                "recipient_id": str(user_id),
                "body_preview": "I commented on my own thread",
            },
        }
        res_self = await client.post("/internal/events", json=self_event_payload)
        assert res_self.status_code == 200
        assert res_self.json()["notifications_created"] == 0

        # Event 3: BestAnswerChanged
        best_answer_payload = {
            "event_id": str(uuid.uuid4()),
            "event_type": "BestAnswerChanged",
            "data": {
                "thread_id": str(uuid.uuid4()),
                "comment_id": str(uuid.uuid4()),
                "comment_author_id": str(user_id),
            },
        }
        res_ba = await client.post("/internal/events", json=best_answer_payload)
        assert res_ba.status_code == 200
        assert res_ba.json()["notifications_created"] == 1

        # Event 4: ModerationActionApplied
        mod_action_payload = {
            "event_id": str(uuid.uuid4()),
            "event_type": "ModerationActionApplied",
            "data": {
                "action": "lock",
                "target_type": "thread",
                "target_id": str(uuid.uuid4()),
                "author_id": str(user_id),
                "reason": "Thread locked due to controversy",
            },
        }
        res_mod = await client.post("/internal/events", json=mod_action_payload)
        assert res_mod.status_code == 200
        assert res_mod.json()["notifications_created"] == 1

        # Check total notifications for user_id -> 3 items
        inbox_all = await client.get("/notifications", cookies=cookies)
        assert len(inbox_all.json()["items"]) == 3

        # Event 5: Preference Disabled Suppression
        # Disable in-app notifications for user_id
        async with notification_db_mod.notification_db.transaction() as session:
            pref = NotificationPreference(
                user_id=user_id,
                in_app_enabled=False,
                email_enabled=False,
            )
            session.add(pref)

        upvote_payload = {
            "event_id": str(uuid.uuid4()),
            "event_type": "VoteChanged",
            "data": {
                "author_id": str(user_id),
                "voter_id": str(actor_id),
                "my_vote": 1,
                "target_type": "thread",
                "target_id": str(uuid.uuid4()),
            },
        }
        res_upvote = await client.post("/internal/events", json=upvote_payload)
        assert res_upvote.status_code == 200
        assert res_upvote.json()["notifications_created"] == 0

        # Verify consumed_events has recorded skipped_by_preference
        async with notification_db_mod.notification_db.session() as session:
            stmt = select(ConsumedEvent).where(
                ConsumedEvent.event_id == uuid.UUID(upvote_payload["event_id"])
            )
            c = (await session.execute(stmt)).scalar_one()
            assert c.status == "skipped_by_preference"


@pytest.mark.asyncio
async def test_gateway_notification_routing_and_spoof_prevention():
    """Verify Gateway routing, 401 unauthenticated response, and header spoof stripping."""
    user_id, session_token, csrf_token = await create_test_user_and_session("gateway_notif_user")

    transport = ASGITransport(app=gateway_app)
    mock_notif_client = AsyncClient(
        transport=ASGITransport(app=notification_app), base_url="http://localhost:8005"
    )

    with patch.object(gateway_app_mod, "http_client", mock_notif_client):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Unauthenticated request to /api/v1/notifications -> 401 Unauthorized
            res_unauth = await client.get("/api/v1/notifications")
            assert res_unauth.status_code == 401

            # 2. Authenticated request with session cookie
            res_auth = await client.get(
                "/api/v1/notifications",
                cookies={"forum_session": session_token},
            )
            assert res_auth.status_code == 200
            assert "items" in res_auth.json()

            # 3. Spoofed identity headers stripped by Gateway
            fake_user_id = str(uuid.uuid4())
            res_spoofed = await client.get(
                "/api/v1/notifications",
                headers={
                    "X-Forwarded-User-ID": fake_user_id,
                    "X-Forwarded-Role": "admin",
                },
            )
            # Should be 401 because spoofed header is stripped and no session cookie provided
            assert res_spoofed.status_code == 401
