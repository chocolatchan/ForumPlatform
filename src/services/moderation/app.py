"""Moderation Service FastAPI application implementing abuse reports, moderation queue, actions, and audit trail."""

import base64
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import Depends, FastAPI, Header, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from src.common.errors import (
    error_response,
    register_canonical_error_handlers,
)
from src.common.logging import configure_logging
from src.services.moderation.auth import (
    ModerationUser,
    get_current_user,
    require_role,
    verify_csrf_header,
)
from src.services.moderation.config import moderation_settings
from src.services.moderation.database import moderation_db
from src.services.moderation.models import ModerationAction, ModerationOutboxEvent, Report
from src.services.moderation.schemas import (
    CreateReportRequest,
    ModerationActionRequest,
    ProactiveModerationActionRequest,
)

configure_logging(moderation_settings.service_name, moderation_settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    await moderation_db.close()


app = FastAPI(
    title="Moderation Service",
    version="1.1.0",
    lifespan=lifespan,
)

register_canonical_error_handlers(app)


# Inter-service HTTP clients for remote content and user mutations (DEC-002)
_content_http_client: httpx.AsyncClient | None = None
_user_http_client: httpx.AsyncClient | None = None


def get_content_http_client() -> httpx.AsyncClient:
    global _content_http_client
    if _content_http_client is None:
        _content_http_client = httpx.AsyncClient(timeout=5.0)
    return _content_http_client


def set_content_http_client(client: httpx.AsyncClient | None) -> None:
    global _content_http_client
    _content_http_client = client


def get_user_http_client() -> httpx.AsyncClient:
    global _user_http_client
    if _user_http_client is None:
        _user_http_client = httpx.AsyncClient(timeout=5.0)
    return _user_http_client


def set_user_http_client(client: httpx.AsyncClient | None) -> None:
    global _user_http_client
    _user_http_client = client


def encode_cursor(dt: datetime) -> str:
    """Encode datetime cursor into URL-safe base64 string."""
    return base64.urlsafe_b64encode(dt.isoformat().encode("utf-8")).decode("utf-8")


def decode_cursor(cursor_str: str | None) -> datetime | None:
    """Decode datetime cursor from URL-safe base64 string."""
    if not cursor_str:
        return None
    try:
        decoded_text = base64.urlsafe_b64decode(cursor_str.encode("utf-8")).decode("utf-8")
        dt = datetime.fromisoformat(decoded_text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return None


def serialize_report(r: Report) -> dict:
    target_id = r.thread_id or r.comment_id or r.reported_account_id or r.id
    created_at_str = r.created_at.isoformat() if r.created_at else datetime.now(UTC).isoformat()
    return {
        "id": str(r.id),
        "target_type": r.target_type,
        "target_id": str(target_id),
        "reason": r.reason,
        "details": r.details,
        "status": r.status,
        "resolution_note": r.resolution_note,
        "created_at": created_at_str,
    }


def serialize_action(a: ModerationAction) -> dict:
    created_at_str = a.created_at.isoformat() if a.created_at else datetime.now(UTC).isoformat()
    return {
        "id": str(a.id),
        "report_id": str(a.report_id) if a.report_id else None,
        "moderator_id": str(a.moderator_id),
        "action": a.action,
        "target_type": a.target_type,
        "target_id": str(a.target_id),
        "reason": a.reason,
        "created_at": created_at_str,
    }


# --- Health & Readiness ---
@app.get("/health/live", tags=["Health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["Health"])
async def readiness() -> JSONResponse:
    db_healthy = await moderation_db.ping()
    if not db_healthy:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "database": "unreachable"},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "ready", "database": "connected"},
    )


# =============================================================================
# Abuse Reports (Member & Public Workflows)
# =============================================================================
@app.post(
    "/reports",
    tags=["Reports"],
    dependencies=[Depends(get_current_user), Depends(verify_csrf_header)],
)
async def create_report(
    req: CreateReportRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: ModerationUser = Depends(get_current_user),
) -> JSONResponse:
    """Submit an abuse report against thread, comment, or account (DEC-004)."""
    # 1. Prevent self-reporting for accounts
    if req.target_type == "account" and req.target_id == current_user.id:
        return error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="SELF_REPORT_NOT_ALLOWED",
            message="You cannot report your own account.",
            request=request,
        )

    # 2. Verify target existence via owning service (DEC-002)
    req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    if req.target_type == "thread":
        content_client = get_content_http_client()
        url = f"{moderation_settings.content_service_url.rstrip('/')}/threads/{req.target_id}"
        try:
            resp = await content_client.get(url, headers={"X-Request-ID": req_id})
            if resp.status_code == 404:
                return error_response(
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="TARGET_NOT_FOUND",
                    message=f"Thread with ID '{req.target_id}' was not found.",
                    request=request,
                )
            elif resp.status_code != 200:
                return error_response(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="INVALID_TARGET",
                    message="Target thread cannot be verified.",
                    request=request,
                )
        except Exception as exc:
            return error_response(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="SERVICE_UNAVAILABLE",
                message=f"Unable to reach Content Service: {exc}",
                request=request,
            )

    elif req.target_type == "comment":
        content_client = get_content_http_client()
        url = f"{moderation_settings.content_service_url.rstrip('/')}/comments/{req.target_id}"
        try:
            resp = await content_client.get(url, headers={"X-Request-ID": req_id})
            if resp.status_code == 404:
                return error_response(
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="TARGET_NOT_FOUND",
                    message=f"Comment with ID '{req.target_id}' was not found.",
                    request=request,
                )
            elif resp.status_code != 200:
                return error_response(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="INVALID_TARGET",
                    message="Target comment cannot be verified.",
                    request=request,
                )
        except Exception as exc:
            return error_response(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="SERVICE_UNAVAILABLE",
                message=f"Unable to reach Content Service: {exc}",
                request=request,
            )

    elif req.target_type == "account":
        user_client = get_user_http_client()
        url = f"{moderation_settings.user_service_url.rstrip('/')}/users/{req.target_id}"
        try:
            resp = await user_client.get(url, headers={"X-Request-ID": req_id})
            if resp.status_code == 404:
                return error_response(
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="TARGET_NOT_FOUND",
                    message=f"User account with ID '{req.target_id}' was not found.",
                    request=request,
                )
            elif resp.status_code != 200:
                return error_response(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="INVALID_TARGET",
                    message="Target account cannot be verified.",
                    request=request,
                )
        except Exception as exc:
            return error_response(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="SERVICE_UNAVAILABLE",
                message=f"Unable to reach User Service: {exc}",
                request=request,
            )

    # 3. Check duplicate active report (status in pending, reviewing)
    async with moderation_db.transaction() as session:
        dup_query = select(Report).where(
            Report.reporter_id == current_user.id,
            Report.status.in_(["pending", "reviewing"]),
        )
        if req.target_type == "thread":
            dup_query = dup_query.where(Report.thread_id == req.target_id)
        elif req.target_type == "comment":
            dup_query = dup_query.where(Report.comment_id == req.target_id)
        elif req.target_type == "account":
            dup_query = dup_query.where(Report.reported_account_id == req.target_id)

        existing_active = (await session.execute(dup_query)).scalar_one_or_none()
        if existing_active:
            return error_response(
                status_code=status.HTTP_409_CONFLICT,
                code="DUPLICATE_REPORT",
                message="An active report for this content/account has already been submitted by you.",
                request=request,
            )

        new_report = Report(
            id=uuid.uuid4(),
            reporter_id=current_user.id,
            target_type=req.target_type,
            thread_id=req.target_id if req.target_type == "thread" else None,
            comment_id=req.target_id if req.target_type == "comment" else None,
            reported_account_id=req.target_id if req.target_type == "account" else None,
            reason=req.reason,
            details=req.details,
            status="pending",
        )
        session.add(new_report)

    return JSONResponse(status_code=status.HTTP_201_CREATED, content=serialize_report(new_report))


