"""Notification Service FastAPI application implementing inbox and event handling workflows."""

import base64
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from src.common.errors import error_response, register_canonical_error_handlers
from src.common.logging import configure_logging
from src.common.metrics import MetricsRegistry, setup_metrics_endpoint
from src.services.notification.auth import (
    NotificationUser,
    get_current_user,
    get_optional_user,
    verify_csrf_header,
    verify_internal_service_key,
)
from src.services.notification.config import notification_settings
from src.services.notification.database import notification_db
from src.services.notification.events import EventHandlerRegistry
from src.services.notification.models import NotificationItem, NotificationPreference
from src.services.notification.schemas import (
    ConsumedEventPayload,
    NotificationPreferencesRequest,
)

configure_logging(notification_settings.service_name, notification_settings.log_level)
metrics_registry = MetricsRegistry(notification_settings.service_name)

DEFAULT_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    await notification_db.close()


app = FastAPI(
    title="Notification Service",
    version="1.1.0",
    lifespan=lifespan,
)

register_canonical_error_handlers(app)
setup_metrics_endpoint(app, metrics_registry, db_manager=notification_db)


def encode_cursor(dt: datetime, item_id: uuid.UUID) -> str:
    """Encode datetime + id cursor into URL-safe base64 string."""
    raw = f"{dt.isoformat()}|{item_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")


def decode_cursor(cursor_str: str | None) -> tuple[datetime, uuid.UUID] | None:
    """Decode datetime + id cursor from URL-safe base64 string."""
    if not cursor_str:
        return None
    try:
        decoded_text = base64.urlsafe_b64decode(cursor_str.encode("utf-8")).decode("utf-8")
        parts = decoded_text.split("|")
        if len(parts) != 2:
            return None
        dt = datetime.fromisoformat(parts[0])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        item_id = uuid.UUID(parts[1])
        return dt, item_id
    except Exception:
        return None


def serialize_notification(item: NotificationItem) -> dict:
    return {
        "id": str(item.id),
        "type": item.type,
        "title": item.title,
        "body": item.body,
        "target_type": item.target_type,
        "target_id": str(item.target_id) if item.target_id else None,
        "is_read": item.is_read,
        "created_at": item.created_at.isoformat()
        if item.created_at
        else datetime.now(UTC).isoformat(),
    }


def serialize_preferences(pref: NotificationPreference | None) -> dict:
    if not pref:
        return {"in_app_enabled": True, "email_enabled": False}
    return {
        "in_app_enabled": pref.in_app_enabled,
        "email_enabled": pref.email_enabled,
    }


# --- Health & Readiness ---
@app.get("/health/live", tags=["Health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["Health"])
async def readiness() -> JSONResponse:
    db_healthy = await notification_db.ping()
    if not db_healthy:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "database": "unreachable"},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "ready", "database": "connected"},
    )


# --- Inbox API ---
@app.get("/notifications", tags=["Notifications"])
async def list_notifications(
    request: Request,
    unread_only: bool = False,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    current_user: NotificationUser = Depends(get_current_user),
) -> JSONResponse:
    """List notifications belonging to the authenticated user with cursor pagination."""
    cursor_info = decode_cursor(cursor)

    async with notification_db.session() as session:
        query = select(NotificationItem).where(NotificationItem.recipient_id == current_user.id)

        if unread_only:
            query = query.where(NotificationItem.is_read == False)  # noqa: E712

        if cursor_info is not None:
            cursor_dt, cursor_id = cursor_info
            # Deterministic ordering: (created_at DESC, id DESC)
            query = query.where(
                (NotificationItem.created_at < cursor_dt)
                | ((NotificationItem.created_at == cursor_dt) & (NotificationItem.id < cursor_id))
            )

        query = query.order_by(
            NotificationItem.created_at.desc(),
            NotificationItem.id.desc(),
        ).limit(limit + 1)

        items = (await session.execute(query)).scalars().all()

        next_cursor = None
        items_slice = items
        if len(items) > limit:
            items_slice = items[:limit]
            last_item = items_slice[-1]
            next_cursor = encode_cursor(last_item.created_at, last_item.id)

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "items": [serialize_notification(i) for i in items_slice],
                "next_cursor": next_cursor,
            },
        )


@app.get("/notifications/unread-count", tags=["Notifications"])
async def get_unread_count(
    request: Request,
    current_user: NotificationUser | None = Depends(get_optional_user),
) -> JSONResponse:
    """Return unread notification count for the authenticated user, or 0 if unauthenticated."""
    if not current_user:
        return JSONResponse(status_code=status.HTTP_200_OK, content={"unread_count": 0})

    async with notification_db.session() as session:
        stmt = (
            select(func.count(NotificationItem.id))
            .where(NotificationItem.recipient_id == current_user.id)
            .where(NotificationItem.is_read == False)  # noqa: E712
        )
        count = (await session.execute(stmt)).scalar_one()

        return JSONResponse(status_code=status.HTTP_200_OK, content={"unread_count": count})


