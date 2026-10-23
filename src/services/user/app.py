"""User Service FastAPI application entry point with full Authentication & Account Lifecycle."""

import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.errors import (
    error_response,
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
    verify_internal_service_key,
)
from src.services.user.config import user_settings
from src.services.user.database import user_db
from src.services.user.models import (
    Account,
    EmailChangeRequest,
    OneTimeToken,
    PasswordHistory,
    PendingRegistration,
    Session,
    UserOutboxEvent,
)
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
    SetAvatarRequest,
    UpdateProfileRequest,
    VerifyOtpRequest,
)
from src.services.user.security import (
    generate_csrf_token,
    generate_otp,
    generate_reset_token,
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
rate_limiter_recovery_request = SlidingWindowRateLimiter(
    max_requests=user_settings.rate_limit_recovery_request_max,
    window_seconds=user_settings.rate_limit_window_seconds,
)
rate_limiter_recovery_verify = SlidingWindowRateLimiter(
    max_requests=user_settings.rate_limit_recovery_verify_max,
    window_seconds=user_settings.rate_limit_window_seconds,
)
rate_limiter_recovery_reset = SlidingWindowRateLimiter(
    max_requests=user_settings.rate_limit_recovery_reset_max,
    window_seconds=user_settings.rate_limit_window_seconds,
)
rate_limiter_change_email_request = SlidingWindowRateLimiter(
    max_requests=5,
    window_seconds=user_settings.rate_limit_window_seconds,
)
rate_limiter_change_email_verify = SlidingWindowRateLimiter(
    max_requests=10,
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


# --- Development/Testing OTP & Rate Limiter Inspection Hook (FR-2) ---
def require_test_endpoints() -> None:
    """Reject test-only hooks unless development/test is explicitly configured."""
    if not user_settings.test_endpoints_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not available")


@app.get(
    "/test/otp",
    include_in_schema=False,
    dependencies=[Depends(require_test_endpoints)],
)
async def get_test_otp(recipient: str) -> JSONResponse:
    """Development/testing-only endpoint to inspect latest OTP code without leaking via public APIs."""
    code = otp_delivery_sink.get_latest_otp(recipient)
    if not code:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No OTP found for recipient"
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content={"code": code})


@app.post(
    "/test/reset-rate-limiters",
    include_in_schema=False,
    dependencies=[Depends(require_test_endpoints)],
)
async def reset_rate_limiters_hook() -> JSONResponse:
    """Development/testing-only endpoint to reset sliding window limiters."""
    rate_limiter_register.reset()
    rate_limiter_login.reset()
    rate_limiter_verify_otp.reset()
    rate_limiter_resend_otp.reset()
    rate_limiter_recovery_request.reset()
    rate_limiter_recovery_verify.reset()
    rate_limiter_recovery_reset.reset()
    rate_limiter_change_email_request.reset()
    rate_limiter_change_email_verify.reset()
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "reset"})


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
    avatar_url = (
        f"/uploads/images/{ctx.user.avatar_object_key}"
        if ctx.user.avatar_object_key
        else None
    )
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
            "avatar_url": avatar_url,
        },
    )


@app.patch("/profile", tags=["Profile"], dependencies=[Depends(verify_csrf_header)])
async def update_own_profile(
    req: UpdateProfileRequest,
    ctx: AuthenticatedContext = Depends(get_current_session),
) -> JSONResponse:
    """Update editable profile fields (display_name, bio)."""
    async with user_db.transaction() as db_session:
        acc_query = select(Account).where(Account.id == ctx.user.id).with_for_update()
        account = (await db_session.execute(acc_query)).scalar_one()

        if req.display_name is not None:
            account.display_name = req.display_name
        if req.bio is not None:
            account.bio = req.bio

    avatar_url = (
        f"/uploads/images/{account.avatar_object_key}"
        if account.avatar_object_key
        else None
    )
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
            "avatar_url": avatar_url,
        },
    )


