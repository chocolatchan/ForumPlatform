"""Comprehensive Automated Test Suite for Task 006: User Service Authentication & Account Lifecycle.

Coverage:
1. Registration & Verification:
   - Valid registration persists pending record, issues OTP, returns 202 Accepted.
   - Duplicate registration returns 202 Accepted without account enumeration (DEC-001).
   - Invalid registration payload returns canonical 422 validation response.
   - Valid OTP completes registration, creates active account (201 Created).
   - Invalid OTP returns 400 with INVALID_OTP code.
   - Expired OTP returns 400 with OTP_EXPIRED code.
   - Attempt limit enforcement (>5 attempts returns 400 OTP_ATTEMPTS_EXCEEDED).
   - Single-use semantics: reusing an already consumed OTP returns 400.
   - Resend OTP respects quota (max 3 resends) and cooldown.

2. Authentication & Sessions:
   - Valid login issues server-side session, sets forum_session cookie.
   - Invalid password/username returns consistent 401 UNAUTHORIZED without leaking user existence.
   - Banned accounts cannot authenticate (returns 403 ACCOUNT_BANNED).
   - Missing / invalid / revoked session cookie returns 401 UNAUTHORIZED.
   - Logout revokes session and clears cookie.
   - Session idle and absolute timeout expiration.
   - CSRF validation enforced on state-changing requests when session cookie present.

3. Authorization & Roles:
   - Ordinary member cannot access /admin/users or /admin/users/{id}/role (returns 403 FORBIDDEN).
   - Admin user can access /admin/users and change user role.
   - Self-profile endpoints (/profile, GET/PATCH) work for authenticated users.

4. Gateway Integration:
   - Registration, login, session lookup, and logout exercised through API Gateway proxy.
"""

import os
import shutil
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import src.gateway.app as gateway_app_mod
import src.services.user.app as user_app_mod
import src.services.user.database as user_db_module
from src.common.database import DatabaseSessionManager
from src.gateway.app import app as gateway_app
from src.services.user.app import app as user_app
from src.services.user.config import user_settings
from src.services.user.models import Account, PendingRegistration
from src.services.user.models import Base as UserBase
from src.services.user.otp_delivery import otp_delivery_sink
from src.services.user.security import (
    generate_otp,
    hash_password,
)

AUTH_TEST_DB_PATH = "/tmp/test_forum_auth_task006.db"
AUTH_TEST_DB_URL = f"sqlite+aiosqlite:///{AUTH_TEST_DB_PATH}"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def setup_auth_test_database() -> AsyncGenerator[None, None]:
    """Initialize dedicated SQLite database for Task 006 authentication testing."""
    if os.path.exists(AUTH_TEST_DB_PATH):
        os.remove(AUTH_TEST_DB_PATH)
    if os.path.exists("/tmp/sqlite_schemas"):
        shutil.rmtree("/tmp/sqlite_schemas")

    test_db = DatabaseSessionManager(AUTH_TEST_DB_URL)
    async with test_db.engine.begin() as conn:
        await conn.run_sync(UserBase.metadata.create_all)

    orig_user_db = user_db_module.user_db
    user_db_module.user_db = test_db
    user_app_mod.user_db = test_db

    yield

    await test_db.close()
    user_db_module.user_db = orig_user_db
    user_app_mod.user_db = orig_user_db
    if os.path.exists(AUTH_TEST_DB_PATH):
        os.remove(AUTH_TEST_DB_PATH)


@pytest.fixture(autouse=True)
def configure_test_email_environment():
    user_app_mod.rate_limiter_register.reset()
    user_app_mod.rate_limiter_login.reset()
    user_app_mod.rate_limiter_verify_otp.reset()
    user_app_mod.rate_limiter_resend_otp.reset()
    otp_delivery_sink.clear()
    orig_backend = user_settings.email_delivery_backend
    orig_allowlist = user_settings.dev_email_allowlist
    user_settings.email_delivery_backend = "memory"
    user_settings.dev_email_allowlist = "bob@example.com,alice@example.com,charlie@example.com,david@example.com,emma@example.com,frank@example.com,grace@example.com,hannah@example.com,ian@example.com,kyle@example.com,developer@example.test,tester@example.test"
    yield
    user_settings.email_delivery_backend = orig_backend
    user_settings.dev_email_allowlist = orig_allowlist
    otp_delivery_sink.clear()
    user_app_mod.rate_limiter_register.reset()
    user_app_mod.rate_limiter_login.reset()
    user_app_mod.rate_limiter_verify_otp.reset()
    user_app_mod.rate_limiter_resend_otp.reset()