@app.get(
    "/reports/mine",
    tags=["Reports"],
    dependencies=[Depends(get_current_user)],
)
async def list_own_reports(
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    current_user: ModerationUser = Depends(get_current_user),
) -> JSONResponse:
    """List own submitted reports with cursor pagination."""
    cursor_dt = decode_cursor(cursor)

    async with moderation_db.session() as session:
        query = (
            select(Report)
            .where(Report.reporter_id == current_user.id)
            .order_by(Report.created_at.desc())
        )
        if cursor_dt is not None:
            query = query.where(Report.created_at < cursor_dt)

        query = query.limit(limit + 1)
        reports = (await session.execute(query)).scalars().all()

        next_cursor = None
        items_slice = reports
        if len(reports) > limit:
            items_slice = reports[:limit]
            next_cursor = encode_cursor(items_slice[-1].created_at)

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "items": [serialize_report(r) for r in items_slice],
                "next_cursor": next_cursor,
            },
        )


# =============================================================================
# Moderation Queue & Report Lifecycle (Moderator/Admin)
# =============================================================================
@app.get(
    "/moderation/reports",
    tags=["Moderation"],
    dependencies=[Depends(require_role("admin", "moderator"))],
)
async def list_moderation_reports(
    status_filter: str | None = Query(
        default=None, alias="status", enum=["pending", "reviewing", "actioned", "dismissed"]
    ),
    target_type: str | None = Query(default=None, enum=["thread", "comment", "account"]),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
) -> JSONResponse:
    """Moderation queue items with status/target filtering and cursor pagination (Moderator/Admin)."""
    cursor_dt = decode_cursor(cursor)

    async with moderation_db.session() as session:
        query = select(Report).order_by(Report.created_at.desc())
        if status_filter:
            query = query.where(Report.status == status_filter)
        if target_type:
            query = query.where(Report.target_type == target_type)
        if cursor_dt is not None:
            query = query.where(Report.created_at < cursor_dt)

        query = query.limit(limit + 1)
        reports = (await session.execute(query)).scalars().all()

        next_cursor = None
        items_slice = reports
        if len(reports) > limit:
            items_slice = reports[:limit]
            next_cursor = encode_cursor(items_slice[-1].created_at)

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "items": [serialize_report(r) for r in items_slice],
                "next_cursor": next_cursor,
            },
        )


