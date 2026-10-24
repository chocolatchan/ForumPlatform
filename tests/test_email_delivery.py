"""User Service OTP delivery, routing, and safe-failure tests.

Coverage:
1. Shared-adapter execution and error boundaries.
2. Registration/resend workflows without recipient allowlist authorization.
3. Neutral anti-enumeration responses and non-delivered OTP state on failure.
4. Production suppression maps to User delivery failure, never success.
5. Sensitive OTP/body values never leak into delivery logs.
"""

import logging
import os
import shutil
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import src.services.user.app as user_app_mod
import src.services.user.database as user_db_module
from src.common.database import DatabaseSessionManager
from src.common.email import EmailAdapterError, EmailMessage, EmailSuppressedError
from src.services.user.app import app as user_app
from src.services.user.config import user_settings
from src.services.user.models import Base as UserBase
from src.services.user.models import PendingRegistration
from src.services.user.otp_delivery import (
    EmailDeliveryError,
    deliver_otp,
    otp_delivery_sink,
)

EMAIL_TEST_DB_PATH = "/tmp/test_forum_email_task006_5.db"
EMAIL_TEST_DB_URL = f"sqlite+aiosqlite:///{EMAIL_TEST_DB_PATH}"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def setup_email_test_database():
    """Initialize dedicated SQLite database for Task 006.5 email delivery testing."""
    if os.path.exists(EMAIL_TEST_DB_PATH):
        os.remove(EMAIL_TEST_DB_PATH)
    if os.path.exists("/tmp/sqlite_schemas"):
        shutil.rmtree("/tmp/sqlite_schemas")

    test_db = DatabaseSessionManager(EMAIL_TEST_DB_URL)
    async with test_db.engine.begin() as conn:
        await conn.run_sync(UserBase.metadata.create_all)

    orig_user_db = user_db_module.user_db
    user_db_module.user_db = test_db
    user_app_mod.user_db = test_db

    yield

    await test_db.close()
    user_db_module.user_db = orig_user_db
    user_app_mod.user_db = orig_user_db
    if os.path.exists(EMAIL_TEST_DB_PATH):
        os.remove(EMAIL_TEST_DB_PATH)


@pytest.fixture(autouse=True)
def reset_delivery_environment():
    """Reset configuration and mailbox before and after each test."""
    otp_delivery_sink.clear()
    orig_backend = user_settings.email_delivery_backend
    orig_host = user_settings.smtp_host
    orig_port = user_settings.smtp_port
    user_settings.email_delivery_backend = "smtp"
    yield
    user_settings.email_delivery_backend = orig_backend
    user_settings.smtp_host = orig_host
    user_settings.smtp_port = orig_port
    otp_delivery_sink.clear()


@pytest.fixture
def mocked_user_adapter():
    """Patch the shared-adapter factory and expose a mocked async adapter."""
    adapter = MagicMock()
    adapter.send = AsyncMock()
    with patch(
        "src.services.user.otp_delivery.get_user_email_adapter",
        return_value=adapter,
    ) as factory:
        yield factory, adapter


# ==============================================================================
# SECTION 1: SHARED ADAPTER EXECUTION & ERROR BOUNDARIES
# ==============================================================================


@pytest.mark.asyncio
async def test_deliver_otp_sends_external_recipient_without_allowlist(mocked_user_adapter):
    """Every recipient is accepted; environment routing belongs to the adapter."""
    factory, adapter = mocked_user_adapter

    for recipient in ("intruder@example.com", "user@test.dev", "user@not-test.dev"):
        await deliver_otp(
            recipient=recipient,
            code="123456",
            purpose="registration",
            timestamp=datetime.now(UTC),
        )

    assert factory.call_count == 3
    assert adapter.send.await_count == 3
    sent_recipients = [call.args[0].recipients for call in adapter.send.await_args_list]
    assert sent_recipients == [
        ["intruder@example.com"],
        ["user@test.dev"],
        ["user@not-test.dev"],
    ]
    assert otp_delivery_sink.get_latest_otp("intruder@example.com") == "123456"


# ==============================================================================
# SECTION 2: SMTP CLIENT EXECUTION & ERROR BOUNDARIES
# ==============================================================================


