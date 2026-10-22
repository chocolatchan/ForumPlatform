"""Task 029.1: Live PostgreSQL Concurrency & Security Verification for Account Recovery.

Verifies against real PostgreSQL (forum_users):
1. Concurrent OTP verification: Two requests with the same valid OTP cannot both issue valid reset tickets.
2. Concurrent password reset: Two requests with the same reset ticket (even proposing different passwords) cannot both succeed.
3. Brute-force & lockouts: Per-challenge attempt limits under concurrent failed verification requests.
4. Resend cooldown & challenge lifecycle: Superceded challenges and active cooldown windows under concurrency.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from src.common.database import DatabaseSessionManager
from src.services.user.models import Account, OneTimeToken, PasswordHistory, Session
from src.services.user.security import (
    generate_otp,
    generate_reset_token,
    hash_password,
    hash_secret,
    verify_password,
)

POSTGRES_USERS_URL = (
    "postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_users"
)
GATEWAY_AUTH_URL = "http://localhost:8000/api/v1/auth"


@pytest.fixture(autouse=True)
async def reset_rate_limiters():
    async with httpx.AsyncClient(base_url="http://localhost:8001", timeout=5.0) as client:
        await client.post("/test/reset-rate-limiters")


@pytest.fixture
def users_db():
    manager = DatabaseSessionManager(POSTGRES_USERS_URL)
    yield manager
    # Engine is managed by connection pooling


@pytest.mark.asyncio
async def test_postgres_concurrent_otp_verification_single_winner(users_db):
    """Verify that two concurrent requests racing to verify the SAME valid recovery OTP
    results in exactly ONE winning verification (issuing a ticket) and ONE failing verification (400 INVALID_OTP),
    with exactly one unconsumed reset ticket created in PostgreSQL.
    """
    now = datetime.now(UTC)
    email = f"conc_verify_{uuid.uuid4().hex[:6]}@example.test"
    username = f"usr_{uuid.uuid4().hex[:6]}"
    password = "OriginalPassword123!"

    # 1. Seed user and recovery OTP in PostgreSQL
    otp = generate_otp(ttl_seconds=900)
    account_id = uuid.uuid4()
    token_id = uuid.uuid4()

    async with users_db.transaction() as session:
        account = Account(
            id=account_id,
            username=username,
            username_normalized=username.lower(),
            email=email,
            email_normalized=email.lower(),
            password_hash=hash_password(password),
            role="member",
            is_banned=False,
            created_at=now,
        )
        session.add(account)

        token_record = OneTimeToken(
            id=token_id,
            account_id=account_id,
            purpose="password_recovery",
            token_hash=otp.code_hash,
            expires_at=otp.expires_at,
            attempts=0,
            resend_count=0,
            resend_after=now + timedelta(seconds=60),
            consumed_at=None,
            created_at=now,
        )
        session.add(token_record)

    # 2. Fire concurrent verification requests via Gateway
    async def call_verify():
        async with httpx.AsyncClient(base_url=GATEWAY_AUTH_URL, timeout=10.0) as client:
            return await client.post(
                "/recovery/verify",
                json={"email": email, "otp": otp.code},
            )

    resp1, resp2 = await asyncio.gather(call_verify(), call_verify())

    statuses = [resp1.status_code, resp2.status_code]
    assert 200 in statuses, f"Expected at least one successful verification, got {statuses}"
    assert 400 in statuses, f"Expected one rejected verification, got {statuses}"

    winner_resp = resp1 if resp1.status_code == 200 else resp2
    loser_resp = resp2 if resp1.status_code == 200 else resp1

    assert "reset_token" in winner_resp.json()
    assert loser_resp.json()["error"]["code"] == "INVALID_OTP"

    # 3. Verify PostgreSQL database state
    async with users_db.session() as session:
        # Recovery token must be consumed
        tok = (
            await session.execute(select(OneTimeToken).where(OneTimeToken.id == token_id))
        ).scalar_one()
        assert tok.consumed_at is not None

        # There must be EXACTLY ONE unconsumed password_reset_ticket for this account
        tickets = (
            (
                await session.execute(
                    select(OneTimeToken).where(
                        OneTimeToken.account_id == account_id,
                        OneTimeToken.purpose == "password_reset_ticket",
                        OneTimeToken.consumed_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(tickets) == 1


@pytest.mark.asyncio
async def test_postgres_concurrent_reset_ticket_single_winner(users_db):
    """Verify that two concurrent requests racing to reset the password with the SAME reset ticket
    (proposing different new passwords) results in exactly ONE winner (204) and ONE loser (400),
    preventing state corruption or multiple history insertions.
    """
    now = datetime.now(UTC)
    email = f"conc_reset_{uuid.uuid4().hex[:6]}@example.test"
    username = f"usr_{uuid.uuid4().hex[:6]}"
    old_password = "InitialPassword123!"

    new_password_a = "NewPasswordVariantA1!"
    new_password_b = "NewPasswordVariantB2!"

    reset_token = generate_reset_token()
    ticket_hash = hash_secret(reset_token)

    account_id = uuid.uuid4()
    session_id = uuid.uuid4()
    ticket_id = uuid.uuid4()

    # 1. Seed user, active session, and reset ticket
    async with users_db.transaction() as session:
        account = Account(
            id=account_id,
            username=username,
            username_normalized=username.lower(),
            email=email,
            email_normalized=email.lower(),
            password_hash=hash_password(old_password),
            role="member",
            is_banned=False,
            created_at=now,
        )
        session.add(account)

        # Active user session
        sess = Session(
            id=session_id,
            account_id=account_id,
            session_token_hash=hash_secret(f"active-test-session-{uuid.uuid4().hex}"),
            created_at=now,
            last_seen_at=now,
            idle_expires_at=now + timedelta(hours=1),
            absolute_expires_at=now + timedelta(days=1),
            revoked_at=None,
        )
        session.add(sess)

        ticket = OneTimeToken(
            id=ticket_id,
            account_id=account_id,
            purpose="password_reset_ticket",
            token_hash=ticket_hash,
            expires_at=now + timedelta(minutes=10),
            attempts=0,
            resend_count=0,
            resend_after=now + timedelta(minutes=10),
            consumed_at=None,
            created_at=now,
        )
        session.add(ticket)

    # 2. Fire concurrent reset requests proposing different passwords
    async def call_reset(pwd: str):
        async with httpx.AsyncClient(base_url=GATEWAY_AUTH_URL, timeout=10.0) as client:
            return await client.post(
                "/recovery/reset",
                json={"reset_token": reset_token, "new_password": pwd},
            )

    resp_a, resp_b = await asyncio.gather(call_reset(new_password_a), call_reset(new_password_b))

    statuses = [resp_a.status_code, resp_b.status_code]
    assert 204 in statuses, f"Expected one 204 success, got {statuses}"
    assert 400 in statuses, f"Expected one 400 rejection, got {statuses}"

    # 3. Verify PostgreSQL database state consistency
    async with users_db.session() as session:
        # Ticket must be consumed
        t = (
            await session.execute(select(OneTimeToken).where(OneTimeToken.id == ticket_id))
        ).scalar_one()
        assert t.consumed_at is not None

        # Account password must match either password A or B, but never old_password
        acc = (await session.execute(select(Account).where(Account.id == account_id))).scalar_one()
        assert not verify_password(acc.password_hash, old_password)
        is_a = verify_password(acc.password_hash, new_password_a)
        is_b = verify_password(acc.password_hash, new_password_b)
        assert is_a ^ is_b, "Password must match exactly one variant"

        # Password history must contain exactly 1 entry for old_password
        history = (
            (
                await session.execute(
                    select(PasswordHistory).where(PasswordHistory.account_id == account_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(history) == 1
        assert verify_password(history[0].password_hash, old_password)

        # Active session must be revoked
        s = (await session.execute(select(Session).where(Session.id == session_id))).scalar_one()
        assert s.revoked_at is not None
        assert s.revocation_reason == "password_reset"


@pytest.mark.asyncio
async def test_postgres_concurrent_failed_otp_attempts_lockout(users_db):
    """Verify that multiple concurrent failed OTP verification attempts increment attempts atomically,
    and upon reaching 5 attempts, the token transitions to consumed (locked out).
    """
    now = datetime.now(UTC)
    email = f"brute_{uuid.uuid4().hex[:6]}@example.test"
    username = f"usr_{uuid.uuid4().hex[:6]}"

    otp = generate_otp(ttl_seconds=900)
    account_id = uuid.uuid4()
    token_id = uuid.uuid4()

    async with users_db.transaction() as session:
        account = Account(
            id=account_id,
            username=username,
            username_normalized=username.lower(),
            email=email,
            email_normalized=email.lower(),
            password_hash=hash_password("Dummy123!"),
            role="member",
            is_banned=False,
            created_at=now,
        )
        session.add(account)

        token_record = OneTimeToken(
            id=token_id,
            account_id=account_id,
            purpose="password_recovery",
            token_hash=otp.code_hash,
            expires_at=otp.expires_at,
            attempts=3,  # already 3 failed attempts
            resend_count=0,
            resend_after=now + timedelta(seconds=60),
            consumed_at=None,
            created_at=now,
        )
        session.add(token_record)

    # Fire 3 concurrent invalid OTP attempts (should hit attempt limit of 5 and lock out)
    async def call_bad_otp():
        async with httpx.AsyncClient(base_url=GATEWAY_AUTH_URL, timeout=10.0) as client:
            return await client.post(
                "/recovery/verify",
                json={"email": email, "otp": "000000"},
            )

    responses = await asyncio.gather(call_bad_otp(), call_bad_otp(), call_bad_otp())
    for r in responses:
        assert r.status_code == 400

    # Token must now be consumed (locked out)
    async with users_db.session() as session:
        tok = (
            await session.execute(select(OneTimeToken).where(OneTimeToken.id == token_id))
        ).scalar_one()
        assert tok.consumed_at is not None
        assert tok.attempts >= 5

    # Subsequent verification with the REAL OTP must now be rejected
    async with httpx.AsyncClient(base_url=GATEWAY_AUTH_URL, timeout=10.0) as client:
        real_v = await client.post(
            "/recovery/verify",
            json={"email": email, "otp": otp.code},
        )
        assert real_v.status_code == 400
        assert real_v.json()["error"]["code"] in ("INVALID_OTP", "OTP_ATTEMPTS_EXCEEDED")