@app.get(
    "/moderation/reports/{report_id}",
    tags=["Moderation"],
    dependencies=[Depends(require_role("admin", "moderator"))],
)
async def get_moderation_report(
    report_id: uuid.UUID,
    request: Request,
) -> JSONResponse:
    """Get report detail for review (Moderator/Admin)."""
    async with moderation_db.session() as session:
        query = select(Report).where(Report.id == report_id)
        report = (await session.execute(query)).scalar_one_or_none()
        if not report:
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="REPORT_NOT_FOUND",
                message=f"Report with ID '{report_id}' was not found.",
                request=request,
            )
        return JSONResponse(status_code=status.HTTP_200_OK, content=serialize_report(report))


@app.patch(
    "/moderation/reports/{report_id}/review",
    tags=["Moderation"],
    dependencies=[Depends(require_role("admin", "moderator")), Depends(verify_csrf_header)],
)
async def claim_or_review_report(
    report_id: uuid.UUID,
    request: Request,
    current_user: ModerationUser = Depends(require_role("admin", "moderator")),
) -> JSONResponse:
    """Transition report from pending to reviewing (claim report)."""
    async with moderation_db.transaction() as session:
        query = select(Report).where(Report.id == report_id).with_for_update()
        report = (await session.execute(query)).scalar_one_or_none()
        if not report:
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="REPORT_NOT_FOUND",
                message=f"Report with ID '{report_id}' was not found.",
                request=request,
            )

        if report.status != "pending":
            return error_response(
                status_code=status.HTTP_409_CONFLICT,
                code="INVALID_STATE_TRANSITION",
                message=f"Report is in '{report.status}' state and cannot transition to 'reviewing'.",
                request=request,
            )

        report.status = "reviewing"
        report.assigned_to = current_user.id

    return JSONResponse(status_code=status.HTTP_200_OK, content=serialize_report(report))


