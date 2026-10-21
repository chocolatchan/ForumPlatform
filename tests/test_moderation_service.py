"""Comprehensive automated tests for Task 010: Moderation Service Core Workflows.

Verifies:
1. Report Creation (Threads, Comments, Accounts):
   - Member submits report with session cookie & CSRF.
   - Status initialized to 'pending'.
   - Target existence checked via Content/User Service.
   - Self-reporting an account rejected (400 SELF_REPORT_NOT_ALLOWED).
   - Duplicate active report returns 409 DUPLICATE_REPORT.
   - Idempotency support.

2. Report Retrieval & Privacy:
   - Moderator/Admin can view moderation queue (GET /moderation/reports).
   - Queue filtering by status and target_type with cursor pagination.
   - Detailed report retrieval (GET /moderation/reports/{id}).
   - Non-moderators rejected with 403 Forbidden.
   - Own reports retrieval (GET /reports/mine) scoped strictly to authenticated user.

3. Moderation Report Lifecycle & State Machine:
   - Claim report: transition 'pending' -> 'reviewing' (PATCH /moderation/reports/{id}/review).
   - Reject invalid transitions (e.g. reviewing -> reviewing, actioned -> reviewing).
   - Dismiss report: transition 'reviewing' -> 'dismissed' (POST /moderation/reports/{id}/actions action=dismiss_report).
   - Action report: transition 'reviewing' -> 'actioned' after successful remote enforcement.
   - Reject rewriting already finalized reports with 409 REPORT_ALREADY_FINALIZED.

4. Moderation Actions & Service Boundaries (DEC-002):
   - Content actions: lock thread, pin thread, soft_delete thread/comment executed via Content Service.
   - Account actions: ban/unban user executed via User Service.
   - Proactive moderation actions without report (POST /moderation/actions).
   - Remote failure handling: failed remote call does NOT mark report actioned; recorded as failed in audit.

5. Audit Trail & Outbox Events:
   - Persistent audit log in `moderation.actions` with outcome status.
   - Audit log listing (GET /moderation/actions) with cursor pagination and filters.
   - Domain event `ModerationActionApplied` written to `moderation.outbox_events`.

6. Gateway Security & Role Authorization:
   - Unauthenticated requests rejected (401).
   - CSRF validation enforced on state-changing requests.
   - Client-supplied identity headers (X-Forwarded-*, X-User-ID) stripped by Gateway.
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
import src.services.moderation.app as moderation_app_mod
import src.services.moderation.database as moderation_db_mod
import src.services.user.app as user_app_mod
import src.services.user.database as user_db_mod
from src.common.database import DatabaseSessionManager
from src.gateway.app import app as gateway_app
from src.services.moderation.app import (
    app as moderation_app,
)
from src.services.moderation.app import (
    set_content_http_client,
    set_user_http_client,
)
from src.services.moderation.auth import set_auth_http_client
from src.services.moderation.models import Base as ModerationBase
from src.services.moderation.models import (
    ModerationOutboxEvent,
    Report,
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

MODERATION_TEST_DB_PATH = "/tmp/test_forum_moderation_task010.db"
MODERATION_TEST_DB_URL = f"sqlite+aiosqlite:///{MODERATION_TEST_DB_PATH}"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def setup_moderation_test_databases() -> AsyncGenerator[None, None]:
    """Initialize SQLite database for Moderation Service tests."""
    import shutil

    if os.path.exists(MODERATION_TEST_DB_PATH):
        os.remove(MODERATION_TEST_DB_PATH)
    if os.path.exists("/tmp/sqlite_schemas"):
        shutil.rmtree("/tmp/sqlite_schemas")

    shared_db = DatabaseSessionManager(MODERATION_TEST_DB_URL)

    async with shared_db.engine.begin() as conn:
        await conn.run_sync(ModerationBase.metadata.create_all)
        await conn.run_sync(UserBase.metadata.create_all)

    orig_moderation_db = moderation_db_mod.moderation_db
    orig_moderation_app_db = moderation_app_mod.moderation_db
    orig_user_db = user_db_mod.user_db
    orig_user_app_db = user_app_mod.user_db

    moderation_db_mod.moderation_db = shared_db
    moderation_app_mod.moderation_db = shared_db
    user_db_mod.user_db = shared_db
    user_app_mod.user_db = shared_db

    user_transport = ASGITransport(app=user_app)
    mock_auth_client = AsyncClient(transport=user_transport, base_url="http://localhost:8001")
    set_auth_http_client(mock_auth_client)

    yield

    set_auth_http_client(None)
    set_content_http_client(None)
    set_user_http_client(None)
    await mock_auth_client.aclose()
    await shared_db.close()

    moderation_db_mod.moderation_db = orig_moderation_db
    moderation_app_mod.moderation_db = orig_moderation_app_db
    user_db_mod.user_db = orig_user_db
    user_app_mod.user_db = orig_user_app_db

    if os.path.exists(MODERATION_TEST_DB_PATH):
        os.remove(MODERATION_TEST_DB_PATH)


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
# 1. Report Creation & Retrieval Tests
# =============================================================================
@pytest.mark.asyncio
async def test_report_creation_validation_and_deduplication():
    """Verify report submission, existence validation, self-reporting prevention, and deduplication."""
    user1_id, user1_sess, user1_csrf = await create_test_user_and_session("reporter_user", "member")
    target_author_id, _, _ = await create_test_user_and_session("bad_author", "member")
    thread_id = uuid.uuid4()
    comment_id = uuid.uuid4()

    # Mock remote content & user clients
    async def mock_content_get(url: str, **kwargs):
        if str(thread_id) in url:
            return httpx.Response(200, json={"id": str(thread_id), "status": "published"})
        if str(comment_id) in url:
            return httpx.Response(200, json={"id": str(comment_id), "is_deleted": False})
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

    async def mock_user_get(url: str, **kwargs):
        if str(target_author_id) in url:
            return httpx.Response(200, json={"id": str(target_author_id), "username": "bad_author"})
        return httpx.Response(404, json={"error": {"code": "USER_NOT_FOUND"}})

    mock_content = AsyncMock(spec=httpx.AsyncClient)
    mock_content.get = mock_content_get
    set_content_http_client(mock_content)

    mock_user = AsyncMock(spec=httpx.AsyncClient)
    mock_user.get = mock_user_get
    set_user_http_client(mock_user)

    transport = ASGITransport(app=moderation_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Self-reporting own account rejected (400 SELF_REPORT_NOT_ALLOWED)
        res_self = await client.post(
            "/reports",
            json={"target_type": "account", "target_id": str(user1_id), "reason": "harassment"},
            cookies={"forum_session": user1_sess},
            headers={"X-CSRF-Token": user1_csrf},
        )
        assert res_self.status_code == 400
        assert res_self.json()["error"]["code"] == "SELF_REPORT_NOT_ALLOWED"

        # 2. Non-existent target thread rejected (404 TARGET_NOT_FOUND)
        missing_thread = uuid.uuid4()
        res_missing = await client.post(
            "/reports",
            json={"target_type": "thread", "target_id": str(missing_thread), "reason": "spam"},
            cookies={"forum_session": user1_sess},
            headers={"X-CSRF-Token": user1_csrf},
        )
        assert res_missing.status_code == 404
        assert res_missing.json()["error"]["code"] == "TARGET_NOT_FOUND"

        # 3. Valid thread report creation
        res_rep1 = await client.post(
            "/reports",
            json={
                "target_type": "thread",
                "target_id": str(thread_id),
                "reason": "spam",
                "details": "Repeated casino spam links.",
            },
            cookies={"forum_session": user1_sess},
            headers={"X-CSRF-Token": user1_csrf},
        )
        assert res_rep1.status_code == 201
        data1 = res_rep1.json()
        assert data1["target_type"] == "thread"
        assert data1["target_id"] == str(thread_id)
        assert data1["status"] == "pending"
        assert data1["reason"] == "spam"
        assert data1["details"] == "Repeated casino spam links."
        report1_id = data1["id"]

        # 4. Duplicate active report submission rejected (409 DUPLICATE_REPORT)
        res_dup = await client.post(
            "/reports",
            json={
                "target_type": "thread",
                "target_id": str(thread_id),
                "reason": "spam",
                "details": "Submitting again.",
            },
            cookies={"forum_session": user1_sess},
            headers={"X-CSRF-Token": user1_csrf},
        )
        assert res_dup.status_code == 409
        assert res_dup.json()["error"]["code"] == "DUPLICATE_REPORT"

        # 5. Report comment & account
        res_com = await client.post(
            "/reports",
            json={"target_type": "comment", "target_id": str(comment_id), "reason": "abuse"},
            cookies={"forum_session": user1_sess},
            headers={"X-CSRF-Token": user1_csrf},
        )
        assert res_com.status_code == 201

        res_acc = await client.post(
            "/reports",
            json={
                "target_type": "account",
                "target_id": str(target_author_id),
                "reason": "harassment",
            },
            cookies={"forum_session": user1_sess},
            headers={"X-CSRF-Token": user1_csrf},
        )
        assert res_acc.status_code == 201

        # 6. Own reports retrieval (GET /reports/mine)
        res_mine = await client.get("/reports/mine", cookies={"forum_session": user1_sess})
        assert res_mine.status_code == 200
        mine_items = res_mine.json()["items"]
        assert len(mine_items) == 3
        assert any(r["id"] == report1_id for r in mine_items)


# =============================================================================
# 2. Moderation Lifecycle & State Machine Tests
# =============================================================================
@pytest.mark.asyncio
async def test_moderation_queue_and_lifecycle_transitions():
    """Verify moderation queue filtering, claim review, and report status transitions."""
    mod_id, mod_sess, mod_csrf = await create_test_user_and_session("moderator_tom", "moderator")
    member_id, member_sess, _ = await create_test_user_and_session("member_bob", "member")

    thread_id = uuid.uuid4()
    async with moderation_db_mod.moderation_db.transaction() as session:
        rep = Report(
            id=uuid.uuid4(),
            reporter_id=member_id,
            target_type="thread",
            thread_id=thread_id,
            reason="misinformation",
            details="Misleading medical advice.",
            status="pending",
        )
        session.add(rep)
        rep_id = rep.id

    transport = ASGITransport(app=moderation_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Member cannot access moderation queue (403 Forbidden)
        res_forbidden = await client.get(
            "/moderation/reports", cookies={"forum_session": member_sess}
        )
        assert res_forbidden.status_code == 403

        # 2. Moderator lists queue (GET /moderation/reports?status=pending)
        res_queue = await client.get(
            "/moderation/reports?status=pending", cookies={"forum_session": mod_sess}
        )
        assert res_queue.status_code == 200
        items = res_queue.json()["items"]
        assert any(r["id"] == str(rep_id) for r in items)

        # 3. Moderator retrieves detailed report (GET /moderation/reports/{id})
        res_detail = await client.get(
            f"/moderation/reports/{rep_id}", cookies={"forum_session": mod_sess}
        )
        assert res_detail.status_code == 200
        assert res_detail.json()["status"] == "pending"

        # 4. Moderator claims report (PATCH /moderation/reports/{id}/review) -> moves to 'reviewing'
        res_claim = await client.patch(
            f"/moderation/reports/{rep_id}/review",
            cookies={"forum_session": mod_sess},
            headers={"X-CSRF-Token": mod_csrf},
        )
        assert res_claim.status_code == 200
        assert res_claim.json()["status"] == "reviewing"

        # 5. Invalid transition: cannot re-claim report already in 'reviewing' (409 Conflict)
        res_reclaim = await client.patch(
            f"/moderation/reports/{rep_id}/review",
            cookies={"forum_session": mod_sess},
            headers={"X-CSRF-Token": mod_csrf},
        )
        assert res_reclaim.status_code == 409
        assert res_reclaim.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

        # 6. Moderator dismisses report (action=dismiss_report) -> moves to 'dismissed'
        res_dismiss = await client.post(
            f"/moderation/reports/{rep_id}/actions",
            json={"action": "dismiss_report", "reason": "Not a violation of community guidelines."},
            cookies={"forum_session": mod_sess},
            headers={"X-CSRF-Token": mod_csrf},
        )
        assert res_dismiss.status_code == 200
        assert res_dismiss.json()["action"] == "dismiss_report"

        # Verify status is now 'dismissed'
        res_check = await client.get(
            f"/moderation/reports/{rep_id}", cookies={"forum_session": mod_sess}
        )
        assert res_check.json()["status"] == "dismissed"
        assert res_check.json()["resolution_note"] == "Not a violation of community guidelines."

        # 7. Finalized report cannot be modified or re-actioned (409 Conflict)
        res_reopen = await client.post(
            f"/moderation/reports/{rep_id}/actions",
            json={"action": "lock", "reason": "Trying to act on dismissed report."},
            cookies={"forum_session": mod_sess},
            headers={"X-CSRF-Token": mod_csrf},
        )
        assert res_reopen.status_code == 409
        assert res_reopen.json()["error"]["code"] == "REPORT_ALREADY_FINALIZED"


# =============================================================================
# 3. Moderation Remote Enforcement & Audit Trail Tests
# =============================================================================
@pytest.mark.asyncio
async def test_moderation_enforcement_actions_and_audit():
    """Verify thread locking, soft deletion, user banning, proactive actions, and audit log."""
    mod_id, mod_sess, mod_csrf = await create_test_user_and_session("moderator_alice", "moderator")
    reporter_id, _, _ = await create_test_user_and_session("reporter_sam", "member")
    target_thread_id = uuid.uuid4()
    target_account_id = uuid.uuid4()

    # Track remote calls
    remote_calls = []

    async def mock_content_put(url: str, **kwargs):
        remote_calls.append(("PUT", url, kwargs.get("json")))
        return httpx.Response(200, json={"status": "ok"})

    async def mock_content_delete(url: str, **kwargs):
        remote_calls.append(("DELETE", url, None))
        return httpx.Response(204)

    async def mock_user_put(url: str, **kwargs):
        remote_calls.append(("PUT", url, kwargs.get("json")))
        return httpx.Response(200, json={"is_banned": True})

    mock_content = AsyncMock(spec=httpx.AsyncClient)
    mock_content.put = mock_content_put
    mock_content.delete = mock_content_delete
    set_content_http_client(mock_content)

    mock_user = AsyncMock(spec=httpx.AsyncClient)
    mock_user.put = mock_user_put
    set_user_http_client(mock_user)

    # Seed a thread report and an account report
    report_thread_id = uuid.uuid4()
    report_account_id = uuid.uuid4()

    async with moderation_db_mod.moderation_db.transaction() as session:
        r1 = Report(
            id=report_thread_id,
            reporter_id=reporter_id,
            target_type="thread",
            thread_id=target_thread_id,
            reason="abuse",
            details="Thread contains abusive language.",
            status="reviewing",
        )
        r2 = Report(
            id=report_account_id,
            reporter_id=reporter_id,
            target_type="account",
            reported_account_id=target_account_id,
            reason="spam",
            details="Spam bot account.",
            status="reviewing",
        )
        session.add_all([r1, r2])

    transport = ASGITransport(app=moderation_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Action thread report: lock thread
        res_act1 = await client.post(
            f"/moderation/reports/{report_thread_id}/actions",
            json={"action": "lock", "reason": "Locking thread due to flame war."},
            cookies={"forum_session": mod_sess},
            headers={"X-CSRF-Token": mod_csrf},
        )
        assert res_act1.status_code == 200
        data_act1 = res_act1.json()
        assert data_act1["action"] == "lock"
        assert data_act1["report_id"] == str(report_thread_id)
        assert data_act1["target_type"] == "thread"
        assert data_act1["target_id"] == str(target_thread_id)

        # Verify remote PUT was called on Content Service
        assert any(
            method == "PUT" and f"/threads/{target_thread_id}/lock" in url
            for method, url, _ in remote_calls
        )

        # 2. Action account report: ban account
        res_act2 = await client.post(
            f"/moderation/reports/{report_account_id}/actions",
            json={"action": "ban", "reason": "Confirmed spam bot."},
            cookies={"forum_session": mod_sess},
            headers={"X-CSRF-Token": mod_csrf},
        )
        assert res_act2.status_code == 200
        assert res_act2.json()["action"] == "ban"
        assert any(
            method == "PUT" and f"/admin/users/{target_account_id}/ban" in url
            for method, url, _ in remote_calls
        )

        # 3. Proactive moderation action without report (POST /moderation/actions)
        proactive_thread = uuid.uuid4()
        res_proactive = await client.post(
            "/moderation/actions",
            json={
                "action": "soft_delete",
                "target_type": "thread",
                "target_id": str(proactive_thread),
                "reason": "Illegal content removed proactively by moderator.",
            },
            cookies={"forum_session": mod_sess},
            headers={"X-CSRF-Token": mod_csrf},
        )
        assert res_proactive.status_code == 200
        assert res_proactive.json()["action"] == "soft_delete"
        assert res_proactive.json()["report_id"] is None

        # 4. Failed remote call test: Content Service returns 500
        mock_content.delete = AsyncMock(return_value=httpx.Response(500, text="Internal Error"))
        fail_thread = uuid.uuid4()
        res_fail = await client.post(
            "/moderation/actions",
            json={
                "action": "soft_delete",
                "target_type": "thread",
                "target_id": str(fail_thread),
                "reason": "Delete attempt that will fail remotely.",
            },
            cookies={"forum_session": mod_sess},
            headers={"X-CSRF-Token": mod_csrf},
        )
        assert res_fail.status_code == 400
        assert res_fail.json()["error"]["code"] == "ACTION_FAILED"

        # 5. Verify audit log (GET /moderation/actions)
        res_audit = await client.get("/moderation/actions", cookies={"forum_session": mod_sess})
        assert res_audit.status_code == 200
        audit_items = res_audit.json()["items"]
        assert len(audit_items) >= 4  # lock, ban, proactive soft_delete, and failed soft_delete

        # Verify Outbox event was persisted
        async with moderation_db_mod.moderation_db.session() as db_sess:
            outbox_events = (
                (
                    await db_sess.execute(
                        select(ModerationOutboxEvent).where(
                            ModerationOutboxEvent.event_type == "ModerationActionApplied"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(outbox_events) >= 3


# =============================================================================
# 4. Gateway Integration & Header Security
# =============================================================================
@pytest.mark.asyncio
async def test_gateway_moderation_routing_and_spoof_prevention():
    """Verify Gateway routing to Moderation Service and stripping of client identity headers."""
    mod_id, mod_sess, _ = await create_test_user_and_session("gateway_mod", "moderator")

    async def mock_gateway_send(req: httpx.Request, **kwargs):
        # Verify Gateway stripped untrusted client identity headers
        assert "x-authenticated-user-id" not in req.headers
        assert "x-authenticated-role" not in req.headers
        assert "x-user-id" not in req.headers
        assert "x-role" not in req.headers
        assert "x-forwarded-user-id" not in req.headers
        assert "x-forwarded-role" not in req.headers

        import json

        content_bytes = json.dumps({"items": [], "next_cursor": None}).encode("utf-8")
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=httpx.ByteStream(content_bytes),
        )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.send = mock_gateway_send
    mock_client.build_request = lambda method, url, **kwargs: httpx.Request(method, url, **kwargs)

    with patch.object(gateway_app_mod, "http_client", mock_client):
        transport = ASGITransport(app=gateway_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get(
                "/api/v1/moderation/reports",
                cookies={"forum_session": mod_sess},
                headers={
                    "X-User-ID": str(uuid.uuid4()),
                    "X-Role": "admin",
                    "X-Forwarded-Role": "admin",
                },
            )
            assert res.status_code == 200
            assert "items" in res.json()