@app.put("/profile/avatar", tags=["Profile"], dependencies=[Depends(verify_csrf_header)])
async def set_avatar(
    req: SetAvatarRequest,
    request: Request,
    ctx: AuthenticatedContext = Depends(get_current_session),
) -> JSONResponse:
    """Set or replace user profile avatar by claiming an uploaded attachment."""
    content_url = user_settings.content_service_url.rstrip("/")
    # Content Service authenticates internal calls with X-Internal-Service-Key.
    headers = {"X-Internal-Service-Key": user_settings.internal_service_secret}
    payload = {"user_id": str(ctx.user.id)}

    try:
        async with httpx.AsyncClient(base_url=content_url, timeout=5.0) as client:
            resp = await client.post(
                f"/internal/attachments/{req.image_id}/claim-avatar",
                headers=headers,
                json=payload,
            )
    except httpx.HTTPError as exc:
        logger.error("Failed to connect to Content Service: %s", exc)
        return error_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="SERVICE_UNAVAILABLE",
            message="Content service unavailable.",
            request=request,
        )

    if resp.status_code == 404:
        return error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="ATTACHMENT_NOT_FOUND",
            message="Attachment not found.",
            request=request,
        )
    if resp.status_code == 403:
        return error_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN_ATTACHMENT_CLAIM",
            message="Cannot use an attachment that does not belong to you.",
            request=request,
        )
    if resp.status_code == 409:
        return error_response(
            status_code=status.HTTP_409_CONFLICT,
            code="INVALID_ATTACHMENT_STATUS",
            message="Attachment cannot be used as an avatar.",
            request=request,
        )
    if resp.status_code != 200:
        logger.error("Content service avatar claim error: %s", resp.text)
        return error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="CONTENT_SERVICE_ERROR",
            message="Failed to claim avatar attachment.",
            request=request,
        )

    # Content Service confirmed the claim. Update the account reference in its own
    # transaction; this is not atomic with the remote claim (see report).
    old_avatar_id = None
    async with user_db.transaction() as db_session:
        acc_query = select(Account).where(Account.id == ctx.user.id).with_for_update()
        account = (await db_session.execute(acc_query)).scalar_one()

        if account.avatar_object_key and account.avatar_object_key != str(req.image_id):
            old_avatar_id = account.avatar_object_key

        account.avatar_object_key = str(req.image_id)

    # If there was an old avatar, best-effort release it in Content Service.
    if old_avatar_id:
        try:
            async with httpx.AsyncClient(base_url=content_url, timeout=5.0) as client:
                release_resp = await client.post(
                    f"/internal/attachments/{old_avatar_id}/release-avatar",
                    headers=headers,
                )
                if release_resp.status_code >= 400:
                    logger.warning(
                        "Failed to release old avatar %s: status %s",
                        old_avatar_id,
                        release_resp.status_code,
                    )
        except httpx.HTTPError as exc:
            logger.warning("Failed to release old avatar %s: %s", old_avatar_id, exc)

    avatar_url = f"/uploads/images/{req.image_id}"
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
            "avatar_url": avatar_url,
        },
    )