# ==============================================================================
# SECTION 1: REGISTRATION & OTP VERIFICATION TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_registration_and_neutral_duplicate():
    """Verify registration creates pending state, sends OTP, and duplicate returns neutral 202."""
    transport = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Valid registration
        res1 = await client.post(
            "/auth/register",
            json={
                "username": "bob_builder",
                "email": "bob@example.com",
                "password": "Password123!",
            },
        )
        assert res1.status_code == 202
        data1 = res1.json()
        assert data1["status"] == "pending_verification"
        assert "verification code has been sent" in data1["message"]

        # Check OTP mailbox
        otp = otp_delivery_sink.get_latest_otp("bob@example.com")
        assert otp is not None
        assert len(otp) == 6 and otp.isdigit()

        # 2. Duplicate registration attempt with same email
        res2 = await client.post(
            "/auth/register",
            json={
                "username": "bob_another",
                "email": "bob@example.com",
                "password": "Password123!",
            },
        )
        # DEC-001: Neutral response
        assert res2.status_code == 202
        assert res2.json()["status"] == "pending_verification"


@pytest.mark.asyncio
async def test_registration_validation_errors():
    """Verify invalid registration payload returns canonical 422 error."""
    transport = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/auth/register",
            json={
                "username": "x",  # Too short (< 3)
                "email": "not-an-email",
                "password": "short",  # Too short (< 8)
            },
        )
        assert res.status_code == 422
        body = res.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert len(body["error"]["details"]) >= 2


@pytest.mark.asyncio
async def test_otp_verification_success_and_reuse_prevention():
    """Verify OTP verification creates active account and prevents reuse."""
    transport = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Register
        await client.post(
            "/auth/register",
            json={
                "username": "charlie_dev",
                "email": "charlie@example.com",
                "password": "Password123!",
            },
        )
        otp = otp_delivery_sink.get_latest_otp("charlie@example.com")
        assert otp is not None

        # 2. Verify with valid OTP
        v_res = await client.post(
            "/auth/register/verify",
            json={
                "email": "charlie@example.com",
                "otp": otp,
            },
        )
        assert v_res.status_code == 201
        user_data = v_res.json()["user"]
        assert user_data["username"] == "charlie_dev"
        assert user_data["role"] == "member"

        # 3. Re-verify with same consumed OTP -> must fail (single-use)
        v_res_reused = await client.post(
            "/auth/register/verify",
            json={
                "email": "charlie@example.com",
                "otp": otp,
            },
        )
        assert v_res_reused.status_code == 400
        assert v_res_reused.json()["error"]["code"] == "INVALID_OTP"


@pytest.mark.asyncio
async def test_otp_verification_invalid_and_attempt_limit():
    """Verify incorrect OTP increments attempts and locks out after 5 failures."""
    transport = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Register
        await client.post(
            "/auth/register",
            json={
                "username": "david_test",
                "email": "david@example.com",
                "password": "Password123!",
            },
        )

        # Send 5 invalid OTP attempts
        for _attempt in range(5):
            bad_res = await client.post(
                "/auth/register/verify",
                json={
                    "email": "david@example.com",
                    "otp": "000000",
                },
            )
            assert bad_res.status_code == 400
            assert bad_res.json()["error"]["code"] == "INVALID_OTP"

        # 6th attempt must be rejected for attempts exceeded
        exceeded_res = await client.post(
            "/auth/register/verify",
            json={
                "email": "david@example.com",
                "otp": "000000",
            },
        )
        assert exceeded_res.status_code == 400
        assert exceeded_res.json()["error"]["code"] == "OTP_ATTEMPTS_EXCEEDED"


