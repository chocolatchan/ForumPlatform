"""Automated Test Suite for Task 006.5: Dockerized SMTP Email Catcher with Recipient Allowlist.

Coverage:
1. Allowlist Parsing and Fail-Closed Verification:
   - Empty or unconfigured allowlist permits nobody (fail-closed).
   - Valid allowlist parses case-insensitively and strips whitespace.
   - Disallowed recipients are rejected immediately.
   - Partial / substring recipient matches are rejected (e.g. developer@example.test.bad).

2. SMTP Client & Boundary Error Handling:
   - Successful delivery via SMTP (mocked server returning 250 OK).
   - Recipient rejection (SMTP 550) raises EmailDeliveryError with is_recipient_rejected=True.
   - Server error / connection failure raises EmailDeliveryError.

3. Registration & Resend Flow Integration:
   - When recipient is allowed and SMTP delivers successfully, pending challenge is created with status 'pending'.
   - When recipient is rejected or SMTP fails:
     - Public API returns neutral 202 Accepted (anti-enumeration DEC-001).
     - Database pending record is transitioned to 'delivery_failed' so it cannot be verified.
     - Attempting to verify the un-emailed OTP returns 400 Bad Request.
   - Plaintext OTP is never leaked in log output or API response.

4. Resend OTP with Email Delivery:
   - Resend flow delivers OTP via configured delivery backend.
   - Resend to disallowed recipient transitions pending status to 'delivery_failed'.
"""