# Helper function to execute remote content/user actions via REST (DEC-002)
async def execute_remote_action(
    action: str,
    target_type: str,
    target_id: uuid.UUID,
    reason: str,
    metadata: dict,
    cookie_header: str | None,
    csrf_token: str | None,
    request_id: str,
) -> tuple[bool, str]:
    headers = {"X-Request-ID": request_id}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
    cookies = {moderation_settings.session_cookie_name: cookie_header} if cookie_header else {}

    if action in ("lock", "pin"):
        if target_type != "thread":
            return False, f"Action '{action}' is only valid for thread targets."
        content_client = get_content_http_client()
        url = f"{moderation_settings.content_service_url.rstrip('/')}/threads/{target_id}/{action}"
        body_field = "is_locked" if action == "lock" else "is_pinned"
        value = metadata.get(body_field, True)
        try:
            resp = await content_client.put(
                url, json={body_field: value}, headers=headers, cookies=cookies
            )
            if resp.status_code == 200:
                return True, "OK"
            return False, f"Remote content service returned {resp.status_code}: {resp.text}"
        except Exception as exc:
            return False, f"Remote connection error: {exc}"

    elif action == "soft_delete":
        if target_type == "thread":
            content_client = get_content_http_client()
            url = f"{moderation_settings.content_service_url.rstrip('/')}/threads/{target_id}"
            try:
                resp = await content_client.delete(url, headers=headers, cookies=cookies)
                if resp.status_code in (200, 204):
                    return True, "OK"
                return False, f"Remote content service returned {resp.status_code}: {resp.text}"
            except Exception as exc:
                return False, f"Remote connection error: {exc}"
        elif target_type == "comment":
            content_client = get_content_http_client()
            url = f"{moderation_settings.content_service_url.rstrip('/')}/comments/{target_id}"
            try:
                resp = await content_client.delete(url, headers=headers, cookies=cookies)
                if resp.status_code in (200, 204):
                    return True, "OK"
                return False, f"Remote content service returned {resp.status_code}: {resp.text}"
            except Exception as exc:
                return False, f"Remote connection error: {exc}"
        else:
            return False, f"soft_delete is not supported for target_type '{target_type}'"

    elif action in ("ban", "unban"):
        if target_type != "account":
            return False, f"Action '{action}' is only valid for account targets."
        user_client = get_user_http_client()
        url = f"{moderation_settings.user_service_url.rstrip('/')}/admin/users/{target_id}/ban"
        is_banned = action == "ban"
        try:
            resp = await user_client.put(
                url,
                json={"is_banned": is_banned, "ban_reason": reason},
                headers=headers,
                cookies=cookies,
            )
            if resp.status_code == 200:
                return True, "OK"
            return False, f"Remote user service returned {resp.status_code}: {resp.text}"
        except Exception as exc:
            return False, f"Remote connection error: {exc}"

    elif action == "role_change":
        if target_type != "account":
            return False, f"Action '{action}' is only valid for account targets."
        new_role = metadata.get("role")
        if not new_role:
            return False, "Metadata missing 'role' field for role_change."
        user_client = get_user_http_client()
        url = f"{moderation_settings.user_service_url.rstrip('/')}/admin/users/{target_id}/role"
        try:
            resp = await user_client.patch(
                url, json={"role": new_role}, headers=headers, cookies=cookies
            )
            if resp.status_code == 200:
                return True, "OK"
            return False, f"Remote user service returned {resp.status_code}: {resp.text}"
        except Exception as exc:
            return False, f"Remote connection error: {exc}"

    elif action == "move_category":
        # Category move supported via thread update in Content Service
        if target_type != "thread":
            return False, "Action 'move_category' is only valid for thread targets."
        new_cat_id = metadata.get("category_id")
        if not new_cat_id:
            return False, "Metadata missing 'category_id' for move_category."
        content_client = get_content_http_client()
        url = f"{moderation_settings.content_service_url.rstrip('/')}/threads/{target_id}"
        try:
            resp = await content_client.patch(
                url, json={"category_id": new_cat_id}, headers=headers, cookies=cookies
            )
            if resp.status_code == 200:
                return True, "OK"
            return False, f"Remote content service returned {resp.status_code}: {resp.text}"
        except Exception as exc:
            return False, f"Remote connection error: {exc}"

    return False, f"Unknown or unsupported action '{action}'"