@app.delete("/profile/avatar", tags=["Profile"], dependencies=[Depends(verify_csrf_header)])
async def delete_avatar(
    request: Request,
    ctx: AuthenticatedContext = Depends(get_current_session),
) -> Response:
    """Remove user profile avatar and release the underlying attachment."""
    old_avatar_id = None
    async with user_db.transaction() as db_session:
        acc_query = select(Account).where(Account.id == ctx.user.id).with_for_update()
        account = (await db_session.execute(acc_query)).scalar_one()

        old_avatar_id = account.avatar_object_key
        account.avatar_object_key = None

    if old_avatar_id:
        content_url = user_settings.content_service_url.rstrip("/")
        headers = {"X-Internal-Service-Key": user_settings.internal_service_secret}
        try:
            async with httpx.AsyncClient(base_url=content_url, timeout=5.0) as client:
                release_resp = await client.post(
                    f"/internal/attachments/{old_avatar_id}/release-avatar",
                    headers=headers,
                )
                if release_resp.status_code >= 400:
                    logger.warning(
                        "Failed to release avatar %s: status %s",
                        old_avatar_id,
                        release_resp.status_code,
                    )
        except httpx.HTTPError as exc:
            logger.warning("Failed to release avatar %s: %s", old_avatar_id, exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Account Password Recovery & Reset (ADR-003, ADR-015, DEC-001) ---
async def _lock_recovery_challenge(
    session: AsyncSession, account_id: uuid.UUID, now: datetime
) -> OneTimeToken | None:
    """Lock and return the newest unconsumed recovery challenge.

    The intended invariant is one active ``password_recovery`` token per
    account. The database does not enforce it yet, so legacy data may contain
    duplicates. Treat the newest row as authoritative and consume older rows
    under the same lock rather than relying on a single-result scalar query.
    """
    stmt = (
        select(OneTimeToken)
        .where(
            OneTimeToken.account_id == account_id,
            OneTimeToken.purpose == "password_recovery",
            OneTimeToken.consumed_at.is_(None),
        )
        .order_by(OneTimeToken.created_at.desc(), OneTimeToken.id.desc())
        .with_for_update()
    )
    active_tokens = list((await session.execute(stmt)).scalars().all())
    if not active_tokens:
        return None

    for superseded_token in active_tokens[1:]:
        superseded_token.consumed_at = now
    return active_tokens[0]


@app.post("/auth/recovery/request", tags=["Auth"])
async def recovery_request(req: RecoveryRequest, request: Request) -> JSONResponse:
    """Request password recovery OTP with anti-enumeration neutral 202 response (DEC-001)."""
    now = datetime.now(UTC)
    client_ip = get_client_ip(request, user_settings.trusted_proxies)

    # Rate limiting on recovery requests
    if not rate_limiter_recovery_request.is_allowed(f"{client_ip}:recovery_request"):
        return error_response(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="RATE_LIMIT_EXCEEDED",
            message="Too many password recovery requests. Please try again later.",
            request=request,
        )

    email_normalized = req.email.strip().lower()

    # Look up account
    async with user_db.session() as session:
        query = select(Account).where(Account.email_normalized == email_normalized)
        account = (await session.execute(query)).scalar_one_or_none()

    if account and not account.is_banned:
        # Lock account row to serialize concurrent initial requests and prevent race condition on cooldown
        async with user_db.transaction() as session:
            acc_stmt = select(Account).where(Account.id == account.id).with_for_update()
            locked_acc = (await session.execute(acc_stmt)).scalar_one_or_none()
            if not locked_acc or locked_acc.is_banned:
                return JSONResponse(
                    status_code=status.HTTP_202_ACCEPTED,
                    content={
                        "status": "accepted",
                        "message": "If the account exists, a recovery code has been sent to your email address.",
                    },
                )

            existing_token = await _lock_recovery_challenge(session, account.id, now)

            # Enforce cooldown and quota if an active token already exists
            if existing_token:
                resend_at = ensure_utc(existing_token.resend_after)
                # If still within cooldown or resend limit reached
                if (resend_at and now < resend_at) or (existing_token.resend_count >= 3):
                    # Cooldown active or max resends reached; silently return neutral 202 without issuing new OTP
                    return JSONResponse(
                        status_code=status.HTTP_202_ACCEPTED,
                        content={
                            "status": "accepted",
                            "message": "If the account exists, a recovery code has been sent to your email address.",
                        },
                    )

            # Generate 6-digit numeric OTP with 15-minute TTL (900 seconds)
            otp = generate_otp(ttl_seconds=900)
            resend_count = (existing_token.resend_count + 1) if existing_token else 0

            # Invalidate any previous unconsumed password_recovery tokens for this account
            await session.execute(
                update(OneTimeToken)
                .where(
                    OneTimeToken.account_id == account.id,
                    OneTimeToken.purpose == "password_recovery",
                    OneTimeToken.consumed_at.is_(None),
                )
                .values(consumed_at=now)
            )

            token_record = OneTimeToken(
                id=uuid.uuid4(),
                account_id=account.id,
                purpose="password_recovery",
                token_hash=otp.code_hash,
                expires_at=otp.expires_at,
                attempts=0,
                resend_count=resend_count,
                resend_after=now + timedelta(seconds=60),
                consumed_at=None,
                created_at=now,
            )
            session.add(token_record)

        # Dispatch OTP via delivery backend (SMTP Mailpit or memory test sink)
        try:
            await deliver_otp(
                recipient=email_normalized,
                code=otp.code,
                purpose="recovery",
                timestamp=now,
            )
        except EmailDeliveryError as exc:
            logger.warning(
                "Recovery OTP delivery failed for account %s: %s", account.id, exc
            )
            # Safe failure: invalidate the token record if delivery failed
            async with user_db.transaction() as session:
                await session.execute(
                    update(OneTimeToken)
                    .where(OneTimeToken.id == token_record.id)
                    .values(consumed_at=now)
                )

    # Return neutral 202 Accepted per DEC-001 anti-enumeration contract
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "message": "If the account exists, a recovery code has been sent to your email address.",
        },
    )