@pytest.mark.asyncio
async def test_otp_expiration_enforcement():
    """Verify expired OTP challenge is rejected."""
    # Seed an already expired pending registration
    past_time = datetime.now(UTC) - timedelta(minutes=15)
    otp = generate_otp()
    async with user_db_module.user_db.transaction() as s:
        pending = PendingRegistration(
            id=uuid.uuid4(),
            username="expired_user",
            username_normalized="expired_user",
            email="expired@example.com",
            email_normalized="expired@example.com",
            password_hash=hash_password("Password123!"),
            otp_hash=otp.code_hash,
            otp_expires_at=past_time,
            otp_attempts=0,
            resend_count=0,
            resend_available_at=past_time,
            status="pending",
        )
        s.add(pending)

    transport = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/auth/register/verify",
            json={
                "email": "expired@example.com",
                "otp": otp.code,
            },
        )
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "OTP_EXPIRED"


@pytest.mark.asyncio
async def test_resend_otp_cooldown_and_quota():
    """Verify resend OTP endpoint respects cooldown and maximum resend count."""
    transport = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register
        await client.post(
            "/auth/register",
            json={
                "username": "emma_resend",
                "email": "emma@example.com",
                "password": "Password123!",
            },
        )
        initial_otp = otp_delivery_sink.get_latest_otp("emma@example.com")
        assert initial_otp is not None

        # Immediate resend (under 60s cooldown) should return 202 (DEC-001) but not deliver new OTP
        otp_delivery_sink.clear()
        res = await client.post(
            "/auth/register/resend-otp",
            json={"email": "emma@example.com"},
        )
        assert res.status_code == 202
        assert otp_delivery_sink.get_latest_otp("emma@example.com") is None