@app.post(
    "/moderation/reports/{report_id}/actions",
    tags=["Moderation"],
    dependencies=[Depends(require_role("admin", "moderator")), Depends(verify_csrf_header)],
)
async def apply_report_moderation_action(
    report_id: uuid.UUID,
    req: ModerationActionRequest,
    request: Request,
    current_user: ModerationUser = Depends(require_role("admin", "moderator")),
) -> JSONResponse:
    """Apply moderation action to resolve report (Moderator/Admin)."""
    req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    cookie_token = request.cookies.get(moderation_settings.session_cookie_name)
    csrf_token = request.headers.get("X-CSRF-Token")

    now = datetime.now(UTC)

    async with moderation_db.transaction() as session:
        query = select(Report).where(Report.id == report_id).with_for_update()
        report = (await session.execute(query)).scalar_one_or_none()
        if not report:
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="REPORT_NOT_FOUND",
                message=f"Report with ID '{report_id}' was not found.",
                request=request,
            )

        if report.status in ("actioned", "dismissed"):
            return error_response(
                status_code=status.HTTP_409_CONFLICT,
                code="REPORT_ALREADY_FINALIZED",
                message=f"Report has already been finalized with status '{report.status}'.",
                request=request,
            )

        target_id = report.thread_id or report.comment_id or report.reported_account_id or report.id

        # 1. Dismiss report without executing external action
        if req.action == "dismiss_report":
            report.status = "dismissed"
            report.resolution_note = req.reason
            report.resolved_at = now
            report.assigned_to = current_user.id

            action_entity = ModerationAction(
                id=uuid.uuid4(),
                report_id=report.id,
                moderator_id=current_user.id,
                action=req.action,
                target_type=report.target_type,
                target_id=target_id,
                reason=req.reason,
                metadata_={**req.metadata, "outcome": "success"},
            )
            session.add(action_entity)

            # Write outbox event
            outbox = ModerationOutboxEvent(
                id=uuid.uuid4(),
                event_type="ModerationActionApplied",
                aggregate_type="Report",
                aggregate_id=report.id,
                payload={
                    "action_id": str(action_entity.id),
                    "report_id": str(report.id),
                    "moderator_id": str(current_user.id),
                    "action": req.action,
                    "target_type": report.target_type,
                    "target_id": str(target_id),
                    "reason": req.reason,
                    "status": "dismissed",
                },
            )
            session.add(outbox)

            return JSONResponse(
                status_code=status.HTTP_200_OK, content=serialize_action(action_entity)
            )

        # 2. Remote enforcement action
        remote_success, err_msg = await execute_remote_action(
            action=req.action,
            target_type=report.target_type,
            target_id=target_id,
            reason=req.reason,
            metadata=req.metadata,
            cookie_header=cookie_token,
            csrf_token=csrf_token,
            request_id=req_id,
        )

        if not remote_success:
            # Audit failed attempt in actions log without marking report actioned
            action_entity = ModerationAction(
                id=uuid.uuid4(),
                report_id=report.id,
                moderator_id=current_user.id,
                action=req.action,
                target_type=report.target_type,
                target_id=target_id,
                reason=req.reason,
                metadata_={**req.metadata, "outcome": "failed", "error": err_msg},
            )
            session.add(action_entity)
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="ACTION_FAILED",
                message=f"Moderation action could not be executed: {err_msg}",
                request=request,
            )

        # 3. Successful remote action -> mark report actioned
        report.status = "actioned"
        report.resolution_note = req.reason
        report.resolved_at = now
        report.assigned_to = current_user.id

        action_entity = ModerationAction(
            id=uuid.uuid4(),
            report_id=report.id,
            moderator_id=current_user.id,
            action=req.action,
            target_type=report.target_type,
            target_id=target_id,
            reason=req.reason,
            metadata_={**req.metadata, "outcome": "success"},
        )
        session.add(action_entity)

        # Outbox event
        outbox = ModerationOutboxEvent(
            id=uuid.uuid4(),
            event_type="ModerationActionApplied",
            aggregate_type="Report",
            aggregate_id=report.id,
            payload={
                "action_id": str(action_entity.id),
                "report_id": str(report.id),
                "moderator_id": str(current_user.id),
                "action": req.action,
                "target_type": report.target_type,
                "target_id": str(target_id),
                "reason": req.reason,
                "status": "actioned",
            },
        )
        session.add(outbox)

    return JSONResponse(status_code=status.HTTP_200_OK, content=serialize_action(action_entity))


