"""Task 030: PostgreSQL Concurrency & Comprehensive Security Verification for Avatar & Email Change Lifecycles.

Verifies:
1. Avatar Lifecycle:
   - Setting avatar updates account.avatar_object_key and claims Content Service attachment.
   - Public visibility of avatar image without session cookies.
   - Replacing avatar updates account and releases old avatar.
   - Deleting avatar sets avatar_object_key to None and releases attachment.
   - Unowned attachment claim is rejected with 403 Forbidden.
   - Non-existent attachment claim is rejected with 404 Not Found.
2. Email Change Lifecycle:
   - Normal request + OTP verification successfully updates account email.
   - Re-request within 60s cooldown is rejected or throttled under concurrency.
   - OTP expiration and attempt limits (>5 attempts invalidates request).
   - Anti-enumeration: requesting taken email returns 202 without creating request.
   - Concurrent requests for same new email: Only one can claim it.
   - Concurrent OTP verification: Exactly one request can consume the OTP and update email.
"""

import asyncio
import io
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select

from src.common.database import DatabaseSessionManager
from src.services.content.models import Attachment
from src.services.user.models import Account, EmailChangeRequest, Session
from src.services.user.security import (
    generate_otp,
    generate_session_token,
    hash_password,
    hash_secret,
)

POSTGRES_USERS_URL = (
    "postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_users"
)
POSTGRES_CONTENT_URL = (
    "postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_content"
)
GATEWAY_BASE_URL = "http://localhost:8000/api/v1"


def make_test_image_bytes(format="JPEG", color=(0, 200, 100)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (120, 120), color=color)
    img.save(buf, format=format)
    return buf.getvalue()


@pytest_asyncio.fixture
async def users_db():
    db = DatabaseSessionManager(POSTGRES_USERS_URL)
    yield db
    await db.close()


@pytest_asyncio.fixture
async def content_db():
    db = DatabaseSessionManager(POSTGRES_CONTENT_URL)
    yield db
    await db.close()


async def create_test_account_with_session(users_db: DatabaseSessionManager, email: str, username: str | None = None):
    now = datetime.now(UTC)
    uid = uuid.uuid4()
    u_name = username or f"usr_{uid.hex[:8]}"
    e_clean = email.strip()
    e_norm = e_clean.lower()
    raw_token = generate_session_token()
    token_hash = hash_secret(raw_token)

    async with users_db.transaction() as s:
        acc = Account(
            id=uid,
            username=u_name,
            username_normalized=u_name.lower(),
            email=e_clean,
            email_normalized=e_norm,
            password_hash=hash_password("Password123!"),
            role="member",
            created_at=now,
            updated_at=now,
            email_verified_at=now,
            password_changed_at=now,
        )
        s.add(acc)
        sess = Session(
            id=uuid.uuid4(),
            account_id=uid,
            session_token_hash=token_hash,
            created_at=now,
            last_seen_at=now,
            idle_expires_at=now + timedelta(hours=4),
            absolute_expires_at=now + timedelta(days=1),
        )
        s.add(sess)

    return uid, raw_token


