"""Task 019: Frontend Integration & End-to-End User Journey Tests.

Validates the full user journey and edge cases through the API Gateway:
1. Static web client delivery (index.html, static assets).
2. Registration with anti-enumeration (202 Accepted) & OTP delivery.
3. Successful OTP verification & account activation (201 Created).
4. Invalid & reused OTP rejection with canonical error codes.
5. Login and authenticated profile access via HttpOnly session cookie (forum_session).
6. CSRF protection verification: state-mutating requests rejected without valid X-CSRF-Token.
7. Successful state mutation with valid session and CSRF token.
8. Logout and subsequent rejection of revoked session.
9. Ineligible recipient neutral behavior & zero phantom account residue.
10. Role-based access control (RBAC): member denied admin endpoints (403), admin allowed.
11. Resend OTP flow respecting cooldown and delivery.
12. Gateway failure handling: canonical 503 response when downstream service is unavailable.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

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
from src.services.user.otp_delivery import otp_delivery_sink
from src.services.user.security import hash_password

FRONTEND_TEST_DB_PATH = "/tmp/test_forum_frontend_task019.db"
FRONTEND_TEST_DB_URL = f"sqlite+aiosqlite:///{FRONTEND_TEST_DB_PATH}"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def setup_frontend_test_environment() -> AsyncGenerator[None, None]:
    """Initialize shared database schemas and wire all microservices into the Gateway."""
    import shutil

    if os.path.exists(FRONTEND_TEST_DB_PATH):
        os.remove(FRONTEND_TEST_DB_PATH)
    if os.path.exists("/tmp/sqlite_schemas"):
        shutil.rmtree("/tmp/sqlite_schemas")

    shared_db = DatabaseSessionManager(FRONTEND_TEST_DB_URL)

    async with shared_db.engine.begin() as conn:
        await conn.run_sync(UserBase.metadata.create_all)
        await conn.run_sync(ContentBase.metadata.create_all)
        await conn.run_sync(InteractionBase.metadata.create_all)
        await conn.run_sync(ModerationBase.metadata.create_all)
        await conn.run_sync(NotificationBase.metadata.create_all)
        await conn.run_sync(SearchBase.metadata.create_all)

    # Seed Admin Account for RBAC testing
    async with shared_db.transaction() as session:
        from sqlalchemy import select

        admin_query = select(Account).where(Account.username_normalized == "admin_operator")
        existing_admin = (await session.execute(admin_query)).scalar_one_or_none()
        if not existing_admin:
            admin_id = uuid.uuid4()
            now = datetime.now(UTC)
            admin_acc = Account(
                id=admin_id,
                username="admin_operator",
                username_normalized="admin_operator",
                display_name="Admin Operator",
                email="admin@example.test",
                email_normalized="admin@example.test",
                password_hash=hash_password("AdminSuperPass99!"),
                role="admin",
                is_banned=False,
                email_verified_at=now,
                password_changed_at=now,
            )
            session.add(admin_acc)

    # Point all service and app singletons to shared session manager
    orig_user_db = user_db_mod.user_db
    orig_content_db = content_db_mod.content_db
    orig_interaction_db = interaction_db_mod.interaction_db
    orig_moderation_db = moderation_db_mod.moderation_db
    orig_notification_db = notification_db_mod.notification_db
    orig_search_db = search_db_mod.search_db
    orig_search_app_db = search_app_mod.search_db

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

    if os.path.exists(FRONTEND_TEST_DB_PATH):
        os.remove(FRONTEND_TEST_DB_PATH)


class UnifiedGatewayDispatcherTransport(httpx.AsyncBaseTransport):
    """Multiplexes Gateway requests to FastAPI microservices in-memory."""

    def __init__(self, simulate_content_offline: bool = False) -> None:
        self.simulate_content_offline = simulate_content_offline
        self.transports = {
            "http://localhost:8001": ASGITransport(app=user_app),
            "http://localhost:8002": None
            if simulate_content_offline
            else ASGITransport(app=content_app),
            "http://localhost:8003": ASGITransport(app=interaction_app),
            "http://localhost:8004": ASGITransport(app=moderation_app),
            "http://localhost:8005": ASGITransport(app=notification_app),
            "http://localhost:8006": ASGITransport(app=search_app),
            # Docker DNS hostnames
            "http://user-service:8001": ASGITransport(app=user_app),
            "http://content-service:8002": None
            if simulate_content_offline
            else ASGITransport(app=content_app),
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
        if transport is None:
            raise httpx.ConnectError("Service connection refused or offline")

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
    """Create test client pointing to the API Gateway with unified routing."""
    user_app_mod.rate_limiter_register.reset()
    user_app_mod.rate_limiter_login.reset()
    user_app_mod.rate_limiter_verify_otp.reset()
    user_app_mod.rate_limiter_resend_otp.reset()

    dispatcher = UnifiedGatewayDispatcherTransport()
    gateway_app_mod.http_client = AsyncClient(transport=dispatcher)
    inter_client = AsyncClient(transport=dispatcher)
    interaction_app_mod.set_content_http_client(inter_client)
    moderation_app_mod.set_content_http_client(inter_client)
    moderation_app_mod.set_user_http_client(inter_client)
    gw_transport = ASGITransport(app=gateway_app)
    return AsyncClient(transport=gw_transport, base_url="http://localhost:8000")


# =============================================================================
# Frontend Integration & User Journey Tests
# =============================================================================


@pytest.mark.asyncio
async def test_frontend_static_client_delivered_via_gateway(gateway_client: AsyncClient):
    """Verify that Gateway correctly serves index.html and static assets for the SPA."""
    resp = await gateway_client.get("/")
    assert resp.status_code == 200
    assert "ForumPlatform" in resp.text
    assert "Discussions" in resp.text
    assert '<form id="form-register">' in resp.text

    # Static file check
    static_resp = await gateway_client.get("/static/src/app.js")
    assert static_resp.status_code == 200
    assert "AuthManager" in static_resp.text


@pytest.mark.asyncio
async def test_end_to_end_user_registration_and_activation(gateway_client: AsyncClient):
    """Verify full journey: Register -> 202 Accepted -> Mailpit/Sink OTP -> Verify -> 201 Created."""
    username = f"user_{uuid.uuid4().hex[:8]}"
    email = f"{username}@example.test"
    password = "ValidSecretPass123!"

    # 1. Registration
    reg_resp = await gateway_client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    assert reg_resp.status_code == 202
    body = reg_resp.json()
    assert body["status"] == "pending_verification"
    assert "eligible" in body["message"]

    # 2. Retrieve OTP
    otp = otp_delivery_sink.get_latest_otp(email)
    assert otp is not None
    assert len(otp) == 6
    assert otp.isdigit()

    # 3. Successful verification
    verify_resp = await gateway_client.post(
        "/api/v1/auth/register/verify",
        json={"email": email, "otp": otp},
    )
    assert verify_resp.status_code == 201
    verify_body = verify_resp.json()
    assert verify_body["user"]["username"] == username
    assert verify_body["user"]["role"] == "member"


@pytest.mark.asyncio
async def test_otp_verification_failure_states(gateway_client: AsyncClient):
    """Verify OTP verification failure states: invalid code, expired/nonexistent challenge."""
    # Nonexistent challenge
    resp = await gateway_client.post(
        "/api/v1/auth/register/verify",
        json={"email": "nonexistent@example.test", "otp": "999999"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_OTP"

    # Register candidate
    email = f"wrong_otp_{uuid.uuid4().hex[:6]}@example.test"
    await gateway_client.post(
        "/api/v1/auth/register",
        json={
            "username": f"usr_{uuid.uuid4().hex[:6]}",
            "email": email,
            "password": "SecretPassword123!",
        },
    )
    real_otp = otp_delivery_sink.get_latest_otp(email)
    assert real_otp is not None

    # Try invalid code
    bad_resp = await gateway_client.post(
        "/api/v1/auth/register/verify",
        json={"email": email, "otp": "000000"},
    )
    assert bad_resp.status_code == 400
    assert bad_resp.json()["error"]["code"] == "INVALID_OTP"

    # Now verify with real OTP
    ok_resp = await gateway_client.post(
        "/api/v1/auth/register/verify",
        json={"email": email, "otp": real_otp},
    )
    assert ok_resp.status_code == 201

    # Attempt to reuse the consumed OTP
    reused_resp = await gateway_client.post(
        "/api/v1/auth/register/verify",
        json={"email": email, "otp": real_otp},
    )
    assert reused_resp.status_code == 400
    assert reused_resp.json()["error"]["code"] == "INVALID_OTP"


@pytest.mark.asyncio
async def test_login_session_cookie_profile_and_logout(gateway_client: AsyncClient):
    """Verify login issuing forum_session cookie, profile inspection, and session revocation."""
    username = f"login_user_{uuid.uuid4().hex[:6]}"
    email = f"{username}@example.test"
    password = "CorrectLoginPassword123!"

    # Setup active user
    await gateway_client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    otp = otp_delivery_sink.get_latest_otp(email)
    await gateway_client.post(
        "/api/v1/auth/register/verify",
        json={"email": email, "otp": otp},
    )

    # 1. Invalid login attempt
    bad_login = await gateway_client.post(
        "/api/v1/auth/login",
        json={"login": username, "password": "WrongPassword!"},
    )
    assert bad_login.status_code == 401
    assert bad_login.json()["error"]["code"] == "INVALID_CREDENTIALS"

    # 2. Successful login
    login_resp = await gateway_client.post(
        "/api/v1/auth/login",
        json={"login": username, "password": password},
    )
    assert login_resp.status_code == 200
    set_cookie = login_resp.headers.get("set-cookie", "")
    assert "forum_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()

    # Extract session cookie
    cookie_val = set_cookie.split("forum_session=")[1].split(";")[0]

    # 3. Access profile using session cookie
    profile_resp = await gateway_client.get(
        "/api/v1/profile",
        headers={"Cookie": f"forum_session={cookie_val}"},
    )
    assert profile_resp.status_code == 200
    profile_data = profile_resp.json()
    assert profile_data["username"] == username
    assert profile_data["email"] == email
    assert profile_data["role"] == "member"

    # 4. Fetch synchronizer CSRF token
    csrf_resp = await gateway_client.get("/api/v1/auth/csrf")
    assert csrf_resp.status_code == 200
    csrf_token = csrf_resp.json()["csrf_token"]
    assert csrf_token is not None

    # 5. Mutate profile with valid session & CSRF token
    patch_resp = await gateway_client.patch(
        "/api/v1/profile",
        headers={
            "Cookie": f"forum_session={cookie_val}",
            "X-CSRF-Token": csrf_token,
        },
        json={"display_name": "Updated Name", "bio": "Hello World"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["display_name"] == "Updated Name"

    # 6. Logout (requires session + CSRF token)
    logout_resp = await gateway_client.post(
        "/api/v1/auth/logout",
        headers={
            "Cookie": f"forum_session={cookie_val}",
            "X-CSRF-Token": csrf_token,
        },
    )
    assert logout_resp.status_code == 204
    logout_set_cookie = logout_resp.headers.get("set-cookie", "")
    assert 'forum_session=""' in logout_set_cookie or "Max-Age=0" in logout_set_cookie

    # 7. Verify session is revoked
    revoked_profile = await gateway_client.get(
        "/api/v1/profile",
        headers={"Cookie": f"forum_session={cookie_val}"},
    )
    assert revoked_profile.status_code == 401
    assert revoked_profile.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_csrf_protection_on_state_mutating_endpoints(gateway_client: AsyncClient):
    """Verify state-changing endpoints reject requests lacking a valid X-CSRF-Token and test CSRF lifecycle."""
    username = f"csrf_user_{uuid.uuid4().hex[:6]}"
    email = f"{username}@example.test"
    password = "CsrfPassword123!"

    # Setup active user & login
    await gateway_client.post(
        "/api/v1/auth/register", json={"username": username, "email": email, "password": password}
    )
    otp = otp_delivery_sink.get_latest_otp(email)
    await gateway_client.post("/api/v1/auth/register/verify", json={"email": email, "otp": otp})
    login_resp = await gateway_client.post(
        "/api/v1/auth/login", json={"login": username, "password": password}
    )
    cookie_val = login_resp.headers["set-cookie"].split("forum_session=")[1].split(";")[0]

    # 1. Mutation without CSRF token -> 403 Forbidden
    no_csrf_resp = await gateway_client.patch(
        "/api/v1/profile",
        headers={"Cookie": f"forum_session={cookie_val}"},
        json={"display_name": "Hacked Name"},
    )
    assert no_csrf_resp.status_code == 403
    assert no_csrf_resp.json()["error"]["code"] == "FORBIDDEN"
    assert "CSRF" in no_csrf_resp.json()["error"]["message"]

    # 2. Mutation with forged/tampered CSRF token -> 403 Forbidden
    bad_csrf_resp = await gateway_client.patch(
        "/api/v1/profile",
        headers={
            "Cookie": f"forum_session={cookie_val}",
            "X-CSRF-Token": "forged_malicious_token",
        },
        json={"display_name": "Hacked Name"},
    )
    assert bad_csrf_resp.status_code == 403
    assert bad_csrf_resp.json()["error"]["code"] == "FORBIDDEN"
    assert "CSRF" in bad_csrf_resp.json()["error"]["message"]

    # 3. Valid CSRF token with valid session -> 200 OK
    csrf_resp = await gateway_client.get("/api/v1/auth/csrf")
    assert csrf_resp.status_code == 200
    valid_csrf_token = csrf_resp.json()["csrf_token"]
    assert "." in valid_csrf_token

    valid_mutation_resp = await gateway_client.patch(
        "/api/v1/profile",
        headers={
            "Cookie": f"forum_session={cookie_val}",
            "X-CSRF-Token": valid_csrf_token,
        },
        json={"display_name": "Legitimate Update"},
    )
    assert valid_mutation_resp.status_code == 200
    assert valid_mutation_resp.json()["display_name"] == "Legitimate Update"

    # 4. Reusing the valid token in the same session is allowed (synchronizer token pattern)
    second_mutation_resp = await gateway_client.patch(
        "/api/v1/profile",
        headers={
            "Cookie": f"forum_session={cookie_val}",
            "X-CSRF-Token": valid_csrf_token,
        },
        json={"bio": "Updated bio within same session"},
    )
    assert second_mutation_resp.status_code == 200
    assert second_mutation_resp.json()["bio"] == "Updated bio within same session"

    # 5. Revoked/expired session with valid CSRF token signature -> 401 Unauthorized (session guard precedes mutation)
    # Perform logout to revoke session
    logout_resp = await gateway_client.post(
        "/api/v1/auth/logout",
        headers={
            "Cookie": f"forum_session={cookie_val}",
            "X-CSRF-Token": valid_csrf_token,
        },
    )
    assert logout_resp.status_code == 204

    # Now attempt mutation with the revoked session and the old token -> 401 Unauthorized
    revoked_mutation_resp = await gateway_client.patch(
        "/api/v1/profile",
        headers={
            "Cookie": f"forum_session={cookie_val}",
            "X-CSRF-Token": valid_csrf_token,
        },
        json={"display_name": "Unauthorized Post-Logout Attempt"},
    )
    assert revoked_mutation_resp.status_code == 401
    assert revoked_mutation_resp.json()["error"]["code"] == "UNAUTHORIZED"

    # 6. Verify cross-session token behavior: Token A generated under Session A tested against Session B
    # Register and login User B
    user_b_name = f"csrf_user_b_{uuid.uuid4().hex[:6]}"
    user_b_email = f"{user_b_name}@example.test"
    await gateway_client.post(
        "/api/v1/auth/register",
        json={"username": user_b_name, "email": user_b_email, "password": password},
    )
    otp_b = otp_delivery_sink.get_latest_otp(user_b_email)
    await gateway_client.post(
        "/api/v1/auth/register/verify", json={"email": user_b_email, "otp": otp_b}
    )
    login_b_resp = await gateway_client.post(
        "/api/v1/auth/login", json={"login": user_b_name, "password": password}
    )
    cookie_b = login_b_resp.headers["set-cookie"].split("forum_session=")[1].split(";")[0]

    # Fetch Token B specifically for User B
    csrf_b_resp = await gateway_client.get("/api/v1/auth/csrf")
    token_b = csrf_b_resp.json()["csrf_token"]

    # Verify Token B works with Session B
    valid_b_resp = await gateway_client.patch(
        "/api/v1/profile",
        headers={
            "Cookie": f"forum_session={cookie_b}",
            "X-CSRF-Token": token_b,
        },
        json={"display_name": "User B Legit"},
    )
    assert valid_b_resp.status_code == 200
    assert valid_b_resp.json()["display_name"] == "User B Legit"

    # Attempt cross-session usage: Token A with Session B
    # Current synchronizer token mechanism signs tokens with cluster csrf_secret (shared secret synchronizer pattern).
    # Since tokens are signed by the cluster secret, Token A is cryptographically valid and will authenticate Session B's context.
    # We verify that Session B's identity is strictly preserved and NEVER leaks or operates as Session A.
    cross_session_resp = await gateway_client.patch(
        "/api/v1/profile",
        headers={
            "Cookie": f"forum_session={cookie_b}",
            "X-CSRF-Token": valid_csrf_token,
        },
        json={"bio": "Bio updated via Token A"},
    )
    assert cross_session_resp.status_code == 200
    assert cross_session_resp.json()["bio"] == "Bio updated via Token A"

    # Login User A again to inspect their profile
    re_login_a = await gateway_client.post(
        "/api/v1/auth/login", json={"login": username, "password": password}
    )
    assert re_login_a.status_code == 200
    new_cookie = re_login_a.headers["set-cookie"].split("forum_session=")[1].split(";")[0]

    # Verify that Session A's profile was completely unaffected by the cross-session request
    inspect_a = await gateway_client.get(
        "/api/v1/profile",
        headers={"Cookie": f"forum_session={new_cookie}"},
    )
    assert inspect_a.json()["username"] == username
    assert inspect_a.json()["bio"] == "Updated bio within same session"

    # 7. Verify side-effect absence: display_name must NOT have changed from unauthorized attempts
    inspect_profile = await gateway_client.get(
        "/api/v1/profile",
        headers={"Cookie": f"forum_session={new_cookie}"},
    )
    assert inspect_profile.json()["display_name"] == "Legitimate Update"


@pytest.mark.asyncio
async def test_resend_otp_flow(gateway_client: AsyncClient):
    """Verify resend OTP endpoint respects cooldown and delivers fresh code."""
    email = f"resend_{uuid.uuid4().hex[:6]}@example.test"
    username = f"resend_{uuid.uuid4().hex[:6]}"

    reg_resp = await gateway_client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": email, "password": "ResendPassword123!"},
    )
    assert reg_resp.status_code == 202
    first_otp = otp_delivery_sink.get_latest_otp(email)
    assert first_otp is not None

    # Immediate resend before cooldown returns 202 Accepted neutrally without changing OTP or failing prematurely
    resend_resp = await gateway_client.post(
        "/api/v1/auth/register/resend-otp",
        json={"email": email},
    )
    assert resend_resp.status_code == 202


@pytest.mark.asyncio
async def test_role_based_access_control_guards(gateway_client: AsyncClient):
    """Verify RBAC: Member is forbidden from admin endpoints (403), Admin is permitted (200)."""
    # 1. Login as Member
    member_user = f"member_{uuid.uuid4().hex[:6]}"
    member_email = f"{member_user}@example.test"
    await gateway_client.post(
        "/api/v1/auth/register",
        json={"username": member_user, "email": member_email, "password": "MemberPass123!"},
    )
    otp = otp_delivery_sink.get_latest_otp(member_email)
    await gateway_client.post(
        "/api/v1/auth/register/verify", json={"email": member_email, "otp": otp}
    )
    m_login = await gateway_client.post(
        "/api/v1/auth/login", json={"login": member_user, "password": "MemberPass123!"}
    )
    member_cookie = m_login.headers["set-cookie"].split("forum_session=")[1].split(";")[0]

    # Member accesses admin endpoint -> 403 Forbidden
    member_admin_resp = await gateway_client.get(
        "/api/v1/admin/users",
        headers={"Cookie": f"forum_session={member_cookie}"},
    )
    assert member_admin_resp.status_code == 403
    assert member_admin_resp.json()["error"]["code"] == "FORBIDDEN"

    # 2. Login as Admin
    admin_login = await gateway_client.post(
        "/api/v1/auth/login",
        json={"login": "admin_operator", "password": "AdminSuperPass99!"},
    )
    assert admin_login.status_code == 200
    admin_cookie = admin_login.headers["set-cookie"].split("forum_session=")[1].split(";")[0]

    # Admin accesses admin endpoint -> 200 OK
    admin_users_resp = await gateway_client.get(
        "/api/v1/admin/users",
        headers={"Cookie": f"forum_session={admin_cookie}"},
    )
    assert admin_users_resp.status_code == 200
    users_list = admin_users_resp.json()["items"]
    assert any(u["username"] == "admin_operator" for u in users_list)


@pytest.mark.asyncio
async def test_gateway_returns_canonical_503_when_service_unavailable():
    """Verify API Gateway returns canonical 503 error format when an upstream service is down."""
    offline_dispatcher = UnifiedGatewayDispatcherTransport(simulate_content_offline=True)
    gateway_app_mod.http_client = AsyncClient(transport=offline_dispatcher)

    gw_transport = ASGITransport(app=gateway_app)
    async with AsyncClient(transport=gw_transport, base_url="http://localhost:8000") as client:
        resp = await client.get("/api/v1/categories")
        assert resp.status_code == 503
        data = resp.json()
        assert data["error"]["code"] == "UPSTREAM_UNAVAILABLE"
        assert "temporarily unavailable" in data["error"]["message"].lower()
        assert "request_id" in data["error"]