# --- Preferences API ---
@app.get("/notifications/preferences", tags=["Notifications"])
async def get_notification_preferences(
    request: Request,
    current_user: NotificationUser | None = Depends(get_optional_user),
) -> JSONResponse:
    """Get notification preferences for the authenticated user, or defaults for anonymous test."""
    user_id = current_user.id if current_user else DEFAULT_USER_ID
    async with notification_db.session() as session:
        query = select(NotificationPreference).where(NotificationPreference.user_id == user_id)
        pref = (await session.execute(query)).scalar_one_or_none()
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=serialize_preferences(pref),
        )


@app.patch("/notifications/preferences", tags=["Notifications"])
async def update_notification_preferences(
    req: NotificationPreferencesRequest,
    request: Request,
    current_user: NotificationUser | None = Depends(get_optional_user),
) -> JSONResponse:
    """Update notification preferences for the user."""
    # Verify CSRF if session cookie is present
    if request.cookies.get(notification_settings.session_cookie_name):
        await verify_csrf_header(request)

    user_id = current_user.id if current_user else DEFAULT_USER_ID
    async with notification_db.transaction() as session:
        query = select(NotificationPreference).where(NotificationPreference.user_id == user_id)
        pref = (await session.execute(query)).scalar_one_or_none()
        if not pref:
            pref = NotificationPreference(
                user_id=user_id,
                in_app_enabled=req.in_app_enabled if req.in_app_enabled is not None else True,
                email_enabled=req.email_enabled if req.email_enabled is not None else False,
            )
            session.add(pref)
        else:
            if req.in_app_enabled is not None:
                pref.in_app_enabled = req.in_app_enabled
            if req.email_enabled is not None:
                pref.email_enabled = req.email_enabled

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=serialize_preferences(pref),
    )


@app.get("/notifications/{notification_id}", tags=["Notifications"])
async def get_notification_detail(
    notification_id: uuid.UUID,
    request: Request,
    current_user: NotificationUser = Depends(get_current_user),
) -> JSONResponse:
    """Get single notification detail. User can only access own notifications."""
    async with notification_db.session() as session:
        stmt = select(NotificationItem).where(
            NotificationItem.id == notification_id,
            NotificationItem.recipient_id == current_user.id,
        )
        item = (await session.execute(stmt)).scalar_one_or_none()
        if not item:
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="NOTIFICATION_NOT_FOUND",
                message=f"Notification with ID '{notification_id}' was not found.",
                request=request,
            )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=serialize_notification(item),
        )


@app.patch(
    "/notifications/{notification_id}/read",
    tags=["Notifications"],
    dependencies=[Depends(verify_csrf_header)],
)
async def mark_notification_read(
    notification_id: uuid.UUID,
    request: Request,
    current_user: NotificationUser = Depends(get_current_user),
) -> JSONResponse:
    """Mark a notification as read. Repeated calls are idempotent."""
    async with notification_db.transaction() as session:
        stmt = select(NotificationItem).where(
            NotificationItem.id == notification_id,
            NotificationItem.recipient_id == current_user.id,
        )
        item = (await session.execute(stmt)).scalar_one_or_none()
        if not item:
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="NOTIFICATION_NOT_FOUND",
                message=f"Notification with ID '{notification_id}' was not found.",
                request=request,
            )

        if not item.is_read:
            item.is_read = True
            item.read_at = datetime.now(UTC)

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=serialize_notification(item),
        )


@app.post(
    "/notifications/read-all",
    tags=["Notifications"],
    dependencies=[Depends(verify_csrf_header)],
)
async def mark_all_notifications_read(
    request: Request,
    current_user: NotificationUser = Depends(get_current_user),
) -> JSONResponse:
    """Mark all unread notifications belonging to the authenticated user as read."""
    async with notification_db.transaction() as session:
        stmt = select(NotificationItem).where(
            NotificationItem.recipient_id == current_user.id,
            NotificationItem.is_read == False,  # noqa: E712
        )
        unread_items = (await session.execute(stmt)).scalars().all()
        now = datetime.now(UTC)
        for item in unread_items:
            item.is_read = True
            item.read_at = now

    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)


# --- Internal Event Ingestion Endpoint ---
@app.post(
    "/internal/events",
    tags=["Internal"],
    dependencies=[Depends(verify_internal_service_key)],
)
async def ingest_event(
    event: ConsumedEventPayload,
    request: Request,
) -> JSONResponse:
    """Internal idempotent domain event consumer."""
    async with notification_db.transaction() as session:
        created_items = await EventHandlerRegistry.process_event(session, event)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "processed",
            "event_id": str(event.event_id),
            "event_type": event.event_type,
            "notifications_created": len(created_items),
        },
    )