@pytest.mark.asyncio
async def test_deliver_otp_uses_shared_adapter_once(mocked_user_adapter):
    """Verify OTP delivery uses the shared adapter exactly once with unchanged content."""
    factory, adapter = mocked_user_adapter

    await deliver_otp(
        recipient="Developer@Example.TEST",
        code="123456",
        purpose="registration",
        timestamp=datetime.now(UTC),
    )

    assert factory.call_count == 1
    assert adapter.send.await_count == 1
    sent_message = adapter.send.await_args.args[0]
    assert isinstance(sent_message, EmailMessage)
    assert sent_message.recipients == ["developer@example.test"]
    assert sent_message.sender == user_settings.email_from
    assert sent_message.subject == "Your Forum Verification Code [Registration]"
    assert "123456" in sent_message.body_text
    assert otp_delivery_sink.get_latest_otp("developer@example.test") == "123456"


@pytest.mark.asyncio
async def test_deliver_otp_maps_adapter_timeout_without_sink_write(mocked_user_adapter):
    """Adapter timeout must surface as EmailDeliveryError and not look delivered."""
    _, adapter = mocked_user_adapter
    adapter.send.side_effect = EmailAdapterError(
        "SMTP connection failed: TimeoutError",
        recipient="developer@example.test",
        is_recipient_rejected=False,
    )

    with pytest.raises(EmailDeliveryError) as exc_info:
        await deliver_otp(
            recipient="developer@example.test",
            code="654321",
            purpose="recovery",
            timestamp=datetime.now(UTC),
        )

    assert exc_info.value.is_recipient_rejected is False
    assert "TimeoutError" in str(exc_info.value)
    assert otp_delivery_sink.get_latest_otp("developer@example.test") is None


@pytest.mark.asyncio
async def test_deliver_otp_maps_adapter_recipient_rejection(mocked_user_adapter):
    """Adapter recipient rejection must preserve the rejected flag."""
    _, adapter = mocked_user_adapter
    adapter.send.side_effect = EmailAdapterError(
        "Recipient refused by SMTP server",
        recipient="developer@example.test",
        is_recipient_rejected=True,
    )

    with pytest.raises(EmailDeliveryError) as exc_info:
        await deliver_otp(
            recipient="developer@example.test",
            code="111111",
            purpose="email_change",
            timestamp=datetime.now(UTC),
        )

    assert exc_info.value.is_recipient_rejected is True
    assert otp_delivery_sink.get_latest_otp("developer@example.test") is None


@pytest.mark.asyncio
async def test_deliver_otp_maps_production_suppression_without_sink_write(mocked_user_adapter):
    """A suppressed production recipient must never be recorded as delivered."""
    _, adapter = mocked_user_adapter
    adapter.send.side_effect = EmailSuppressedError(
        recipients=("user@test.dev",),
        domains=("test.dev",),
    )

    with pytest.raises(EmailDeliveryError) as exc_info:
        await deliver_otp(
            recipient="user@test.dev",
            code="222222",
            purpose="registration",
            timestamp=datetime.now(UTC),
        )

    assert exc_info.value.is_suppressed is True
    assert exc_info.value.is_recipient_rejected is False
    assert otp_delivery_sink.get_latest_otp("user@test.dev") is None


@pytest.mark.asyncio
async def test_deliver_otp_failure_does_not_log_otp_or_body(mocked_user_adapter, caplog):
    """Sensitive OTP and message body must not appear in delivery logs."""
    _, adapter = mocked_user_adapter
    adapter.send.side_effect = EmailAdapterError(
        "SMTP connection failed: TimeoutError",
        recipient="developer@example.test",
    )
    caplog.set_level(logging.DEBUG)

    with pytest.raises(EmailDeliveryError):
        await deliver_otp(
            recipient="developer@example.test",
            code="987654",
            purpose="registration",
            timestamp=datetime.now(UTC),
        )

    assert "987654" not in caplog.text
    assert "Your verification code is:" not in caplog.text


# ==============================================================================
# SECTION 3: REGISTRATION & RESEND INTEGRATION WITH SAFE FAILURE HANDLING
# ==============================================================================