@app.post("/auth/recovery/verify", tags=["Auth"])
async def recovery_verify(req: RecoveryVerifyRequest, request: Request) -> JSONResponse:
    """Verify recovery OTP and issue a short-lived reset ticket (10 min TTL)."""
    now = datetime.now(UTC)
    client_ip = get_client_ip(request, user_settings.trusted_proxies)

    if not rate_limiter_recovery_verify.is_allowed(f"{client_ip}:recovery_verify"):
        return error_response(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="RATE_LIMIT_EXCEEDED",
            message="Too many verification attempts. Please try again later.",
            request=request,
        )

    email_normalized = req.email.strip().lower()

    async with user_db.transaction() as session:
        # Find account
        acc_stmt = select(Account).where(Account.email_normalized == email_normalized).with_for_update()
        account = (await session.execute(acc_stmt)).scalar_one_or_none()
        if not account or account.is_banned:
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="INVALID_OTP",
                message="Invalid or expired recovery code.",
                request=request,
            )

        # Find the newest active recovery token. Legacy duplicate rows are
        # normalized by the helper so only one challenge remains usable.
        token_record = await _lock_recovery_challenge(session, account.id, now)

        if not token_record:
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="INVALID_OTP",
                message="No active recovery request found.",
                request=request,
            )

        # Check expiration
        expires_at = ensure_utc(token_record.expires_at)
        if expires_at and now > expires_at:
            token_record.consumed_at = now
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="OTP_EXPIRED",
                message="Recovery code has expired. Please request a new one.",
                request=request,
            )

        # Check attempt limit (max 5)
        if token_record.attempts >= 5:
            token_record.consumed_at = now
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="OTP_ATTEMPTS_EXCEEDED",
                message="Maximum verification attempts exceeded. Please request a new code.",
                request=request,
            )

        # Verify OTP constant-time
        if not verify_otp_hash(token_record.token_hash, req.otp):
            token_record.attempts += 1
            if token_record.attempts >= 5:
                token_record.consumed_at = now
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="INVALID_OTP",
                message="Invalid recovery code.",
                request=request,
            )

        # OTP verified! Consume the recovery OTP token
        token_record.consumed_at = now

        # Invalidate any prior reset tickets for this account
        await session.execute(
            update(OneTimeToken)
            .where(
                OneTimeToken.account_id == account.id,
                OneTimeToken.purpose == "password_reset_ticket",
                OneTimeToken.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )

        # Generate single-use reset ticket (10-minute TTL)
        reset_token = generate_reset_token()
        ticket_hash = hash_secret(reset_token)
        ticket_expires_at = now + timedelta(minutes=10)

        reset_ticket_record = OneTimeToken(
            id=uuid.uuid4(),
            account_id=account.id,
            purpose="password_reset_ticket",
            token_hash=ticket_hash,
            expires_at=ticket_expires_at,
            attempts=0,
            resend_count=0,
            resend_after=ticket_expires_at,
            consumed_at=None,
            created_at=now,
        )
        session.add(reset_ticket_record)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "reset_token": reset_token,
            "expires_at": ticket_expires_at.isoformat(),
        },
    )


