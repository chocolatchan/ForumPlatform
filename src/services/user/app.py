"""User Service FastAPI application entry point with full Authentication & Account Lifecycle."""

import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.common.errors import (
    error_response,
    not_implemented_response,
    register_canonical_error_handlers,
)
from src.common.logging import configure_logging
from src.common.metrics import MetricsRegistry, setup_metrics_endpoint
from src.common.models import ensure_utc
from src.common.outbox_worker import OutboxWorker
from src.common.rate_limit import SlidingWindowRateLimiter, get_client_ip
from src.services.user.auth import (
    AuthenticatedContext,
    get_current_session,
    require_role,
    verify_csrf_header,
)
from src.services.user.config import user_settings
from src.services.user.database import user_db
from src.services.user.models import Account, PendingRegistration, Session, UserOutboxEvent
from src.services.user.otp_delivery import (
    EmailDeliveryError,
    deliver_otp,
    otp_delivery_sink,
)
from src.services.user.schemas import (
    BanUserRequest,
    ChangeEmailRequest,
    ChangeEmailVerifyRequest,
    ChangePasswordRequest,
    ChangeUserRoleRequest,
    LoginRequest,
    RecoveryRequest,
    RecoveryVerifyRequest,
    RegisterRequest,
    ResendOtpRequest,
    ResetPasswordRequest,
    UpdateProfileRequest,
    VerifyOtpRequest,
)
from src.services.user.security import (
    generate_csrf_token,
    generate_otp,
    generate_session_token,
    hash_password,
    hash_secret,
    verify_otp_hash,
    verify_password,
)

logger = logging.getLogger(__name__)
configure_logging(user_settings.service_name, user_settings.log_level)
metrics_registry = MetricsRegistry(user_settings.service_name)


outbox_worker = OutboxWorker(
    service_name="user-service",
    db_manager=user_db,
    model_class=UserOutboxEvent,
    service_urls=user_settings.get_service_urls(),
    internal_key=user_settings.internal_service_secret,
    poll_interval_seconds=1.0,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await outbox_worker.start()
    yield
    await outbox_worker.stop()
    await user_db.close()


app = FastAPI(
    title="User Service",
    version="1.1.0",
    lifespan=lifespan,
)

register_canonical_error_handlers(app)
setup_metrics_endpoint(app, metrics_registry, db_manager=user_db, outbox_worker=outbox_worker)

# Abuse controls: sliding window rate limiters (Task 007)
rate_limiter_register = SlidingWindowRateLimiter(
    max_requests=user_settings.rate_limit_register_max,
    window_seconds=user_settings.rate_limit_window_seconds,
)
rate_limiter_login = SlidingWindowRateLimiter(
    max_requests=user_settings.rate_limit_login_max,
    window_seconds=user_settings.rate_limit_window_seconds,
)
rate_limiter_verify_otp = SlidingWindowRateLimiter(
    max_requests=user_settings.rate_limit_verify_otp_max,
    window_seconds=user_settings.rate_limit_window_seconds,
)
rate_limiter_resend_otp = SlidingWindowRateLimiter(
    max_requests=user_settings.rate_limit_resend_otp_max,
    window_seconds=user_settings.rate_limit_window_seconds,
)


# --- Health & Readiness ---
@app.get("/health/live", tags=["Health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["Health"])
async def readiness() -> JSONResponse:
    db_healthy = await user_db.ping()
    if not db_healthy:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "database": "unreachable"},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "ready", "database": "connected"},
    )


# --- Synchronizer CSRF Token (ADR-004) ---
@app.get("/auth/csrf", tags=["Auth"])
async def get_csrf_token() -> dict[str, str]:
    """Return a cryptographically secure synchronizer CSRF token as specified in OpenAPI."""
    token = generate_csrf_token(user_settings.csrf_secret)
    return {"csrf_token": token}


# --- Development/Testing OTP Inspection Hook (FR-2) ---
@app.get("/test/otp", include_in_schema=False)
async def get_test_otp(recipient: str) -> JSONResponse:
    """Development/testing-only endpoint to inspect latest OTP code without leaking via public APIs."""
    if user_settings.environment == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not available")
    code = otp_delivery_sink.get_latest_otp(recipient)
    if not code:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No OTP found for recipient"
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content={"code": code})