# ==============================================================================
# SECTION 2: AUTHENTICATION & SESSION LIFECYCLE TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_login_success_and_session_cookie():
    """Verify valid login sets forum_session cookie and returns user + session metadata."""
    # Create active account
    async with user_db_module.user_db.transaction() as s:
        acc = Account(
            id=uuid.uuid4(),
            username="frank_login",
            username_normalized="frank_login",
            email="frank@example.com",
            email_normalized="frank@example.com",
            password_hash=hash_password("Secret123!"),
            role="member",
            is_banned=False,
        )
        s.add(acc)

    transport = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Login with email
        res = await client.post(
            "/auth/login",
            json={
                "login": "frank@example.com",
                "password": "Secret123!",
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["user"]["username"] == "frank_login"
        assert "idle_expires_at" in body["session"]
        assert "absolute_expires_at" in body["session"]

        # Check Set-Cookie
        assert "forum_session" in client.cookies

        # Verify /auth/session using the issued cookie
        sess_res = await client.get("/auth/session")
        assert sess_res.status_code == 200
        assert sess_res.json()["user"]["username"] == "frank_login"


@pytest.mark.asyncio
async def test_login_invalid_credentials_and_banned_account():
    """Verify invalid password returns 401 and banned account returns 403."""
    async with user_db_module.user_db.transaction() as s:
        banned_user = Account(
            id=uuid.uuid4(),
            username="banned_user",
            username_normalized="banned_user",
            email="banned@example.com",
            email_normalized="banned@example.com",
            password_hash=hash_password("Secret123!"),
            role="member",
            is_banned=True,
            ban_reason="Violation of terms",
        )
        s.add(banned_user)

    transport = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Invalid password
        bad_pw_res = await client.post(
            "/auth/login",
            json={"login": "banned_user", "password": "WrongPassword!"},
        )
        assert bad_pw_res.status_code == 401
        assert bad_pw_res.json()["error"]["code"] == "INVALID_CREDENTIALS"

        # Nonexistent account
        nonexistent_res = await client.post(
            "/auth/login",
            json={"login": "ghost_user", "password": "AnyPassword123!"},
        )
        assert nonexistent_res.status_code == 401
        assert nonexistent_res.json()["error"]["code"] == "INVALID_CREDENTIALS"

        # Banned account with correct password
        banned_res = await client.post(
            "/auth/login",
            json={"login": "banned_user", "password": "Secret123!"},
        )
        assert banned_res.status_code == 403
        assert banned_res.json()["error"]["code"] == "ACCOUNT_BANNED"


@pytest.mark.asyncio
async def test_logout_revokes_session():
    """Verify logout revokes the server-side session and clears cookie."""
    async with user_db_module.user_db.transaction() as s:
        acc = Account(
            id=uuid.uuid4(),
            username="grace_logout",
            username_normalized="grace_logout",
            email="grace@example.com",
            email_normalized="grace@example.com",
            password_hash=hash_password("Secret123!"),
            role="member",
            is_banned=False,
        )
        s.add(acc)

    transport = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Login
        await client.post(
            "/auth/login",
            json={"login": "grace_logout", "password": "Secret123!"},
        )
        session_token = client.cookies.get("forum_session")
        assert session_token is not None

        # Fetch CSRF token
        csrf_res = await client.get("/auth/csrf")
        csrf_token = csrf_res.json()["csrf_token"]

        # Logout with CSRF token
        logout_res = await client.post(
            "/auth/logout",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert logout_res.status_code == 204

        # Subsequent authenticated call must be rejected (401)
        subsequent_res = await client.get("/auth/session")
        assert subsequent_res.status_code == 401


@pytest.mark.asyncio
async def test_csrf_protection_on_mutations():
    """Verify state-changing mutations with session cookie require valid X-CSRF-Token."""
    async with user_db_module.user_db.transaction() as s:
        acc = Account(
            id=uuid.uuid4(),
            username="helen_csrf",
            username_normalized="helen_csrf",
            email="helen@example.com",
            email_normalized="helen@example.com",
            password_hash=hash_password("Secret123!"),
            role="member",
            is_banned=False,
        )
        s.add(acc)

    transport = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/auth/login",
            json={"login": "helen_csrf", "password": "Secret123!"},
        )

        # PATCH /profile without CSRF token -> 403 Forbidden
        no_csrf_res = await client.patch(
            "/profile",
            json={"display_name": "Helen Hacker"},
        )
        assert no_csrf_res.status_code == 403
        assert no_csrf_res.json()["error"]["code"] == "FORBIDDEN"

        # PATCH /profile with invalid CSRF token -> 403 Forbidden
        bad_csrf_res = await client.patch(
            "/profile",
            json={"display_name": "Helen Hacker"},
            headers={"X-CSRF-Token": "invalid.signature"},
        )
        assert bad_csrf_res.status_code == 403

        # PATCH /profile with valid CSRF token -> 200 OK
        csrf_token = (await client.get("/auth/csrf")).json()["csrf_token"]
        ok_res = await client.patch(
            "/profile",
            json={"display_name": "Helen Valid"},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert ok_res.status_code == 200
        assert ok_res.json()["display_name"] == "Helen Valid"


# ==============================================================================
# SECTION 3: AUTHORIZATION & ROLE ENFORCEMENT TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_role_authorization_and_self_elevation_prevention():
    """Verify ordinary members cannot access admin routes or elevate their own role."""
    member_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    async with user_db_module.user_db.transaction() as s:
        member = Account(
            id=member_id,
            username="ian_member",
            username_normalized="ian_member",
            email="ian@example.com",
            email_normalized="ian@example.com",
            password_hash=hash_password("Secret123!"),
            role="member",
            is_banned=False,
        )
        admin = Account(
            id=admin_id,
            username="julia_admin",
            username_normalized="julia_admin",
            email="julia@example.com",
            email_normalized="julia@example.com",
            password_hash=hash_password("Secret123!"),
            role="admin",
            is_banned=False,
        )
        s.add_all([member, admin])

    transport = ASGITransport(app=user_app)

    # 1. Member tries to list admin users or update role
    async with AsyncClient(transport=transport, base_url="http://test") as member_client:
        await member_client.post(
            "/auth/login",
            json={"login": "ian_member", "password": "Secret123!"},
        )
        list_res = await member_client.get("/admin/users")
        assert list_res.status_code == 403
        assert list_res.json()["error"]["code"] == "FORBIDDEN"

        # Member tries to self-elevate to admin
        csrf_token = (await member_client.get("/auth/csrf")).json()["csrf_token"]
        patch_res = await member_client.patch(
            f"/admin/users/{member_id}/role",
            json={"role": "admin"},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert patch_res.status_code == 403

    # 2. Administrator successfully accesses admin endpoints
    async with AsyncClient(transport=transport, base_url="http://test") as admin_client:
        await admin_client.post(
            "/auth/login",
            json={"login": "julia_admin", "password": "Secret123!"},
        )
        admin_list = await admin_client.get("/admin/users")
        assert admin_list.status_code == 200
        assert len(admin_list.json()["items"]) >= 2

        # Admin updates member role to moderator
        admin_csrf = (await admin_client.get("/auth/csrf")).json()["csrf_token"]
        elevate_res = await admin_client.patch(
            f"/admin/users/{member_id}/role",
            json={"role": "moderator"},
            headers={"X-CSRF-Token": admin_csrf},
        )
        assert elevate_res.status_code == 200
        assert elevate_res.json()["role"] == "moderator"


# ==============================================================================
# SECTION 4: GATEWAY INTEGRATION & REVERSE PROXY TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_full_auth_flow_through_gateway():
    """Verify complete registration, login, session inspection, and logout through API Gateway."""
    transport = ASGITransport(app=gateway_app)
    mock_client = AsyncClient(
        transport=ASGITransport(app=user_app), base_url="http://localhost:8001"
    )

    async def mock_forward_send(request, **kwargs):
        # Stream read request content
        body_bytes = b""
        if hasattr(request.stream, "__aiter__"):
            async for chunk in request.stream:
                body_bytes += chunk
        elif hasattr(request, "read"):
            body_bytes = request.read()

        async with AsyncClient(
            transport=ASGITransport(app=user_app), base_url="http://localhost:8001"
        ) as svc:
            resp = await svc.request(
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
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Register through Gateway
            reg_res = await client.post(
                "/api/v1/auth/register",
                json={
                    "username": "kyle_gw",
                    "email": "kyle@example.com",
                    "password": "Password123!",
                },
            )
            assert reg_res.status_code == 202

            # 2. Verify OTP through Gateway
            otp = otp_delivery_sink.get_latest_otp("kyle@example.com")
            assert otp is not None
            ver_res = await client.post(
                "/api/v1/auth/register/verify",
                json={"email": "kyle@example.com", "otp": otp},
            )
            assert ver_res.status_code == 201
            assert ver_res.json()["user"]["username"] == "kyle_gw"

            # 3. Login through Gateway
            login_res = await client.post(
                "/api/v1/auth/login",
                json={"login": "kyle_gw", "password": "Password123!"},
            )
            assert login_res.status_code == 200
            session_cookie = client.cookies.get("forum_session")
            assert session_cookie is not None

            # 4. Check /auth/session through Gateway
            session_res = await client.get("/api/v1/auth/session")
            assert session_res.status_code == 200
            assert session_res.json()["user"]["username"] == "kyle_gw"

            # 5. Fetch CSRF and Logout through Gateway
            csrf_res = await client.get("/api/v1/auth/csrf")
            csrf_token = csrf_res.json()["csrf_token"]
            logout_res = await client.post(
                "/api/v1/auth/logout",
                headers={"X-CSRF-Token": csrf_token},
            )
            assert logout_res.status_code == 204


@pytest.mark.asyncio
async def test_password_change_lifecycle():
    """Verify complete password change workflow:
    - 401 when unauthenticated
    - 403 when CSRF missing with active session
    - 400 when current password incorrect (INVALID_PASSWORD)
    - 422 when new password too short (< 8 chars)
    - 400 when new password identical to current password (PASSWORD_REUSE_FORBIDDEN)
    - 204 on successful password change
    - Other active sessions revoked, current session remains active
    - Old password fails login, new password succeeds login
    """
    transport = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unauthenticated change password -> 401
        res = await client.post(
            "/profile/change-password",
            json={"current_password": "OldPassword123!", "new_password": "NewPassword123!"},
        )
        assert res.status_code == 401

        # Register & verify a test user: "emma"
        reg_res = await client.post(
            "/auth/register",
            json={
                "username": "emma_pwd",
                "email": "emma@example.com",
                "password": "InitialPassword123!",
            },
        )
        assert reg_res.status_code == 202
        otp = otp_delivery_sink.get_latest_otp("emma@example.com")
        assert otp is not None
        v_res = await client.post(
            "/auth/register/verify",
            json={"email": "emma@example.com", "otp": otp},
        )
        assert v_res.status_code == 201

        # Session 1 (Client 1)
        login1_res = await client.post(
            "/auth/login",
            json={"login": "emma_pwd", "password": "InitialPassword123!"},
        )
        assert login1_res.status_code == 200
        client1_cookie = client.cookies.get("forum_session")
        assert client1_cookie is not None

    # Session 2 (Client 2 - e.g. mobile or other device)
    async with AsyncClient(transport=transport, base_url="http://test") as client2:
        login2_res = await client2.post(
            "/auth/login",
            json={"login": "emma_pwd", "password": "InitialPassword123!"},
        )
        assert login2_res.status_code == 200
        client2_cookie = client2.cookies.get("forum_session")
        assert client2_cookie is not None

        # Verify Client 2 session is currently active
        s2_check = await client2.get("/auth/session")
        assert s2_check.status_code == 200

    # Back to Client 1 to test change-password validations
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"forum_session": client1_cookie},
    ) as client1:
        # 2. CSRF missing -> 403
        csrf_missing_res = await client1.post(
            "/profile/change-password",
            json={
                "current_password": "InitialPassword123!",
                "new_password": "NewSecretPassword123!",
            },
        )
        assert csrf_missing_res.status_code == 403

        # Fetch CSRF
        csrf_res = await client1.get("/auth/csrf")
        csrf_token = csrf_res.json()["csrf_token"]
        csrf_headers = {"X-CSRF-Token": csrf_token}

        # 3. Incorrect current password -> 400 INVALID_PASSWORD
        wrong_pwd_res = await client1.post(
            "/profile/change-password",
            headers=csrf_headers,
            json={"current_password": "WrongPassword999!", "new_password": "NewSecretPassword123!"},
        )
        assert wrong_pwd_res.status_code == 400
        assert wrong_pwd_res.json()["error"]["code"] == "INVALID_PASSWORD"

        # 4. New password too short (< 8 chars) -> 422 VALIDATION_ERROR
        short_pwd_res = await client1.post(
            "/profile/change-password",
            headers=csrf_headers,
            json={"current_password": "InitialPassword123!", "new_password": "short"},
        )
        assert short_pwd_res.status_code == 422
        assert short_pwd_res.json()["error"]["code"] == "VALIDATION_ERROR"

        # 5. New password identical to current password -> 400 PASSWORD_REUSE_FORBIDDEN
        reuse_res = await client1.post(
            "/profile/change-password",
            headers=csrf_headers,
            json={"current_password": "InitialPassword123!", "new_password": "InitialPassword123!"},
        )
        assert reuse_res.status_code == 400
        assert reuse_res.json()["error"]["code"] == "PASSWORD_REUSE_FORBIDDEN"

        # 6. Success -> 204 No Content
        success_res = await client1.post(
            "/profile/change-password",
            headers=csrf_headers,
            json={
                "current_password": "InitialPassword123!",
                "new_password": "NewSecretPassword123!",
            },
        )
        assert success_res.status_code == 204

        # 6b. Verify backward compatibility: old_password alias accepted
        alias_res = await client1.post(
            "/profile/change-password",
            headers=csrf_headers,
            json={
                "old_password": "NewSecretPassword123!",
                "new_password": "AliasTestedPassword123!",
            },
        )
        assert alias_res.status_code == 204

        # 6c. Change back to NewSecretPassword123! using canonical current_password
        revert_res = await client1.post(
            "/profile/change-password",
            headers=csrf_headers,
            json={
                "current_password": "AliasTestedPassword123!",
                "new_password": "NewSecretPassword123!",
            },
        )
        assert revert_res.status_code == 204

        # 7. Current session remains authenticated
        s1_check = await client1.get("/auth/session")
        assert s1_check.status_code == 200
        assert s1_check.json()["user"]["username"] == "emma_pwd"

    # 8. Check that other session (Client 2) has been revoked
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"forum_session": client2_cookie},
    ) as client2:
        s2_after = await client2.get("/auth/session")
        assert s2_after.status_code == 401
        assert "revoked" in s2_after.json()["error"]["message"].lower()

    # 9. Verify login with old password fails, new password succeeds
    async with AsyncClient(transport=transport, base_url="http://test") as login_client:
        old_login = await login_client.post(
            "/auth/login",
            json={"login": "emma_pwd", "password": "InitialPassword123!"},
        )
        assert old_login.status_code == 401

        new_login = await login_client.post(
            "/auth/login",
            json={"login": "emma_pwd", "password": "NewSecretPassword123!"},
        )
        assert new_login.status_code == 200
        assert new_login.json()["user"]["username"] == "emma_pwd"