@app.post("/auth/recovery/reset", tags=["Auth"])
async def recovery_reset(req: ResetPasswordRequest, request: Request) -> Response:
    """Reset account password using reset token, record history, and revoke all active sessions."""
    now = datetime.now(UTC)
    client_ip = get_client_ip(request, user_settings.trusted_proxies)

    if not rate_limiter_recovery_reset.is_allowed(f"{client_ip}:recovery_reset"):
        return error_response(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="RATE_LIMIT_EXCEEDED",
            message="Too many password reset attempts. Please try again later.",
            request=request,
        )

    ticket_hash = hash_secret(req.reset_token)

    # Pre-read the ticket owner to acquire the account lock first. This keeps the
    # lock order consistent with recovery_request/recovery_verify (Account -> token)
    # and avoids a verify/reset lock-order inversion. The ticket is re-read under
    # FOR UPDATE inside the transaction, so this snapshot is not trusted for state.
    async with user_db.session() as session:
        owner_stmt = select(OneTimeToken.account_id).where(
            OneTimeToken.token_hash == ticket_hash,
            OneTimeToken.purpose == "password_reset_ticket",
            OneTimeToken.consumed_at.is_(None),
        )
        ticket_account_id = (await session.execute(owner_stmt)).scalar_one_or_none()

    if not ticket_account_id:
        return error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_RESET_TOKEN",
            message="Invalid or already used password reset token.",
            request=request,
        )

    async with user_db.transaction() as session:
        # Lock account first to keep a consistent Account -> token lock order
        acc_stmt = select(Account).where(Account.id == ticket_account_id).with_for_update()
        account = (await session.execute(acc_stmt)).scalar_one_or_none()

        # Find and lock the reset ticket
        tok_stmt = (
            select(OneTimeToken)
            .where(
                OneTimeToken.token_hash == ticket_hash,
                OneTimeToken.account_id == ticket_account_id,
                OneTimeToken.purpose == "password_reset_ticket",
                OneTimeToken.consumed_at.is_(None),
            )
            .with_for_update()
        )
        ticket_record = (await session.execute(tok_stmt)).scalar_one_or_none()

        if not account or not ticket_record:
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="INVALID_RESET_TOKEN",
                message="Invalid or already used password reset token.",
                request=request,
            )

        expires_at = ensure_utc(ticket_record.expires_at)
        if expires_at and now > expires_at:
            ticket_record.consumed_at = now
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="RESET_TOKEN_EXPIRED",
                message="Password reset token has expired. Please restart recovery.",
                request=request,
            )

        # Enforce password policy & 5-generation password history
        # 1. Prevent reuse of current password
        if verify_password(account.password_hash, req.new_password):
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="PASSWORD_REUSE_FORBIDDEN",
                message="New password cannot be the same as your recent passwords.",
                request=request,
            )

        # 2. Prevent reuse of previous 5 password history records
        hist_stmt = (
            select(PasswordHistory)
            .where(PasswordHistory.account_id == account.id)
            .order_by(PasswordHistory.created_at.desc())
            .limit(5)
        )
        recent_history = (await session.execute(hist_stmt)).scalars().all()
        for hist in recent_history:
            if verify_password(hist.password_hash, req.new_password):
                return error_response(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="PASSWORD_REUSE_FORBIDDEN",
                    message="New password cannot be the same as your recent passwords.",
                    request=request,
                )

        # Consume ticket atomically
        ticket_record.consumed_at = now

        # Record current password in password_history
        session.add(
            PasswordHistory(
                account_id=account.id,
                password_hash=account.password_hash,
                created_at=now,
            )
        )

        # Update password
        new_hash = hash_password(req.new_password)
        account.password_hash = new_hash
        account.password_changed_at = now

        # Revoke ALL active sessions for this account (ADR-003)
        await session.execute(
            update(Session)
            .where(
                Session.account_id == account.id,
                Session.revoked_at.is_(None),
            )
            .values(revoked_at=now, revocation_reason="password_reset")
        )

    logger.info("Password reset successfully for account %s; all sessions revoked.", account.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Email Change Lifecycle (Task 030) ---
@app.post(
    "/profile/change-email/request",
    tags=["Profile"],
    dependencies=[Depends(verify_csrf_header)],
)
async def change_email_request(
    req: ChangeEmailRequest,
    request: Request,
    ctx: AuthenticatedContext = Depends(get_current_session),
) -> JSONResponse:
    """Request email change with neutral 202 response and anti-enumeration."""
    now = datetime.now(UTC)
    client_ip = get_client_ip(request, user_settings.trusted_proxies)

    # Rate limiting on change email requests
    if not rate_limiter_change_email_request.is_allowed(f"{client_ip}:change_email_request"):
        return error_response(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="RATE_LIMIT_EXCEEDED",
            message="Too many email change requests. Please try again later.",
            request=request,
        )

    # Re-authentication is required by the committed OpenAPI contract and enforced
    # by ChangeEmailRequest; this check rejects an incorrect password.
    if not verify_password(ctx.user.password_hash, req.password):
        return error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_PASSWORD",
            message="Current password is incorrect.",
            request=request,
        )

    new_email_clean = req.new_email.strip()
    new_email_norm = new_email_clean.lower()

    # Basic email format check
    if "@" not in new_email_norm or len(new_email_norm) < 3 or "." not in new_email_norm.split("@")[-1]:
        return error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="VALIDATION_ERROR",
            message="Invalid email address format.",
            request=request,
        )

    # If new email is identical to current email, reject
    if new_email_norm == ctx.user.email_normalized:
        return error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="EMAIL_UNCHANGED",
            message="New email cannot be the same as your current email.",
            request=request,
        )

    # Check if another account already uses this email (anti-enumeration check)
    is_taken = False
    async with user_db.session() as session:
        dup_stmt = select(Account.id).where(Account.email_normalized == new_email_norm)
        existing_acc = (await session.execute(dup_stmt)).scalar_one_or_none()
        if existing_acc and existing_acc != ctx.user.id:
            is_taken = True

    if is_taken:
        # Anti-enumeration: neutral 202 without creating request or delivering OTP
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "status": "accepted",
                "message": "If the email is valid and available, a verification code has been sent.",
            },
        )

    # Under lock on Account to serialize concurrent initial requests and prevent race condition
    email_req_record = None
    async with user_db.transaction() as session:
        acc_stmt = select(Account).where(Account.id == ctx.user.id).with_for_update()
        locked_acc = (await session.execute(acc_stmt)).scalar_one_or_none()
        if not locked_acc or locked_acc.is_banned:
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={
                    "status": "accepted",
                    "message": "If the email is valid and available, a verification code has been sent.",
                },
            )

        # Look for existing active email change request for this account
        existing_req_stmt = (
            select(EmailChangeRequest)
            .where(
                EmailChangeRequest.account_id == ctx.user.id,
                EmailChangeRequest.completed_at.is_(None),
            )
            .order_by(EmailChangeRequest.created_at.desc())
            .with_for_update()
        )
        existing_req = (await session.execute(existing_req_stmt)).scalar_one_or_none()

        if existing_req:
            resend_at = ensure_utc(existing_req.resend_available_at)
            # Check cooldown and quota
            if (resend_at and now < resend_at) or (existing_req.resend_count >= 3):
                return JSONResponse(
                    status_code=status.HTTP_202_ACCEPTED,
                    content={
                        "status": "accepted",
                        "message": "If the email is valid and available, a verification code has been sent.",
                    },
                )

        # Invalidate previous unconsumed requests
        await session.execute(
            update(EmailChangeRequest)
            .where(
                EmailChangeRequest.account_id == ctx.user.id,
                EmailChangeRequest.completed_at.is_(None),
            )
            .values(completed_at=now)
        )

        otp = generate_otp(ttl_seconds=600)
        resend_count = (existing_req.resend_count + 1) if existing_req else 0

        email_req_record = EmailChangeRequest(
            id=uuid.uuid4(),
            account_id=ctx.user.id,
            new_email=new_email_clean,
            new_email_normalized=new_email_norm,
            otp_hash=otp.code_hash,
            expires_at=otp.expires_at,
            attempts=0,
            resend_count=resend_count,
            resend_available_at=now + timedelta(seconds=60),
            created_at=now,
            completed_at=None,
        )
        session.add(email_req_record)

    # Deliver OTP to new_email
    try:
        await deliver_otp(
            recipient=new_email_norm,
            code=otp.code,
            purpose="email_change",
            timestamp=now,
        )
    except EmailDeliveryError as exc:
        logger.warning(
            "Email change OTP delivery failed for account %s: %s", ctx.user.id, exc
        )
        # Safe failure: invalidate record if delivery failed
        async with user_db.transaction() as session:
            await session.execute(
                update(EmailChangeRequest)
                .where(EmailChangeRequest.id == email_req_record.id)
                .values(completed_at=now)
            )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "message": "If the email is valid and available, a verification code has been sent.",
        },
    )