# --- FR-1: Account Registration (DEC-001) ---
@app.post("/auth/register", tags=["Auth"])
async def register(req: RegisterRequest, request: Request) -> JSONResponse:
    """Start registration flow and issue verification OTP.

    Per DEC-001: Returns neutral 202 Accepted without revealing whether email or username exists.
    """
    client_ip = get_client_ip(request, user_settings.trusted_proxies)
    if not rate_limiter_register.is_allowed(f"{client_ip}:register"):
        return error_response(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="RATE_LIMITED",
            message="Too many registration attempts. Please try again later.",
            request=request,
        )

    username_norm = req.username.lower().strip()
    email_norm = req.email.lower().strip()
    now = datetime.now(UTC)

    async with user_db.transaction() as db_session:
        # Check existing active account
        acc_query = select(Account).where(
            (Account.username_normalized == username_norm)
            | (Account.email_normalized == email_norm)
        )
        existing_acc = (await db_session.execute(acc_query)).scalar_one_or_none()
        if existing_acc:
            # Neutral response per DEC-001
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={
                    "status": "pending_verification",
                    "message": "If the registration details are eligible, a verification code has been sent.",
                },
            )

        # Check existing pending registration with lock to prevent concurrent duplicate races
        pending_query = (
            select(PendingRegistration)
            .where(
                (PendingRegistration.email_normalized == email_norm)
                | (PendingRegistration.username_normalized == username_norm)
            )
            .with_for_update()
        )
        pending = (await db_session.execute(pending_query)).scalar_one_or_none()

        otp = generate_otp(ttl_seconds=600)
        hashed_pwd = hash_password(req.password)

        if pending:
            # Update pending challenge with new OTP, reset attempts
            pending.username = req.username
            pending.username_normalized = username_norm
            pending.email = req.email
            pending.email_normalized = email_norm
            pending.password_hash = hashed_pwd
            pending.otp_hash = otp.code_hash
            pending.otp_expires_at = otp.expires_at
            pending.otp_attempts = 0
            pending.resend_count = 0
            pending.resend_available_at = now + timedelta(seconds=60)
            pending.status = "pending"
        else:
            pending = PendingRegistration(
                id=uuid.uuid4(),
                username=req.username,
                username_normalized=username_norm,
                email=req.email,
                email_normalized=email_norm,
                password_hash=hashed_pwd,
                otp_hash=otp.code_hash,
                otp_expires_at=otp.expires_at,
                otp_attempts=0,
                resend_count=0,
                resend_available_at=now + timedelta(seconds=60),
                status="pending",
            )
            db_session.add(pending)
        pending_id = pending.id

    # Deliver OTP via configured delivery backend (never logged or exposed in public response)
    try:
        await deliver_otp(
            recipient=email_norm,
            code=otp.code,
            purpose="registration",
            timestamp=now,
        )
    except EmailDeliveryError as exc:
        logger.warning(
            "OTP email delivery failed for registration: recipient=%s (is_rejected=%s)",
            email_norm,
            exc.is_recipient_rejected,
        )
        # Transaction safety: invalidating/expiring pending challenge so it cannot be verified
        async with user_db.transaction() as db_session:
            pending_query = select(PendingRegistration).where(
                PendingRegistration.id == pending_id,
                PendingRegistration.status == "pending",
            )
            failed_pending = (await db_session.execute(pending_query)).scalar_one_or_none()
            if failed_pending:
                failed_pending.status = "delivery_failed"

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "pending_verification",
            "message": "If the registration details are eligible, a verification code has been sent.",
        },
    )