@app.post(
    "/moderation/actions",
    tags=["Moderation"],
    dependencies=[Depends(require_role("admin", "moderator")), Depends(verify_csrf_header)],
)
async def apply_proactive_moderation_action(
    req: ProactiveModerationActionRequest,
    request: Request,
    current_user: ModerationUser = Depends(require_role("admin", "moderator")),
) -> JSONResponse:
    """Proactive moderation action without report (Moderator/Admin)."""
    req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    cookie_token = request.cookies.get(moderation_settings.session_cookie_name)
    csrf_token = request.headers.get("X-CSRF-Token")

    remote_success, err_msg = await execute_remote_action(
        action=req.action,
        target_type=req.target_type,
        target_id=req.target_id,
        reason=req.reason,
        metadata=req.metadata,
        cookie_header=cookie_token,
        csrf_token=csrf_token,
        request_id=req_id,
    )

    async with moderation_db.transaction() as session:
        if not remote_success:
            action_entity = ModerationAction(
                id=uuid.uuid4(),
                report_id=None,
                moderator_id=current_user.id,
                action=req.action,
                target_type=req.target_type,
                target_id=req.target_id,
                reason=req.reason,
                metadata_={**req.metadata, "outcome": "failed", "error": err_msg},
            )
            session.add(action_entity)
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="ACTION_FAILED",
                message=f"Moderation action could not be executed: {err_msg}",
                request=request,
            )

        action_entity = ModerationAction(
            id=uuid.uuid4(),
            report_id=None,
            moderator_id=current_user.id,
            action=req.action,
            target_type=req.target_type,
            target_id=req.target_id,
            reason=req.reason,
            metadata_={**req.metadata, "outcome": "success"},
        )
        session.add(action_entity)

        outbox = ModerationOutboxEvent(
            id=uuid.uuid4(),
            event_type="ModerationActionApplied",
            aggregate_type=req.target_type.capitalize(),
            aggregate_id=req.target_id,
            payload={
                "action_id": str(action_entity.id),
                "report_id": None,
                "moderator_id": str(current_user.id),
                "action": req.action,
                "target_type": req.target_type,
                "target_id": str(req.target_id),
                "reason": req.reason,
            },
        )
        session.add(outbox)

    return JSONResponse(status_code=status.HTTP_200_OK, content=serialize_action(action_entity))


@app.get(
    "/moderation/actions",
    tags=["Moderation"],
    dependencies=[Depends(require_role("admin", "moderator"))],
)
async def list_moderation_actions(
    target_type: str | None = Query(default=None, enum=["thread", "comment", "account"]),
    target_id: uuid.UUID | None = None,
    moderator_id: uuid.UUID | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
) -> JSONResponse:
    """Audit log of moderation actions with filtering and cursor pagination (Moderator/Admin)."""
    cursor_dt = decode_cursor(cursor)

    async with moderation_db.session() as session:
        query = select(ModerationAction).order_by(ModerationAction.created_at.desc())
        if target_type:
            query = query.where(ModerationAction.target_type == target_type)
        if target_id:
            query = query.where(ModerationAction.target_id == target_id)
        if moderator_id:
            query = query.where(ModerationAction.moderator_id == moderator_id)
        if cursor_dt is not None:
            query = query.where(ModerationAction.created_at < cursor_dt)

        query = query.limit(limit + 1)
        actions = (await session.execute(query)).scalars().all()

        next_cursor = None
        items_slice = actions
        if len(actions) > limit:
            items_slice = actions[:limit]
            next_cursor = encode_cursor(items_slice[-1].created_at)

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "items": [serialize_action(a) for a in items_slice],
                "next_cursor": next_cursor,
            },
        )