@app.post(
    "/profile/change-email/resend-otp",
    tags=["Profile"],
    dependencies=[Depends(verify_csrf_header)],
)
async def change_email_resend_otp(
    request: Request,
    ctx: AuthenticatedContext = Depends(get_current_session),
) -> JSONResponse:
    """Resend email change verification OTP respecting cooldown and quota."""
    now = datetime.now(UTC)
    client_ip = get_client_ip(request, user_settings.trusted_proxies)

    if not rate_limiter_resend_otp.is_allowed(f"{client_ip}:resend_email_change_otp"):
        return error_response(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="RATE_LIMIT_EXCEEDED",
            message="Too many resend attempts. Please try again later.",
            request=request,
        )

    otp = None
    target_email = None

    async with user_db.transaction() as session:
        acc_stmt = select(Account).where(Account.id == ctx.user.id).with_for_update()
        locked_acc = (await session.execute(acc_stmt)).scalar_one_or_none()
        if not locked_acc:
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="USER_NOT_FOUND",
                message="User account not found.",
                request=request,
            )

        existing_req_stmt = (
            select(EmailChangeRequest)
            .where(
                EmailChangeRequest.account_id == ctx.user.id,
                EmailChangeRequest.completed_at.is_(None),
            )
            .order_by(EmailChangeRequest.created_at.desc())
            .with_for_update()
        )
        existing_req = (await session.execute(existing_req_stmt)).scalar_one_or_none()

        if not existing_req:
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="NO_ACTIVE_EMAIL_CHANGE",
                message="No active email change request found.",
                request=request,
            )

        resend_at = ensure_utc(existing_req.resend_available_at)
        expires_at = ensure_utc(existing_req.expires_at)

        if resend_at and now < resend_at:
            remaining = int((resend_at - now).total_seconds())
            return error_response(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                code="RESEND_COOLDOWN_ACTIVE",
                message=f"Please wait {remaining} seconds before requesting another code.",
                request=request,
            )

        if existing_req.resend_count >= 3:
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="RESEND_LIMIT_EXCEEDED",
                message="Maximum number of resend attempts exceeded. Please initiate a new request.",
                request=request,
            )

        if expires_at and now > expires_at:
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="REQUEST_EXPIRED",
                message="Email change request has expired. Please initiate a new request.",
                request=request,
            )

        otp = generate_otp(ttl_seconds=600)
        existing_req.otp_hash = otp.code_hash
        existing_req.expires_at = otp.expires_at
        existing_req.resend_count += 1
        existing_req.resend_available_at = now + timedelta(seconds=60)
        target_email = existing_req.new_email_normalized

    try:
        await deliver_otp(
            recipient=target_email,
            code=otp.code,
            purpose="email_change",
            timestamp=now,
        )
    except EmailDeliveryError as exc:
        logger.warning(
            "Resend email change OTP delivery failed for account %s: %s", ctx.user.id, exc
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "sent", "message": "Verification code resent successfully."},
    )