@pytest.mark.asyncio
async def test_registration_with_allowed_recipient_smtp_success(mocked_user_adapter):
    """Verify registration succeeds when recipient is allowed and adapter delivery passes."""
    _, adapter = mocked_user_adapter

    transport = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/auth/register",
            json={
                "username": "smtp_dev_user",
                "email": "developer@example.test",
                "password": "Password123!",
            },
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "pending_verification"
        assert adapter.send.await_count == 1

        # Verify challenge is in 'pending' status in database
        async with user_db_module.user_db.session() as db_session:
            query = select(PendingRegistration).where(
                PendingRegistration.email_normalized == "developer@example.test"
            )
            pending = (await db_session.execute(query)).scalar_one_or_none()
            assert pending is not None
            assert pending.status == "pending"

        # Verify account can now be verified
        otp = otp_delivery_sink.get_latest_otp("developer@example.test")
        assert otp is not None
        v_res = await client.post(
            "/auth/register/verify",
            json={"email": "developer@example.test", "otp": otp},
        )
        assert v_res.status_code == 201


@pytest.mark.asyncio
async def test_registration_delivery_failure_safe_handling(mocked_user_adapter):
    """Verify an adapter failure returns neutral 202 and prevents verification.

    Key safety requirement:
    - Return neutral 202 Accepted (anti-enumeration DEC-001).
    - Transition challenge to 'delivery_failed' so the un-emailed OTP cannot activate the account.
    """
    _, adapter = mocked_user_adapter
    adapter.send.side_effect = EmailAdapterError(
        "SMTP connection failed: TimeoutError",
        recipient="unlisted@example.com",
        is_recipient_rejected=False,
    )

    transport = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register with unlisted recipient
        resp = await client.post(
            "/auth/register",
            json={
                "username": "unlisted_user",
                "email": "unlisted@example.com",
                "password": "Password123!",
            },
        )
        # Neutral response must still be 202 Accepted
        assert resp.status_code == 202
        assert resp.json()["status"] == "pending_verification"

        # Check database: challenge status must NOT be 'pending'
        async with user_db_module.user_db.session() as db_session:
            query = select(PendingRegistration).where(
                PendingRegistration.email_normalized == "unlisted@example.com"
            )
            pending = (await db_session.execute(query)).scalar_one_or_none()
            assert pending is not None
            assert pending.status == "delivery_failed"

        # Attempting verification must fail with 400 Bad Request
        v_res = await client.post(
            "/auth/register/verify",
            json={"email": "unlisted@example.com", "otp": "123456"},
        )
        assert v_res.status_code == 400
        assert v_res.json()["error"]["code"] == "INVALID_OTP"


@pytest.mark.asyncio
async def test_resend_otp_delivery_failure_safe_handling(mocked_user_adapter):
    """Verify resend OTP endpoint safe failure handling when delivery fails."""
    _, adapter = mocked_user_adapter

    # Step 1: Initial successful registration
    transport = ASGITransport(app=user_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        reg_resp = await client.post(
            "/auth/register",
            json={
                "username": "resend_tester",
                "email": "tester@example.test",
                "password": "Password123!",
            },
        )
        assert reg_resp.status_code == 202
        assert adapter.send.await_count == 1

        # Advance resend cooldown in database
        async with user_db_module.user_db.session() as db_session:
            query = select(PendingRegistration).where(
                PendingRegistration.email_normalized == "tester@example.test"
            )
            pending = (await db_session.execute(query)).scalar_one()
            pending.resend_available_at = datetime.now(UTC)
            await db_session.commit()

        # Step 2: Now simulate SMTP server failure during resend
        adapter.send.side_effect = EmailAdapterError(
            "SMTP connection failed: TimeoutError",
            recipient="tester@example.test",
            is_recipient_rejected=False,
        )
        resend_resp = await client.post(
            "/auth/register/resend-otp",
            json={"email": "tester@example.test"},
        )
        assert resend_resp.status_code == 202
        assert adapter.send.await_count == 2

        # Verify pending record was marked as delivery_failed
        async with user_db_module.user_db.session() as db_session:
            pending_after = (await db_session.execute(query)).scalar_one()
            assert pending_after.status == "delivery_failed"