# --- FR-2: OTP Verification & Account Activation ---
@app.post("/auth/register/verify", tags=["Auth"])
async def verify_registration(req: VerifyOtpRequest, request: Request) -> JSONResponse:
    """Verify registration OTP and create active user account."""
    client_ip = get_client_ip(request, user_settings.trusted_proxies)
    if not rate_limiter_verify_otp.is_allowed(f"{client_ip}:verify_otp"):
        return error_response(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="RATE_LIMITED",
            message="Too many verification attempts. Please try again later.",
            request=request,
        )

    email_norm = req.email.lower().strip()
    now = datetime.now(UTC)

    async with user_db.transaction() as db_session:
        # Atomic lock/select for pending registration
        pending_query = (
            select(PendingRegistration)
            .where(
                PendingRegistration.email_normalized == email_norm,
                PendingRegistration.status == "pending",
            )
            .with_for_update()
        )
        pending = (await db_session.execute(pending_query)).scalar_one_or_none()

        if not pending:
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="INVALID_OTP",
                message="Invalid or expired verification challenge.",
                request=request,
            )

        # Check maximum attempts (max 5)
        if pending.otp_attempts >= 5:
            pending.status = "expired"
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="OTP_ATTEMPTS_EXCEEDED",
                message="Maximum verification attempts exceeded.",
                request=request,
            )

        # Check expiration
        expires_at = ensure_utc(pending.otp_expires_at)
        if expires_at and now > expires_at:
            pending.status = "expired"
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="OTP_EXPIRED",
                message="Verification challenge has expired.",
                request=request,
            )

        # Increment attempt counter
        pending.otp_attempts += 1

        # Verify OTP code hash
        if not verify_otp_hash(pending.otp_hash, req.otp):
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="INVALID_OTP",
                message="Invalid verification code.",
                request=request,
            )

        # Prevent duplicate account creation if an account was created concurrently
        existing_acc = (
            await db_session.execute(
                select(Account).where(
                    (Account.email_normalized == email_norm)
                    | (Account.username_normalized == pending.username_normalized)
                )
            )
        ).scalar_one_or_none()

        if existing_acc:
            pending.status = "consumed"
            return error_response(
                status_code=status.HTTP_409_CONFLICT,
                code="ACCOUNT_ALREADY_EXISTS",
                message="An account with this email or username already exists.",
                request=request,
            )

        # Create active account
        new_account = Account(
            id=uuid.uuid4(),
            username=pending.username,
            username_normalized=pending.username_normalized,
            display_name=None,
            email=pending.email,
            email_normalized=pending.email_normalized,
            password_hash=pending.password_hash,
            role="member",
            is_banned=False,
            email_verified_at=now,
            password_changed_at=now,
        )
        db_session.add(new_account)
        pending.status = "consumed"

        outbox = UserOutboxEvent(
            id=uuid.uuid4(),
            event_type="UserRegistered",
            aggregate_type="User",
            aggregate_id=new_account.id,
            payload={
                "user_id": str(new_account.id),
                "username": new_account.username,
                "email": new_account.email,
                "role": new_account.role,
                "created_at": now.isoformat(),
            },
        )
        db_session.add(outbox)

        try:
            await db_session.flush()
        except IntegrityError:
            return error_response(
                status_code=status.HTTP_409_CONFLICT,
                code="ACCOUNT_ALREADY_EXISTS",
                message="An account with this email or username already exists.",
                request=request,
            )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "user": {
                "id": str(new_account.id),
                "username": new_account.username,
                "display_name": new_account.display_name,
                "bio": new_account.bio,
                "role": new_account.role,
            }
        },
    )