@app.post(
    "/profile/change-email/verify",
    tags=["Profile"],
    dependencies=[Depends(verify_csrf_header)],
)
async def change_email_verify(
    req: ChangeEmailVerifyRequest,
    request: Request,
    ctx: AuthenticatedContext = Depends(get_current_session),
) -> JSONResponse:
    """Verify OTP and update user email atomically with uniqueness guard."""
    now = datetime.now(UTC)
    client_ip = get_client_ip(request, user_settings.trusted_proxies)

    if not rate_limiter_change_email_verify.is_allowed(f"{client_ip}:change_email_verify"):
        return error_response(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="RATE_LIMIT_EXCEEDED",
            message="Too many verification attempts. Please try again later.",
            request=request,
        )

    try:
        async with user_db.transaction() as session:
            # 1. Lock Account first. Request/resend use Account -> EmailChangeRequest;
            # locking the request row first here would invert that order.
            acc_stmt = select(Account).where(Account.id == ctx.user.id).with_for_update()
            account = (await session.execute(acc_stmt)).scalar_one()

            # 2. Lock the active email change request row
            req_stmt = (
                select(EmailChangeRequest)
                .where(
                    EmailChangeRequest.account_id == ctx.user.id,
                    EmailChangeRequest.completed_at.is_(None),
                )
                .order_by(EmailChangeRequest.created_at.desc())
                .with_for_update()
            )
            change_req = (await session.execute(req_stmt)).scalar_one_or_none()

            if not change_req:
                return error_response(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="NO_ACTIVE_EMAIL_CHANGE",
                    message="No active email change request found. Please request a new code.",
                    request=request,
                )

            # 3. Check expiration
            expires_at = ensure_utc(change_req.expires_at)
            if expires_at and now > expires_at:
                change_req.completed_at = now
                return error_response(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="OTP_EXPIRED",
                    message="Verification code has expired. Please request a new code.",
                    request=request,
                )

            # 4. Check attempt limit
            if change_req.attempts >= 5:
                change_req.completed_at = now
                return error_response(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="OTP_ATTEMPTS_EXCEEDED",
                    message="Maximum verification attempts exceeded. Please request a new code.",
                    request=request,
                )

            # 5. Verify OTP hash
            if not verify_otp_hash(change_req.otp_hash, req.otp):
                change_req.attempts += 1
                if change_req.attempts >= 5:
                    change_req.completed_at = now
                    return error_response(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        code="OTP_ATTEMPTS_EXCEEDED",
                        message="Maximum verification attempts exceeded. Please request a new code.",
                        request=request,
                    )
                return error_response(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="INVALID_OTP",
                    message="Invalid verification code.",
                    request=request,
                )

            # 6. Re-check email uniqueness across accounts. Do not lock the other
            # account here: two accounts changing to each other's addresses would
            # otherwise form a cross-account lock cycle. The unique constraint on
            # accounts.email_normalized remains the final concurrency guard.
            dup_stmt = select(Account).where(
                Account.email_normalized == change_req.new_email_normalized,
                Account.id != account.id,
            )
            duplicate_account = (await session.execute(dup_stmt)).scalar_one_or_none()

            if duplicate_account:
                change_req.completed_at = now
                return error_response(
                    status_code=status.HTTP_409_CONFLICT,
                    code="EMAIL_ALREADY_IN_USE",
                    message="This email address is already registered to another account.",
                    request=request,
                )

            # 7. Apply email change
            account.email = change_req.new_email
            account.email_normalized = change_req.new_email_normalized
            account.email_verified_at = now

            change_req.verified_at = now
            change_req.completed_at = now
    except IntegrityError:
        # Two accounts can pass the SELECT-based uniqueness check concurrently when
        # no duplicate row exists yet. The unique constraint on accounts.email_normalized
        # is the final guard; map that conflict to the documented 409 instead of a 500.
        logger.warning(
            "Email change for account %s conflicted with a concurrent email update.",
            ctx.user.id,
        )
        return error_response(
            status_code=status.HTTP_409_CONFLICT,
            code="EMAIL_ALREADY_IN_USE",
            message="This email address is already registered to another account.",
            request=request,
        )

    logger.info("Email updated successfully for account %s to %s", ctx.user.id, account.email)

    avatar_url = (
        f"/uploads/images/{account.avatar_object_key}"
        if account.avatar_object_key
        else None
    )
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
            "avatar_url": avatar_url,
        },
    )


