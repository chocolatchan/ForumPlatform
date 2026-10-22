"""FastAPI authentication and CSRF dependencies for User Service."""

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, Request, status
from fastapi.exceptions import HTTPException
from sqlalchemy import select

import src.services.user.database as user_db_module
from src.common.models import ensure_utc
from src.services.user.config import user_settings
from src.services.user.models import Account, Session
from src.services.user.security import hash_secret, verify_csrf_token


@dataclass
class AuthenticatedContext:
    user: Account
    session: Session
    session_token: str


async def get_current_session(request: Request) -> AuthenticatedContext:
    """Validate opaque server-side session cookie forum_session.

    Enforces idle timeout (4h), absolute timeout (24h), revocation status, and account active/ban status.
    """
    cookie_name = user_settings.session_cookie_name
    session_token = request.cookies.get(cookie_name)
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required: session cookie missing.",
        )

    token_hash = hash_secret(session_token)
    now = datetime.now(UTC)

    async with user_db_module.user_db.session() as db_session:
        query = (
            select(Session, Account)
            .join(Account, Session.account_id == Account.id)
            .where(Session.session_token_hash == token_hash)
        )
        result = (await db_session.execute(query)).first()

        if not result:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired session.",
            )

        session_obj, account = result

        # Check revocation
        if session_obj.revoked_at is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session has been revoked.",
            )

        # Check absolute expiration
        abs_exp = ensure_utc(session_obj.absolute_expires_at)
        if abs_exp and now > abs_exp:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired (absolute timeout).",
            )

        # Check idle expiration
        idle_exp = ensure_utc(session_obj.idle_expires_at)
        if idle_exp and now > idle_exp:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired (idle timeout).",
            )

        # Check account ban/status
        if account.is_banned:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is banned.",
            )

    from datetime import timedelta

    # Optimize write amplification: only update last_seen_at and idle_expires_at if last_seen was > 60s ago
    last_seen_utc = ensure_utc(session_obj.last_seen_at)
    if last_seen_utc is None or (now - last_seen_utc).total_seconds() > 60:
        abs_exp_clean = ensure_utc(session_obj.absolute_expires_at) or now
        calculated_idle = min(
            now + timedelta(seconds=user_settings.session_idle_timeout_seconds),
            abs_exp_clean,
        )

        async with user_db_module.user_db.transaction() as update_tx:
            s_query = select(Session).where(Session.id == session_obj.id)
            current_s = (await update_tx.execute(s_query)).scalar_one_or_none()
            if current_s:
                current_s.last_seen_at = now
                current_s.idle_expires_at = calculated_idle

    return AuthenticatedContext(user=account, session=session_obj, session_token=session_token)


def require_role(*allowed_roles: str):
    """Enforce role authorization on protected endpoints."""

    def role_checker(
        ctx: AuthenticatedContext = Depends(get_current_session),
    ) -> AuthenticatedContext:
        if ctx.user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: requires role in {allowed_roles}, your role is '{ctx.user.role}'.",
            )
        return ctx

    return role_checker


async def verify_csrf_header(request: Request) -> None:
    """Enforce synchronizer CSRF token check (ADR-004) on state-changing requests."""
    # Only enforce when a session cookie is present
    cookie_name = user_settings.session_cookie_name
    if cookie_name not in request.cookies:
        return

    csrf_token = request.headers.get("X-CSRF-Token")
    if not csrf_token or not verify_csrf_token(csrf_token, user_settings.csrf_secret):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token validation failed or missing X-CSRF-Token header.",
        )


async def verify_internal_service_key(request: Request) -> None:
    """Verify internal service-to-service key for internal endpoints."""
    key = request.headers.get("X-Internal-Service-Key")
    expected = user_settings.internal_service_secret
    if key:
        if key != expected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Internal service key invalid.",
            )
        return

    # In local testing or direct execution without Gateway, allow if environment is development/test
    if user_settings.environment in ("development", "test"):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Internal service key missing.",
    )