# --- Resend Registration OTP (DEC-001) ---
@app.post("/auth/register/resend-otp", tags=["Auth"])
async def resend_registration_otp(req: ResendOtpRequest, request: Request) -> JSONResponse:
    """Resend registration OTP respecting rate limits and quota.

    Returns neutral 202 Accepted.
    """
    client_ip = get_client_ip(request, user_settings.trusted_proxies)
    if not rate_limiter_resend_otp.is_allowed(f"{client_ip}:resend_otp"):
        return error_response(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="RATE_LIMITED",
            message="Too many OTP resend attempts. Please try again later.",
            request=request,
        )

    email_norm = req.email.lower().strip()
    now = datetime.now(UTC)

    async with user_db.transaction() as db_session:
        pending_query = (
            select(PendingRegistration)
            .where(
                PendingRegistration.email_normalized == email_norm,
                PendingRegistration.status == "pending",
            )
            .with_for_update()
        )
        pending = (await db_session.execute(pending_query)).scalar_one_or_none()

        if pending:
            resend_at = ensure_utc(pending.resend_available_at)
            expires_at = ensure_utc(pending.otp_expires_at)
            if (
                pending.resend_count < 3
                and resend_at
                and now >= resend_at
                and expires_at
                and now <= expires_at
            ):
                otp = generate_otp(ttl_seconds=600)
                pending.otp_hash = otp.code_hash
                pending.otp_expires_at = otp.expires_at
                pending.resend_count += 1
                pending.resend_available_at = now + timedelta(seconds=60)

                # Attempt delivery
                try:
                    await deliver_otp(
                        recipient=email_norm,
                        code=otp.code,
                        purpose="registration",
                        timestamp=now,
                    )
                except EmailDeliveryError as exc:
                    logger.warning(
                        "OTP email resend failed: recipient=%s (is_rejected=%s)",
                        email_norm,
                        exc.is_recipient_rejected,
                    )
                    pending.status = "delivery_failed"

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "message": "If details are eligible, the request will be processed.",
        },
    )


# --- FR-3 & FR-4: Login and Session Creation (ADR-003) ---
@app.post("/auth/login", tags=["Auth"])
async def login(req: LoginRequest, response: Response, request: Request) -> JSONResponse:
    """Authenticate user with username or email and issue opaque forum_session cookie."""
    client_ip = get_client_ip(request, user_settings.trusted_proxies)
    if not rate_limiter_login.is_allowed(f"{client_ip}:login"):
        return error_response(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="RATE_LIMITED",
            message="Too many login attempts. Please try again later.",
            request=request,
        )

    login_norm = req.login.lower().strip()
    now = datetime.now(UTC)

    async with user_db.transaction() as db_session:
        query = select(Account).where(
            (Account.username_normalized == login_norm) | (Account.email_normalized == login_norm)
        )
        account = (await db_session.execute(query)).scalar_one_or_none()

        if not account or not verify_password(account.password_hash, req.password):
            return error_response(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code="INVALID_CREDENTIALS",
                message="Invalid username/email or password.",
                request=request,
            )

        if account.is_banned:
            return error_response(
                status_code=status.HTTP_403_FORBIDDEN,
                code="ACCOUNT_BANNED",
                message="Account has been suspended or banned.",
                request=request,
            )

        # Issue opaque server-side session token
        session_token = generate_session_token()
        session_token_hash = hash_secret(session_token)

        idle_expires_at = now + timedelta(seconds=user_settings.session_idle_timeout_seconds)
        absolute_expires_at = now + timedelta(
            seconds=user_settings.session_absolute_timeout_seconds
        )

        session_record = Session(
            id=uuid.uuid4(),
            account_id=account.id,
            session_token_hash=session_token_hash,
            created_at=now,
            last_seen_at=now,
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
            revoked_at=None,
            revocation_reason=None,
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )
        db_session.add(session_record)

    # Set-Cookie forum_session (ADR-003)
    cookie_secure = user_settings.is_cookie_secure()
    response.set_cookie(
        key=user_settings.session_cookie_name,
        value=session_token,
        max_age=user_settings.session_absolute_timeout_seconds,
        httponly=True,
        secure=cookie_secure,
        samesite="lax",
        path="/",
    )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        headers=dict(response.headers),
        content={
            "user": {
                "id": str(account.id),
                "username": account.username,
                "display_name": account.display_name,
                "bio": account.bio,
                "role": account.role,
            },
            "session": {
                "idle_expires_at": idle_expires_at.isoformat(),
                "absolute_expires_at": absolute_expires_at.isoformat(),
            },
        },
    )