@app.post("/profile/change-password", tags=["Profile"], dependencies=[Depends(verify_csrf_header)])
async def change_password(
    req: ChangePasswordRequest,
    request: Request,
    ctx: AuthenticatedContext = Depends(get_current_session),
) -> Response:
    """Change account password, record history, and revoke other active sessions (ADR-003)."""
    now = datetime.now(UTC)

    # 1. Verify current password
    if not verify_password(ctx.user.password_hash, req.current_password):
        return error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_PASSWORD",
            message="Current password is incorrect.",
            request=request,
        )

    # 2. Prevent password reuse (identical to current password)
    if verify_password(ctx.user.password_hash, req.new_password):
        return error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="PASSWORD_REUSE_FORBIDDEN",
            message="New password cannot be the same as the current password.",
            request=request,
        )

    # 3. Hash new password and update account in atomic transaction
    new_hash = hash_password(req.new_password)

    async with user_db.transaction() as db_session:
        acc_query = select(Account).where(Account.id == ctx.user.id).with_for_update()
        account = (await db_session.execute(acc_query)).scalar_one()

        # Record old password in history before updating
        db_session.add(
            PasswordHistory(
                account_id=account.id,
                password_hash=account.password_hash,
                created_at=now,
            )
        )

        account.password_hash = new_hash
        account.password_changed_at = now

        # Revoke all OTHER active sessions for this account
        await db_session.execute(
            update(Session)
            .where(
                Session.account_id == account.id,
                Session.id != ctx.session.id,
                Session.revoked_at.is_(None),
            )
            .values(revoked_at=now, revocation_reason="password_changed")
        )

    logger.info(
        "Password successfully updated for user %s; other sessions revoked.",
        ctx.user.id,
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
                "avatar_url": (
                    f"/uploads/images/{account.avatar_object_key}"
                    if account.avatar_object_key
                    else None
                ),
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


@app.get(
    "/internal/users/{user_id}",
    tags=["Internal"],
    dependencies=[Depends(verify_internal_service_key)],
)
async def get_internal_user_contact(
    user_id: uuid.UUID,
    request: Request,
) -> JSONResponse:
    """Internal service-to-service endpoint to retrieve user contact details (DEC-002)."""
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
                "email": account.email,
                "role": account.role,
                "is_banned": account.is_banned,
            },
        )