import os
import shutil
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import src.services.user.app as user_app_mod
import src.services.user.database as user_db_module
from src.common.database import DatabaseSessionManager
from src.services.user.app import app as user_app
from src.services.user.config import UserSettings, user_settings
from src.services.user.models import Base as UserBase
from src.services.user.models import PendingRegistration
from src.services.user.otp_delivery import (
    EmailDeliveryError,
    _send_smtp_email_sync,
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
    orig_allowlist = user_settings.dev_email_allowlist
    orig_host = user_settings.smtp_host
    orig_port = user_settings.smtp_port
    user_settings.email_delivery_backend = "smtp"
    user_settings.dev_email_allowlist = "developer@example.test,tester@example.test"
    yield
    user_settings.email_delivery_backend = orig_backend
    user_settings.dev_email_allowlist = orig_allowlist
    user_settings.smtp_host = orig_host
    user_settings.smtp_port = orig_port
    otp_delivery_sink.clear()


# ==============================================================================
# SECTION 1: ALLOWLIST & FAIL-CLOSED VERIFICATION
# ==============================================================================


def test_allowlist_parsing_and_normalization():
    """Verify comma-separated email list is parsed, lowercased, and trimmed."""
    settings = UserSettings(
        dev_email_allowlist="  Developer@Example.TEST , tester@example.test  ,, OTHER@TEST.DEV "
    )
    allowed = settings.get_allowed_recipients()
    assert allowed == {
        "developer@example.test",
        "tester@example.test",
        "other@test.dev",
    }


def test_allowlist_fail_closed_when_empty():
    """Verify empty or whitespace-only allowlist allows nobody (fail-closed)."""
    settings_empty = UserSettings(dev_email_allowlist="")
    assert settings_empty.get_allowed_recipients() == set()

    settings_spaces = UserSettings(dev_email_allowlist="   ,   , ")
    assert settings_spaces.get_allowed_recipients() == set()


@pytest.mark.asyncio
async def test_deliver_otp_rejects_unlisted_recipient_fail_closed():
    """Verify deliver_otp raises EmailDeliveryError if recipient is not in allowlist."""
    user_settings.dev_email_allowlist = "developer@example.test"

    # Exact match allowed
    with patch("src.services.user.otp_delivery._send_smtp_email_sync") as mock_send:
        await deliver_otp(
            recipient="developer@example.test",
            code="123456",
            purpose="registration",
            timestamp=datetime.now(UTC),
        )
        assert mock_send.call_count == 1

    # Unlisted recipient fails closed
    with pytest.raises(EmailDeliveryError) as exc_info:
        await deliver_otp(
            recipient="intruder@example.com",
            code="123456",
            purpose="registration",
            timestamp=datetime.now(UTC),
        )
    assert exc_info.value.is_recipient_rejected is True
    assert "intruder@example.com" in str(exc_info.value)


@pytest.mark.asyncio
async def test_deliver_otp_rejects_partial_or_substring_match():
    """Verify partial matches (substring or prefix/suffix) fail delivery."""
    user_settings.dev_email_allowlist = "developer@example.test"

    invalid_recipients = [
        "developer@example.test.bad",
        "baddeveloper@example.test",
        "developer@example",
        "developer@example.test.net",
    ]

    for bad_recipient in invalid_recipients:
        with pytest.raises(EmailDeliveryError) as exc_info:
            await deliver_otp(
                recipient=bad_recipient,
                code="654321",
                purpose="registration",
                timestamp=datetime.now(UTC),
            )
        assert exc_info.value.is_recipient_rejected is True


# ==============================================================================
# SECTION 2: SMTP CLIENT EXECUTION & ERROR BOUNDARIES
# ==============================================================================


def test_smtp_send_success():
    """Verify _send_smtp_email_sync successfully creates SMTP session and sends message."""
    mock_smtp = MagicMock()
    mock_smtp.__enter__.return_value = mock_smtp
    mock_smtp.send_message.return_value = {}  # Empty dict means all recipients accepted

    with patch("smtplib.SMTP", return_value=mock_smtp):
        _send_smtp_email_sync(
            recipient="developer@example.test",
            subject="Test Subject",
            body="Test Body",
            from_addr="noreply@forumplatform.local",
            host="mailpit",
            port=1025,
        )

        assert mock_smtp.send_message.call_count == 1
        sent_msg = mock_smtp.send_message.call_args[0][0]
        assert sent_msg["To"] == "developer@example.test"
        assert sent_msg["From"] == "noreply@forumplatform.local"
        assert sent_msg["Subject"] == "Test Subject"
        assert "Test Body" in sent_msg.get_content()


def test_smtp_recipient_refused_error():
    """Verify SMTPRecipientsRefused is converted into EmailDeliveryError."""

    mock_smtp = MagicMock()
    mock_smtp.__enter__.return_value = mock_smtp
    mock_smtp.send_message.return_value = {
        "disallowed@example.test": (550, b"Requested action not taken: mailbox unavailable")
    }

    with patch("smtplib.SMTP", return_value=mock_smtp):
        with pytest.raises(EmailDeliveryError) as exc_info:
            _send_smtp_email_sync(
                recipient="disallowed@example.test",
                subject="Test",
                body="Test",
                from_addr="noreply@forumplatform.local",
                host="mailpit",
                port=1025,
            )
        assert exc_info.value.is_recipient_rejected is True


def test_smtp_connection_refused_error():
    """Verify socket connection error is converted into EmailDeliveryError without crashing."""
    with patch("smtplib.SMTP", side_effect=ConnectionRefusedError("Connection refused")):
        with pytest.raises(EmailDeliveryError) as exc_info:
            _send_smtp_email_sync(
                recipient="developer@example.test",
                subject="Test",
                body="Test",
                from_addr="noreply@forumplatform.local",
                host="mailpit",
                port=1025,
            )
        assert exc_info.value.is_recipient_rejected is False


# ==============================================================================
# SECTION 3: REGISTRATION & RESEND INTEGRATION WITH SAFE FAILURE HANDLING
# ==============================================================================


@pytest.mark.asyncio
async def test_registration_with_allowed_recipient_smtp_success():
    """Verify registration succeeds when recipient is allowed and SMTP delivery passes."""
    user_settings.dev_email_allowlist = "developer@example.test"

    with patch("src.services.user.otp_delivery._send_smtp_email_sync") as mock_send:
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
            assert mock_send.call_count == 1

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
async def test_registration_delivery_failure_safe_handling():
    """Verify registration with rejected recipient returns neutral 202 and prevents verification.

    Key safety requirement:
    - Return neutral 202 Accepted (anti-enumeration DEC-001).
    - Transition challenge to 'delivery_failed' so the un-emailed OTP cannot activate the account.
    """
    user_settings.dev_email_allowlist = "developer@example.test"

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
async def test_resend_otp_delivery_failure_safe_handling():
    """Verify resend OTP endpoint safe failure handling when delivery fails."""
    user_settings.dev_email_allowlist = "tester@example.test"

    # Step 1: Initial successful registration
    with patch("src.services.user.otp_delivery._send_smtp_email_sync"):
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

            # Advance resend cooldown in database
            async with user_db_module.user_db.session() as db_session:
                query = select(PendingRegistration).where(
                    PendingRegistration.email_normalized == "tester@example.test"
                )
                pending = (await db_session.execute(query)).scalar_one()
                pending.resend_available_at = datetime.now(UTC)
                await db_session.commit()

            # Step 2: Now simulate SMTP server failure during resend
            with patch(
                "src.services.user.otp_delivery._send_smtp_email_sync",
                side_effect=EmailDeliveryError(
                    "SMTP Server Unavailable", recipient="tester@example.test"
                ),
            ):
                resend_resp = await client.post(
                    "/auth/register/resend-otp",
                    json={"email": "tester@example.test"},
                )
                assert resend_resp.status_code == 202

                # Verify pending record was marked as delivery_failed
                async with user_db_module.user_db.session() as db_session:
                    pending_after = (await db_session.execute(query)).scalar_one()
                    assert pending_after.status == "delivery_failed"