# --- FR-4: Logout and Session Invalidation ---
@app.post("/auth/logout", tags=["Auth"], dependencies=[Depends(verify_csrf_header)])
async def logout(
    response: Response,
    ctx: AuthenticatedContext = Depends(get_current_session),
) -> Response:
    """Revoke current session and clear session cookie."""
    now = datetime.now(UTC)

    async with user_db.transaction() as db_session:
        s_query = select(Session).where(Session.id == ctx.session.id)
        current_s = (await db_session.execute(s_query)).scalar_one_or_none()
        if current_s:
            current_s.revoked_at = now
            current_s.revocation_reason = "user_logout"

    # Delete cookie
    cookie_secure = user_settings.is_cookie_secure()
    response.delete_cookie(
        key=user_settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=cookie_secure,
        samesite="lax",
    )
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers=dict(response.headers),
    )


# --- FR-5: Current Session & User Metadata ---
@app.get("/auth/session", tags=["Auth"])
async def get_current_session_info(
    ctx: AuthenticatedContext = Depends(get_current_session),
) -> JSONResponse:
    """Get active session details and authenticated user profile."""
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "user": {
                "id": str(ctx.user.id),
                "username": ctx.user.username,
                "display_name": ctx.user.display_name,
                "bio": ctx.user.bio,
                "role": ctx.user.role,
            },
            "session": {
                "idle_expires_at": ctx.session.idle_expires_at.isoformat(),
                "absolute_expires_at": ctx.session.absolute_expires_at.isoformat(),
            },
        },
    )


# --- FR-5 & FR-6: Own Profile Operations ---
@app.get("/profile", tags=["Profile"])
async def get_own_profile(
    ctx: AuthenticatedContext = Depends(get_current_session),
) -> JSONResponse:
    """Get detailed private profile for authenticated user."""
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "id": str(ctx.user.id),
            "username": ctx.user.username,
            "display_name": ctx.user.display_name,
            "bio": ctx.user.bio,
            "email": ctx.user.email,
            "role": ctx.user.role,
            "created_at": ctx.user.created_at.isoformat() if ctx.user.created_at else None,
        },
    )


@app.patch("/profile", tags=["Profile"], dependencies=[Depends(verify_csrf_header)])
async def update_own_profile(
    req: UpdateProfileRequest,
    ctx: AuthenticatedContext = Depends(get_current_session),
) -> JSONResponse:
    """Update editable profile fields (display_name, bio)."""
    async with user_db.transaction() as db_session:
        acc_query = select(Account).where(Account.id == ctx.user.id)
        account = (await db_session.execute(acc_query)).scalar_one()

        if req.display_name is not None:
            account.display_name = req.display_name
        if req.bio is not None:
            account.bio = req.bio

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "id": str(account.id),
            "username": account.username,
            "display_name": account.display_name,
            "bio": account.bio,
            "email": account.email,
            "role": account.role,
            "created_at": account.created_at.isoformat() if account.created_at else None,
        },
    )


# --- Controlled 501 Placeholders for recovery & email/password change ---
@app.post("/auth/recovery/request", tags=["Auth"])
async def recovery_request(req: RecoveryRequest, request: Request) -> JSONResponse:
    return not_implemented_response(request, "POST /auth/recovery/request")


@app.post("/auth/recovery/verify", tags=["Auth"])
async def recovery_verify(req: RecoveryVerifyRequest, request: Request) -> JSONResponse:
    return not_implemented_response(request, "POST /auth/recovery/verify")


@app.post("/auth/recovery/reset", tags=["Auth"])
async def recovery_reset(req: ResetPasswordRequest, request: Request) -> JSONResponse:
    return not_implemented_response(request, "POST /auth/recovery/reset")


@app.post(
    "/profile/change-email/request", tags=["Profile"], dependencies=[Depends(verify_csrf_header)]
)
async def change_email_request(req: ChangeEmailRequest, request: Request) -> JSONResponse:
    return not_implemented_response(request, "POST /profile/change-email/request")


@app.post(
    "/profile/change-email/verify", tags=["Profile"], dependencies=[Depends(verify_csrf_header)]
)
async def change_email_verify(req: ChangeEmailVerifyRequest, request: Request) -> JSONResponse:
    return not_implemented_response(request, "POST /profile/change-email/verify")


@app.post("/profile/change-password", tags=["Profile"], dependencies=[Depends(verify_csrf_header)])
async def change_password(req: ChangePasswordRequest, request: Request) -> JSONResponse:
    return not_implemented_response(request, "POST /profile/change-password")