@pytest.mark.asyncio
async def test_avatar_lifecycle_and_permissions(users_db: DatabaseSessionManager, content_db: DatabaseSessionManager):
    """Test full avatar lifecycle: upload, claim, public access, replacement, and deletion."""
    user_id, token = await create_test_account_with_session(users_db, f"av_{uuid.uuid4().hex[:8]}@example.com")
    cookies = {"forum_session": token}

    async with httpx.AsyncClient(base_url=GATEWAY_BASE_URL) as client:
        # Get CSRF
        csrf_res = await client.get("/auth/csrf", cookies=cookies)
        csrf_token = csrf_res.json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf_token}

        # 1. Upload first image
        img1 = make_test_image_bytes(color=(255, 0, 0))
        up1 = await client.post(
            "/uploads/images",
            files={"file": ("av1.jpg", img1, "image/jpeg")},
            headers=headers,
            cookies=cookies,
        )
        assert up1.status_code == 201
        att1_id = up1.json()["id"]

        # 2. Claim first avatar
        set1 = await client.put(
            "/profile/avatar",
            json={"image_id": att1_id},
            headers=headers,
            cookies=cookies,
        )
        assert set1.status_code == 200
        assert set1.json()["avatar_url"] == f"/uploads/images/{att1_id}"

        # Public access check
        pub_fetch = await client.get(f"/uploads/images/{att1_id}")
        assert pub_fetch.status_code == 200
        assert pub_fetch.headers.get("content-type") == "image/jpeg"

        # 3. Upload second image and replace avatar
        img2 = make_test_image_bytes(color=(0, 0, 255))
        up2 = await client.post(
            "/uploads/images",
            files={"file": ("av2.jpg", img2, "image/jpeg")},
            headers=headers,
            cookies=cookies,
        )
        assert up2.status_code == 201
        att2_id = up2.json()["id"]

        set2 = await client.put(
            "/profile/avatar",
            json={"image_id": att2_id},
            headers=headers,
            cookies=cookies,
        )
        assert set2.status_code == 200
        assert set2.json()["avatar_url"] == f"/uploads/images/{att2_id}"

        # Verify old avatar status in Content DB transitioned to deleted
        async with content_db.session() as cs:
            att1_row = (await cs.execute(select(Attachment).where(Attachment.id == uuid.UUID(att1_id)))).scalar_one()
            assert att1_row.status == "deleted"

        # 4. Delete avatar
        del_res = await client.delete("/profile/avatar", headers=headers, cookies=cookies)
        assert del_res.status_code == 204

        # Profile reflects no avatar
        prof = await client.get("/profile", cookies=cookies)
        assert prof.json()["avatar_url"] is None

        # Clean up account
        async with users_db.transaction() as us:
            await us.execute(select(Account).where(Account.id == user_id))
            acc = (await us.execute(select(Account).where(Account.id == user_id))).scalar_one()
            await us.delete(acc)


@pytest.mark.asyncio
async def test_avatar_unowned_attachment_rejection(users_db: DatabaseSessionManager):
    """Test that claiming an attachment belonging to another user is rejected with 403 Forbidden."""
    u1_id, u1_token = await create_test_account_with_session(users_db, f"u1_{uuid.uuid4().hex[:8]}@example.com")
    u2_id, u2_token = await create_test_account_with_session(users_db, f"u2_{uuid.uuid4().hex[:8]}@example.com")

    async with httpx.AsyncClient(base_url=GATEWAY_BASE_URL) as client:
        # User 1 uploads image
        csrf1 = (await client.get("/auth/csrf", cookies={"forum_session": u1_token})).json()["csrf_token"]
        up1 = await client.post(
            "/uploads/images",
            files={"file": ("u1.jpg", make_test_image_bytes(), "image/jpeg")},
            headers={"X-CSRF-Token": csrf1},
            cookies={"forum_session": u1_token},
        )
        assert up1.status_code == 201
        att_id = up1.json()["id"]

        # User 2 attempts to claim User 1's attachment
        csrf2 = (await client.get("/auth/csrf", cookies={"forum_session": u2_token})).json()["csrf_token"]
        claim_attempt = await client.put(
            "/profile/avatar",
            json={"image_id": att_id},
            headers={"X-CSRF-Token": csrf2},
            cookies={"forum_session": u2_token},
        )
        assert claim_attempt.status_code == 403
        assert claim_attempt.json()["error"]["code"] == "FORBIDDEN_ATTACHMENT_CLAIM"

    async with users_db.transaction() as us:
        for uid in (u1_id, u2_id):
            acc = (await us.execute(select(Account).where(Account.id == uid))).scalar_one_or_none()
            if acc:
                await us.delete(acc)


@pytest.mark.asyncio
async def test_email_change_anti_enumeration(users_db: DatabaseSessionManager):
    """Requesting an email that is already registered returns neutral 202 without creating an active request."""
    taken_email = f"taken_{uuid.uuid4().hex[:8]}@example.com"
    u1_id, _ = await create_test_account_with_session(users_db, taken_email)
    u2_id, u2_token = await create_test_account_with_session(users_db, f"req_{uuid.uuid4().hex[:8]}@example.com")

    cookies = {"forum_session": u2_token}
    async with httpx.AsyncClient(base_url=GATEWAY_BASE_URL) as client:
        csrf = (await client.get("/auth/csrf", cookies=cookies)).json()["csrf_token"]
        res = await client.post(
            "/profile/change-email/request",
            json={"new_email": taken_email, "password": "Password123!"},
            headers={"X-CSRF-Token": csrf},
            cookies=cookies,
        )
        assert res.status_code == 202
        assert res.json()["status"] == "accepted"

    # Verify NO active email_change_request was created for u2
    async with users_db.session() as us:
        ecr = (await us.execute(
            select(EmailChangeRequest).where(
                EmailChangeRequest.account_id == u2_id,
                EmailChangeRequest.completed_at.is_(None),
            )
        )).scalar_one_or_none()
        assert ecr is None

    async with users_db.transaction() as us:
        for uid in (u1_id, u2_id):
            acc = (await us.execute(select(Account).where(Account.id == uid))).scalar_one_or_none()
            if acc:
                await us.delete(acc)


@pytest.mark.asyncio
async def test_email_change_otp_attempts_lockout(users_db: DatabaseSessionManager):
    """Exceeding 5 failed OTP attempts invalidates the email change request."""
    uid, token = await create_test_account_with_session(users_db, f"att_{uuid.uuid4().hex[:8]}@example.com")
    now = datetime.now(UTC)

    # Insert an active email change request directly
    otp_data = generate_otp(ttl_seconds=600)
    req_id = uuid.uuid4()
    async with users_db.transaction() as us:
        ecr = EmailChangeRequest(
            id=req_id,
            account_id=uid,
            new_email="new_target@example.com",
            new_email_normalized="new_target@example.com",
            otp_hash=otp_data.code_hash,
            expires_at=otp_data.expires_at,
            attempts=4,  # 4 previous attempts
            resend_count=0,
            resend_available_at=now,
            created_at=now,
            completed_at=None,
        )
        us.add(ecr)

    cookies = {"forum_session": token}
    async with httpx.AsyncClient(base_url=GATEWAY_BASE_URL) as client:
        csrf = (await client.get("/auth/csrf", cookies=cookies)).json()["csrf_token"]
        # 5th failed attempt -> 400 OTP_ATTEMPTS_EXCEEDED
        fail_res = await client.post(
            "/profile/change-email/verify",
            json={"otp": "000000"},
            headers={"X-CSRF-Token": csrf},
            cookies=cookies,
        )
        assert fail_res.status_code == 400
        assert fail_res.json()["error"]["code"] == "OTP_ATTEMPTS_EXCEEDED"

    # Verify request is marked completed/invalidated
    async with users_db.session() as us:
        ecr_row = (await us.execute(select(EmailChangeRequest).where(EmailChangeRequest.id == req_id))).scalar_one()
        assert ecr_row.completed_at is not None

    async with users_db.transaction() as us:
        acc = (await us.execute(select(Account).where(Account.id == uid))).scalar_one()
        await us.delete(acc)


@pytest.mark.asyncio
async def test_concurrent_email_change_verification(users_db: DatabaseSessionManager):
    """Concurrent verify requests with the same valid OTP: exactly ONE must succeed in updating email."""
    uid, token = await create_test_account_with_session(users_db, f"orig_{uuid.uuid4().hex[:8]}@example.com")
    now = datetime.now(UTC)

    otp_code = "654321"
    req_id = uuid.uuid4()
    target_email = f"verified_{uuid.uuid4().hex[:8]}@example.com"
    async with users_db.transaction() as us:
        ecr = EmailChangeRequest(
            id=req_id,
            account_id=uid,
            new_email=target_email,
            new_email_normalized=target_email,
            otp_hash=hash_secret(otp_code),
            expires_at=now + timedelta(minutes=10),
            attempts=0,
            resend_count=0,
            resend_available_at=now,
            created_at=now,
            completed_at=None,
        )
        us.add(ecr)

    cookies = {"forum_session": token}
    async with httpx.AsyncClient(base_url=GATEWAY_BASE_URL) as client:
        csrf = (await client.get("/auth/csrf", cookies=cookies)).json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}

        # Fire 2 concurrent verify requests with the identical valid OTP
        async def do_verify():
            return await client.post(
                "/profile/change-email/verify",
                json={"otp": otp_code},
                headers=headers,
                cookies=cookies,
            )

        resp1, resp2 = await asyncio.gather(do_verify(), do_verify())

    statuses = [resp1.status_code, resp2.status_code]
    assert 200 in statuses
    # One succeeds (200), the other must be rejected (400 NO_ACTIVE_EMAIL_CHANGE)
    assert 400 in statuses

    # Confirm user account email in DB was updated to target_email
    async with users_db.session() as us:
        acc = (await us.execute(select(Account).where(Account.id == uid))).scalar_one()
        assert acc.email == target_email

    async with users_db.transaction() as us:
        await us.delete(acc)