# --- Public User Profile ---
@app.get("/users/{user_id}", tags=["Profile"])
async def get_user_profile(user_id: uuid.UUID, request: Request) -> JSONResponse:
    """Retrieve public profile of a user by UUID."""
    async with user_db.session() as db_session:
        query = select(Account).where(Account.id == user_id)
        account = (await db_session.execute(query)).scalar_one_or_none()
        if not account:
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="USER_NOT_FOUND",
                message=f"User with ID '{user_id}' was not found.",
                request=request,
            )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "id": str(account.id),
                "username": account.username,
                "display_name": account.display_name,
                "bio": account.bio,
                "role": account.role,
            },
        )


# --- FR-6: Admin User Management ---
@app.get(
    "/admin/users",
    tags=["Admin Users"],
    dependencies=[Depends(require_role("admin"))],
)
async def admin_list_users(
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
) -> JSONResponse:
    """List registered users with administrative details (Admin only)."""
    async with user_db.session() as db_session:
        query = select(Account).order_by(Account.created_at.desc()).limit(limit)
        accounts = (await db_session.execute(query)).scalars().all()
        items = [
            {
                "id": str(acc.id),
                "username": acc.username,
                "display_name": acc.display_name,
                "email": acc.email,
                "role": acc.role,
                "is_banned": acc.is_banned,
                "ban_reason": acc.ban_reason,
                "created_at": acc.created_at.isoformat() if acc.created_at else None,
            }
            for acc in accounts
        ]
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"items": items, "next_cursor": None},
        )


@app.patch(
    "/admin/users/{user_id}/role",
    tags=["Admin Users"],
    dependencies=[Depends(require_role("admin")), Depends(verify_csrf_header)],
)
async def admin_change_user_role(
    user_id: uuid.UUID,
    req: ChangeUserRoleRequest,
    request: Request,
) -> JSONResponse:
    """Modify user role (Admin only)."""
    async with user_db.transaction() as db_session:
        query = select(Account).where(Account.id == user_id)
        account = (await db_session.execute(query)).scalar_one_or_none()
        if not account:
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="USER_NOT_FOUND",
                message=f"User with ID '{user_id}' was not found.",
                request=request,
            )
        account.role = req.role

        # Security hardening: invalidate active sessions when role changes
        # to ensure role changes take immediate effect across all sessions
        from sqlalchemy import update

        await db_session.execute(
            update(Session)
            .where(Session.account_id == user_id, Session.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC), revocation_reason="role_change")
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "id": str(account.id),
            "username": account.username,
            "display_name": account.display_name,
            "email": account.email,
            "role": account.role,
            "is_banned": account.is_banned,
            "ban_reason": account.ban_reason,
            "created_at": account.created_at.isoformat() if account.created_at else None,
        },
    )


@app.put(
    "/admin/users/{user_id}/ban",
    tags=["Admin Users"],
    dependencies=[Depends(require_role("admin", "moderator")), Depends(verify_csrf_header)],
)
async def admin_ban_user(
    user_id: uuid.UUID,
    req: BanUserRequest,
    request: Request,
) -> JSONResponse:
    """Ban or unban a user account (Admin/Moderator)."""
    async with user_db.transaction() as db_session:
        query = select(Account).where(Account.id == user_id)
        account = (await db_session.execute(query)).scalar_one_or_none()
        if not account:
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="USER_NOT_FOUND",
                message=f"User with ID '{user_id}' was not found.",
                request=request,
            )

        account.is_banned = req.is_banned
        account.ban_reason = req.ban_reason if req.is_banned else None

        if req.is_banned:
            from sqlalchemy import update

            await db_session.execute(
                update(Session)
                .where(Session.account_id == user_id, Session.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC), revocation_reason="account_banned")
            )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "id": str(account.id),
            "username": account.username,
            "display_name": account.display_name,
            "email": account.email,
            "role": account.role,
            "is_banned": account.is_banned,
            "ban_reason": account.ban_reason,
            "created_at": account.created_at.isoformat() if account.created_at else None,
        },
    )
