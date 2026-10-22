"""Content Service FastAPI application implementing core discussion and content workflows."""

import asyncio
import base64
import contextlib
import logging
import os
import re
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.common.errors import (
    error_response,
    register_canonical_error_handlers,
)
from src.common.logging import configure_logging
from src.common.metrics import MetricsRegistry, setup_metrics_endpoint
from src.common.outbox_worker import OutboxWorker
from src.services.content.auth import (
    ContentUser,
    get_current_user,
    get_optional_current_user,
    require_role,
    verify_csrf_header,
    verify_internal_service_key,
)
from src.services.content.config import content_settings
from src.services.content.database import content_db
from src.services.content.models import (
    Attachment,
    Category,
    Comment,
    ConsumedEvent,
    ContentOutboxEvent,
    Thread,
    ThreadTag,
)
from src.services.content.schemas import (
    AttachmentResponse,
    BestAnswerRequest,
    CreateCategoryRequest,
    CreateCommentRequest,
    CreateThreadRequest,
    EditCommentRequest,
    EditThreadRequest,
    LockThreadRequest,
    PinThreadRequest,
    UpdateCategoryRequest,
)
from src.services.content.storage import (
    MAX_FILE_SIZE_BYTES,
    SAFE_OBJECT_KEY_PATTERN,
    ensure_storage_dir,
    generate_object_key,
    get_absolute_file_path,
    sniff_image_type,
    validate_and_normalize_image,
)

configure_logging(content_settings.service_name, content_settings.log_level)
metrics_registry = MetricsRegistry(content_settings.service_name)

outbox_worker = OutboxWorker(
    service_name="content-service",
    db_manager=content_db,
    model_class=ContentOutboxEvent,
    service_urls=content_settings.get_service_urls(),
    internal_key=content_settings.internal_service_secret,
    poll_interval_seconds=1.0,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await outbox_worker.start()
    cleanup_task = asyncio.create_task(
        _pending_attachment_cleanup_loop(), name="pending-attachment-cleanup"
    )
    yield
    cleanup_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cleanup_task
    await outbox_worker.stop()
    await content_db.close()


app = FastAPI(
    title="Content Service",
    version="1.1.0",
    lifespan=lifespan,
)

register_canonical_error_handlers(app)
setup_metrics_endpoint(app, metrics_registry, db_manager=content_db, outbox_worker=outbox_worker)


# --- Helpers ---
def normalize_tag(tag: str) -> str:
    """Normalize tag for case-insensitive deduplication and lookup."""
    return re.sub(r"[^a-z0-9\-]", "", tag.strip().lower())


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


def serialize_category(c: Category) -> dict:
    return {
        "id": str(c.id),
        "name": c.name,
        "slug": c.slug,
        "description": c.description,
    }


def serialize_thread(t: Thread, explicit_tags: list[ThreadTag] | None = None) -> dict:
    if explicit_tags is not None:
        tags = [tag_rel.tag for tag_rel in explicit_tags]
    else:
        tags = [tag_rel.tag for tag_rel in t.tags] if hasattr(t, "tags") and t.tags else []
    return {
        "id": str(t.id),
        "author_id": str(t.author_id),
        "category_id": str(t.category_id),
        "title": t.title,
        "body": t.body,
        "tags": tags,
        "status": t.status,
        "is_pinned": t.is_pinned,
        "is_locked": t.is_locked,
        "best_answer_comment_id": str(t.best_answer_comment_id)
        if t.best_answer_comment_id
        else None,
        "view_count": t.view_count,
        "created_at": t.created_at.isoformat() if t.created_at else datetime.now(UTC).isoformat(),
        "updated_at": t.updated_at.isoformat() if t.updated_at else datetime.now(UTC).isoformat(),
    }


def serialize_comment(c: Comment) -> dict:
    return {
        "id": str(c.id),
        "thread_id": str(c.thread_id),
        "author_id": str(c.author_id),
        "parent_comment_id": str(c.parent_comment_id) if c.parent_comment_id else None,
        "body": "[deleted]" if c.is_deleted else c.body,
        "is_deleted": c.is_deleted,
        "created_at": c.created_at.isoformat() if c.created_at else datetime.now(UTC).isoformat(),
    }


# --- Health & Readiness ---
@app.get("/health/live", tags=["Health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["Health"])
async def readiness() -> JSONResponse:
    db_healthy = await content_db.ping()
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
# Categories
# =============================================================================
@app.get("/categories", tags=["Categories"])
async def list_categories() -> dict[str, list[dict]]:
    """Return category list matching the Category OpenAPI schema."""
    async with content_db.session() as session:
        query = (
            select(Category)
            .where(Category.is_hidden.is_(False))
            .order_by(Category.sort_order.asc(), Category.name.asc())
        )
        categories = (await session.execute(query)).scalars().all()
        return {"items": [serialize_category(c) for c in categories]}


@app.get("/categories/{category_id}", tags=["Categories"])
async def get_category(category_id: uuid.UUID, request: Request) -> JSONResponse:
    async with content_db.session() as session:
        query = select(Category).where(Category.id == category_id)
        cat = (await session.execute(query)).scalar_one_or_none()
        if not cat:
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CATEGORY_NOT_FOUND",
                message=f"Category with ID '{category_id}' was not found.",
                request=request,
            )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=serialize_category(cat),
        )


@app.post(
    "/admin/categories",
    tags=["Categories"],
    dependencies=[Depends(require_role("admin")), Depends(verify_csrf_header)],
)
async def admin_create_category(
    req: CreateCategoryRequest,
    request: Request,
    current_user: ContentUser = Depends(require_role("admin")),
) -> JSONResponse:
    """Create a new category (Admin only)."""
    norm_slug = req.slug.strip().lower()
    async with content_db.transaction() as session:
        # Check slug uniqueness
        query = select(Category).where(Category.slug == norm_slug)
        existing = (await session.execute(query)).scalar_one_or_none()
        if existing:
            return error_response(
                status_code=status.HTTP_409_CONFLICT,
                code="CATEGORY_SLUG_CONFLICT",
                message=f"Category with slug '{norm_slug}' already exists.",
                request=request,
            )

        new_cat = Category(
            id=uuid.uuid4(),
            name=req.name.strip(),
            slug=norm_slug,
            description=req.description,
            sort_order=req.sort_order,
            created_by=current_user.id,
        )
        session.add(new_cat)

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=serialize_category(new_cat),
    )


@app.patch(
    "/admin/categories/{category_id}",
    tags=["Categories"],
    dependencies=[Depends(require_role("admin")), Depends(verify_csrf_header)],
)
async def admin_update_category(
    category_id: uuid.UUID,
    req: UpdateCategoryRequest,
    request: Request,
) -> JSONResponse:
    """Update category properties (Admin only)."""
    async with content_db.transaction() as session:
        query = select(Category).where(Category.id == category_id)
        cat = (await session.execute(query)).scalar_one_or_none()
        if not cat:
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CATEGORY_NOT_FOUND",
                message=f"Category with ID '{category_id}' was not found.",
                request=request,
            )

        if req.slug is not None:
            norm_slug = req.slug.strip().lower()
            if norm_slug != cat.slug:
                conflict_query = select(Category).where(
                    Category.slug == norm_slug, Category.id != category_id
                )
                existing = (await session.execute(conflict_query)).scalar_one_or_none()
                if existing:
                    return error_response(
                        status_code=status.HTTP_409_CONFLICT,
                        code="CATEGORY_SLUG_CONFLICT",
                        message=f"Category with slug '{norm_slug}' already exists.",
                        request=request,
                    )
                cat.slug = norm_slug

        if req.name is not None:
            cat.name = req.name.strip()
        if req.description is not None:
            cat.description = req.description
        if req.sort_order is not None:
            cat.sort_order = req.sort_order
        if req.is_hidden is not None:
            cat.is_hidden = req.is_hidden

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=serialize_category(cat),
    )


# =============================================================================
# Threads
# =============================================================================
@app.get("/threads", tags=["Threads"])
async def list_threads(
    request: Request,
    category_id: uuid.UUID | None = None,
    tag: str | None = None,
    sort: Literal["newest", "most_viewed", "most_voted"] = Query(default="newest"),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
) -> JSONResponse:
    """List published threads with optional category/tag filters and cursor pagination."""
    cursor_dt = decode_cursor(cursor)

    async with content_db.session() as session:
        query = (
            select(Thread).options(selectinload(Thread.tags)).where(Thread.status == "published")
        )

        if category_id is not None:
            query = query.where(Thread.category_id == category_id)

        if tag is not None:
            norm_tag = normalize_tag(tag)
            query = query.join(Thread.tags).where(ThreadTag.tag_normalized == norm_tag)

        # Pagination & sorting
        if sort == "most_viewed":
            query = query.order_by(
                Thread.is_pinned.desc(), Thread.view_count.desc(), Thread.created_at.desc()
            )
            if cursor_dt is not None:
                query = query.where(Thread.created_at < cursor_dt)
        else:  # newest / default
            query = query.order_by(Thread.is_pinned.desc(), Thread.created_at.desc())
            if cursor_dt is not None:
                query = query.where(Thread.created_at < cursor_dt)

        query = query.limit(limit + 1)
        results = (await session.execute(query)).scalars().all()

        has_next = len(results) > limit
        items = results[:limit]

        next_cursor = None
        if has_next and items:
            next_cursor = encode_cursor(items[-1].created_at)

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "items": [serialize_thread(t) for t in items],
                "next_cursor": next_cursor,
            },
        )


@app.post(
    "/threads",
    tags=["Threads"],
    dependencies=[Depends(get_current_user), Depends(verify_csrf_header)],
)
async def create_thread(
    req: CreateThreadRequest,
    request: Request,
    current_user: ContentUser = Depends(get_current_user),
) -> JSONResponse:
    """Create a new thread with tags and outbox event."""
    async with content_db.transaction() as session:
        # Verify category exists
        cat_query = select(Category).where(
            Category.id == req.category_id, Category.is_hidden.is_(False)
        )
        cat = (await session.execute(cat_query)).scalar_one_or_none()
        if not cat:
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CATEGORY_NOT_FOUND",
                message=f"Category with ID '{req.category_id}' was not found.",
                request=request,
            )

        # Lock eligible attachment rows BEFORE creating the thread. This both
        # enforces a single lock order (Attachment.id) across concurrent
        # requests and ensures an invalid attachment cannot leave behind a
        # half-created thread when the handler returns an error response.
        linked_attachment_ids: list[uuid.UUID] = []
        locked_attachments: list[Attachment] = []
        if req.image_ids:
            # Deterministically sort IDs to prevent deadlocks across concurrent requests
            sorted_image_ids = sorted(req.image_ids)
            att_stmt = (
                select(Attachment)
                .where(
                    Attachment.id.in_(sorted_image_ids),
                    Attachment.author_id == current_user.id,
                    Attachment.status == "pending",
                )
                .order_by(Attachment.id)
                .with_for_update()
            )
            locked_attachments = list((await session.execute(att_stmt)).scalars().all())
            if len(locked_attachments) != len(sorted_image_ids):
                return error_response(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="INVALID_ATTACHMENT",
                    message="One or more attachments are invalid, do not belong to you, or have already been linked.",
                    request=request,
                )

        new_thread = Thread(
            id=uuid.uuid4(),
            author_id=current_user.id,
            category_id=req.category_id,
            title=req.title.strip(),
            body=req.body.strip(),
            status="published",
            is_pinned=False,
            is_locked=False,
            view_count=0,
        )
        session.add(new_thread)
        await session.flush()

        # Handle tags
        tag_objects: list[ThreadTag] = []
        seen_tags = set()
        for t in req.tags:
            cleaned_tag = t.strip()
            norm = normalize_tag(cleaned_tag)
            if norm and norm not in seen_tags:
                seen_tags.add(norm)
                tag_obj = ThreadTag(
                    id=uuid.uuid4(),
                    thread_id=new_thread.id,
                    tag=cleaned_tag[:40],
                    tag_normalized=norm[:40],
                )
                session.add(tag_obj)
                tag_objects.append(tag_obj)

        await session.flush()

        # Associate the already-locked attachments with the new thread.
        for att in locked_attachments:
            att.thread_id = new_thread.id
            att.status = "linked"
            att.linked_at = datetime.now(UTC)
            linked_attachment_ids.append(att.id)

        # Outbox event
        outbox = ContentOutboxEvent(
            id=uuid.uuid4(),
            event_type="ThreadCreated",
            aggregate_type="Thread",
            aggregate_id=new_thread.id,
            payload={
                "thread_id": str(new_thread.id),
                "author_id": str(new_thread.author_id),
                "category_id": str(new_thread.category_id),
                "title": new_thread.title,
                "status": new_thread.status,
                "tags": [t.tag for t in tag_objects],
                "image_ids": [str(att_id) for att_id in linked_attachment_ids],
            },
        )
        session.add(outbox)

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=serialize_thread(new_thread, tag_objects),
    )


@app.get("/threads/{thread_id}", tags=["Threads"])
async def get_thread(thread_id: uuid.UUID, request: Request) -> JSONResponse:
    """Get thread details by ID."""
    async with content_db.session() as session:
        query = select(Thread).options(selectinload(Thread.tags)).where(Thread.id == thread_id)
        thread = (await session.execute(query)).scalar_one_or_none()
        if not thread or thread.status in ("deleted", "hidden"):
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="THREAD_NOT_FOUND",
                message=f"Thread with ID '{thread_id}' was not found.",
                request=request,
            )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=serialize_thread(thread),
        )


@app.patch(
    "/threads/{thread_id}",
    tags=["Threads"],
    dependencies=[Depends(get_current_user), Depends(verify_csrf_header)],
)
async def edit_thread(
    thread_id: uuid.UUID,
    req: EditThreadRequest,
    request: Request,
    current_user: ContentUser = Depends(get_current_user),
) -> JSONResponse:
    """Edit own thread title, body, and tags."""
    async with content_db.transaction() as session:
        query = select(Thread).options(selectinload(Thread.tags)).where(Thread.id == thread_id)
        thread = (await session.execute(query)).scalar_one_or_none()
        if not thread or thread.status == "deleted":
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="THREAD_NOT_FOUND",
                message=f"Thread with ID '{thread_id}' was not found.",
                request=request,
            )

        # Author check or admin/mod
        if thread.author_id != current_user.id and current_user.role not in ("admin", "moderator"):
            return error_response(
                status_code=status.HTTP_403_FORBIDDEN,
                code="FORBIDDEN_THREAD_EDIT",
                message="You do not have permission to edit this thread.",
                request=request,
            )

        if req.title is not None:
            thread.title = req.title.strip()
        if req.body is not None:
            thread.body = req.body.strip()

        if req.tags is not None:
            # Delete old tags
            for old_tag in thread.tags:
                await session.delete(old_tag)
            await session.flush()

        tag_objects: list[ThreadTag] | None = None
        if req.tags is not None:
            tag_objects = []
            seen_tags = set()
            for t in req.tags:
                cleaned = t.strip()
                norm = normalize_tag(cleaned)
                if norm and norm not in seen_tags:
                    seen_tags.add(norm)
                    new_tag = ThreadTag(
                        id=uuid.uuid4(),
                        thread_id=thread.id,
                        tag=cleaned[:40],
                        tag_normalized=norm[:40],
                    )
                    session.add(new_tag)
                    tag_objects.append(new_tag)
            await session.flush()

        thread.edited_at = datetime.now(UTC)

        outbox = ContentOutboxEvent(
            id=uuid.uuid4(),
            event_type="ThreadUpdated",
            aggregate_type="Thread",
            aggregate_id=thread.id,
            payload={
                "thread_id": str(thread.id),
                "author_id": str(thread.author_id),
                "title": thread.title,
                "status": thread.status,
            },
        )
        session.add(outbox)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=serialize_thread(thread, tag_objects),
    )


@app.delete(
    "/threads/{thread_id}",
    tags=["Threads"],
    dependencies=[Depends(get_current_user), Depends(verify_csrf_header)],
)
async def delete_thread(
    thread_id: uuid.UUID,
    request: Request,
    current_user: ContentUser = Depends(get_current_user),
) -> Response:
    """Soft delete thread. ADR-008: If non-deleted comments exist, owner cannot delete."""
    async with content_db.transaction() as session:
        query = select(Thread).where(Thread.id == thread_id)
        thread = (await session.execute(query)).scalar_one_or_none()
        if not thread or thread.status == "deleted":
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="THREAD_NOT_FOUND",
                message=f"Thread with ID '{thread_id}' was not found.",
                request=request,
            )

        is_owner = thread.author_id == current_user.id
        is_mod = current_user.role in ("admin", "moderator")

        if not is_owner and not is_mod:
            return error_response(
                status_code=status.HTTP_403_FORBIDDEN,
                code="FORBIDDEN_THREAD_DELETE",
                message="You do not have permission to delete this thread.",
                request=request,
            )

        # ADR-008: Check if thread has comments or best answer if requester is owner
        if is_owner and not is_mod:
            if thread.best_answer_comment_id is not None:
                return error_response(
                    status_code=status.HTTP_409_CONFLICT,
                    code="THREAD_REQUIRES_MODERATION",
                    message="Threads with a selected best answer require moderator assistance to delete.",
                    request=request,
                )

            comment_count_query = select(func.count(Comment.id)).where(
                Comment.thread_id == thread_id, Comment.is_deleted.is_(False)
            )
            comment_count = (await session.execute(comment_count_query)).scalar_one()
            if comment_count > 0:
                return error_response(
                    status_code=status.HTTP_409_CONFLICT,
                    code="THREAD_REQUIRES_MODERATION",
                    message="Threads with active comments require moderator assistance to delete.",
                    request=request,
                )

        thread.status = "deleted"
        outbox = ContentOutboxEvent(
            id=uuid.uuid4(),
            event_type="ThreadDeleted",
            aggregate_type="Thread",
            aggregate_id=thread.id,
            payload={
                "thread_id": str(thread.id),
                "deleted_by": str(current_user.id),
                "role": current_user.role,
            },
        )
        session.add(outbox)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.put(
    "/threads/{thread_id}/lock",
    tags=["Threads"],
    dependencies=[Depends(require_role("admin", "moderator")), Depends(verify_csrf_header)],
)
async def lock_thread(
    thread_id: uuid.UUID,
    req: LockThreadRequest,
    request: Request,
) -> JSONResponse:
    """Lock or unlock a thread (Moderator/Admin only)."""
    async with content_db.transaction() as session:
        query = select(Thread).options(selectinload(Thread.tags)).where(Thread.id == thread_id)
        thread = (await session.execute(query)).scalar_one_or_none()
        if not thread or thread.status == "deleted":
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="THREAD_NOT_FOUND",
                message=f"Thread with ID '{thread_id}' was not found.",
                request=request,
            )

        thread.is_locked = req.is_locked
        outbox = ContentOutboxEvent(
            id=uuid.uuid4(),
            event_type="ThreadLockToggled",
            aggregate_type="Thread",
            aggregate_id=thread.id,
            payload={"thread_id": str(thread.id), "is_locked": thread.is_locked},
        )
        session.add(outbox)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=serialize_thread(thread),
    )


@app.put(
    "/threads/{thread_id}/pin",
    tags=["Threads"],
    dependencies=[Depends(require_role("admin", "moderator")), Depends(verify_csrf_header)],
)
async def pin_thread(
    thread_id: uuid.UUID,
    req: PinThreadRequest,
    request: Request,
) -> JSONResponse:
    """Pin or unpin a thread (Moderator/Admin only)."""
    async with content_db.transaction() as session:
        query = select(Thread).options(selectinload(Thread.tags)).where(Thread.id == thread_id)
        thread = (await session.execute(query)).scalar_one_or_none()
        if not thread or thread.status == "deleted":
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="THREAD_NOT_FOUND",
                message=f"Thread with ID '{thread_id}' was not found.",
                request=request,
            )

        thread.is_pinned = req.is_pinned
        outbox = ContentOutboxEvent(
            id=uuid.uuid4(),
            event_type="ThreadPinToggled",
            aggregate_type="Thread",
            aggregate_id=thread.id,
            payload={"thread_id": str(thread.id), "is_pinned": thread.is_pinned},
        )
        session.add(outbox)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=serialize_thread(thread),
    )


@app.put(
    "/threads/{thread_id}/best-answer",
    tags=["Threads"],
    dependencies=[Depends(get_current_user), Depends(verify_csrf_header)],
)
async def set_best_answer(
    thread_id: uuid.UUID,
    req: BestAnswerRequest,
    request: Request,
    current_user: ContentUser = Depends(get_current_user),
) -> JSONResponse:
    """Set best answer comment for a thread (Owner only)."""
    async with content_db.transaction() as session:
        query = select(Thread).options(selectinload(Thread.tags)).where(Thread.id == thread_id)
        thread = (await session.execute(query)).scalar_one_or_none()
        if not thread or thread.status == "deleted":
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="THREAD_NOT_FOUND",
                message=f"Thread with ID '{thread_id}' was not found.",
                request=request,
            )

        # Author check
        if thread.author_id != current_user.id:
            return error_response(
                status_code=status.HTTP_403_FORBIDDEN,
                code="FORBIDDEN_BEST_ANSWER",
                message="Only the author of the thread can select the best answer.",
                request=request,
            )

        # Verify comment belongs to thread and is not deleted
        comment_query = select(Comment).where(
            Comment.id == req.comment_id,
            Comment.thread_id == thread_id,
            Comment.is_deleted.is_(False),
        )
        comment = (await session.execute(comment_query)).scalar_one_or_none()
        if not comment:
            return error_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="INVALID_COMMENT",
                message="Selected comment does not belong to this thread or is deleted.",
                request=request,
            )

        thread.best_answer_comment_id = req.comment_id
        outbox = ContentOutboxEvent(
            id=uuid.uuid4(),
            event_type="BestAnswerChanged",
            aggregate_type="Thread",
            aggregate_id=thread.id,
            payload={
                "thread_id": str(thread.id),
                "comment_id": str(comment.id),
                "comment_author_id": str(comment.author_id),
            },
        )
        session.add(outbox)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=serialize_thread(thread),
    )


# =============================================================================
# Comments
# =============================================================================
@app.get("/threads/{thread_id}/comments", tags=["Comments"])
async def list_thread_comments(
    thread_id: uuid.UUID,
    request: Request,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
) -> JSONResponse:
    """List comments of a thread with cursor pagination."""
    cursor_dt = decode_cursor(cursor)

    async with content_db.session() as session:
        # Check thread exists
        thread_query = select(Thread).where(Thread.id == thread_id)
        thread = (await session.execute(thread_query)).scalar_one_or_none()
        if not thread or thread.status in ("deleted", "hidden"):
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="THREAD_NOT_FOUND",
                message=f"Thread with ID '{thread_id}' was not found.",
                request=request,
            )

        query = (
            select(Comment).where(Comment.thread_id == thread_id).order_by(Comment.created_at.asc())
        )

        if cursor_dt is not None:
            query = query.where(Comment.created_at > cursor_dt)

        query = query.limit(limit + 1)
        results = (await session.execute(query)).scalars().all()

        has_next = len(results) > limit
        items = results[:limit]

        next_cursor = None
        if has_next and items:
            next_cursor = encode_cursor(items[-1].created_at)

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "items": [serialize_comment(c) for c in items],
                "next_cursor": next_cursor,
            },
        )


@app.post(
    "/threads/{thread_id}/comments",
    tags=["Comments"],
    dependencies=[Depends(get_current_user), Depends(verify_csrf_header)],
)
async def create_comment(
    thread_id: uuid.UUID,
    req: CreateCommentRequest,
    request: Request,
    current_user: ContentUser = Depends(get_current_user),
) -> JSONResponse:
    """Create comment or reply (max 2 depth levels)."""
    async with content_db.transaction() as session:
        thread_query = select(Thread).where(Thread.id == thread_id)
        thread = (await session.execute(thread_query)).scalar_one_or_none()
        if not thread or thread.status in ("deleted", "hidden"):
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="THREAD_NOT_FOUND",
                message=f"Thread with ID '{thread_id}' was not found.",
                request=request,
            )

        if thread.is_locked:
            return error_response(
                status_code=status.HTTP_409_CONFLICT,
                code="THREAD_LOCKED",
                message="Cannot add comment: Thread is locked.",
                request=request,
            )

        # Hierarchy validation
        if req.parent_comment_id is not None:
            parent_query = select(Comment).where(Comment.id == req.parent_comment_id)
            parent = (await session.execute(parent_query)).scalar_one_or_none()
            if not parent:
                return error_response(
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="PARENT_COMMENT_NOT_FOUND",
                    message="Parent comment not found.",
                    request=request,
                )

            if parent.thread_id != thread_id:
                return error_response(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="CROSS_THREAD_PARENT",
                    message="Parent comment belongs to a different thread.",
                    request=request,
                )

            # Max depth 2: parent cannot itself be a reply
            if parent.parent_comment_id is not None:
                return error_response(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="COMMENT_DEPTH_EXCEEDED",
                    message="Maximum comment depth of 2 levels exceeded (replies to replies are not permitted).",
                    request=request,
                )

        new_comment = Comment(
            id=uuid.uuid4(),
            thread_id=thread_id,
            author_id=current_user.id,
            parent_comment_id=req.parent_comment_id,
            body=req.body.strip(),
            is_deleted=False,
        )
        session.add(new_comment)
        await session.flush()

        outbox = ContentOutboxEvent(
            id=uuid.uuid4(),
            event_type="CommentCreated",
            aggregate_type="Comment",
            aggregate_id=new_comment.id,
            payload={
                "comment_id": str(new_comment.id),
                "thread_id": str(thread_id),
                "author_id": str(current_user.id),
                "parent_comment_id": str(new_comment.parent_comment_id)
                if new_comment.parent_comment_id
                else None,
            },
        )
        session.add(outbox)

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=serialize_comment(new_comment),
    )


@app.patch(
    "/comments/{comment_id}",
    tags=["Comments"],
    dependencies=[Depends(get_current_user), Depends(verify_csrf_header)],
)
async def edit_comment(
    comment_id: uuid.UUID,
    req: EditCommentRequest,
    request: Request,
    current_user: ContentUser = Depends(get_current_user),
) -> JSONResponse:
    """Edit own comment body."""
    async with content_db.transaction() as session:
        query = select(Comment).where(Comment.id == comment_id)
        comment = (await session.execute(query)).scalar_one_or_none()
        if not comment or comment.is_deleted:
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="COMMENT_NOT_FOUND",
                message=f"Comment with ID '{comment_id}' was not found.",
                request=request,
            )

        if comment.author_id != current_user.id and current_user.role not in (
            "admin",
            "moderator",
        ):
            return error_response(
                status_code=status.HTTP_403_FORBIDDEN,
                code="FORBIDDEN_COMMENT_EDIT",
                message="You do not have permission to edit this comment.",
                request=request,
            )

        comment.body = req.body.strip()
        comment.edited_at = datetime.now(UTC)

        outbox = ContentOutboxEvent(
            id=uuid.uuid4(),
            event_type="CommentUpdated",
            aggregate_type="Comment",
            aggregate_id=comment.id,
            payload={"comment_id": str(comment.id), "thread_id": str(comment.thread_id)},
        )
        session.add(outbox)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=serialize_comment(comment),
    )


@app.delete(
    "/comments/{comment_id}",
    tags=["Comments"],
    dependencies=[Depends(get_current_user), Depends(verify_csrf_header)],
)
async def delete_comment(
    comment_id: uuid.UUID,
    request: Request,
    current_user: ContentUser = Depends(get_current_user),
) -> Response:
    """Soft delete comment."""
    async with content_db.transaction() as session:
        query = select(Comment).where(Comment.id == comment_id)
        comment = (await session.execute(query)).scalar_one_or_none()
        if not comment or comment.is_deleted:
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="COMMENT_NOT_FOUND",
                message=f"Comment with ID '{comment_id}' was not found.",
                request=request,
            )

        is_owner = comment.author_id == current_user.id
        is_mod = current_user.role in ("admin", "moderator")

        if not is_owner and not is_mod:
            return error_response(
                status_code=status.HTTP_403_FORBIDDEN,
                code="FORBIDDEN_COMMENT_DELETE",
                message="You do not have permission to delete this comment.",
                request=request,
            )

        comment.is_deleted = True
        outbox = ContentOutboxEvent(
            id=uuid.uuid4(),
            event_type="CommentDeleted",
            aggregate_type="Comment",
            aggregate_id=comment.id,
            payload={
                "comment_id": str(comment.id),
                "thread_id": str(comment.thread_id),
                "deleted_by": str(current_user.id),
            },
        )
        session.add(outbox)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Uploads & Attachments
# =============================================================================
logger = logging.getLogger("content_service")


def _remove_stored_file(object_key: str) -> bool:
    """Best-effort removal of a single stored object.

    Returns True when the file is gone (including already absent). A failed
    unlink is logged and left for filesystem reconciliation; callers must not
    pretend the removal succeeded.
    """
    try:
        file_path = get_absolute_file_path(content_settings.storage_local_dir, object_key)
        file_path.unlink(missing_ok=True)
        return True
    except OSError as exc:
        logger.warning(
            "Failed to remove stored object %s (%s)", object_key, type(exc).__name__
        )
        return False


async def cleanup_expired_pending_attachments(
    max_age_seconds: int = 86400, batch_size: int = 100
) -> int:
    """Bounded cleanup of expired unlinked pending attachments.

    State transition and physical deletion are deliberately split:

    1. A short transaction locks eligible rows (consistent id order, SKIP
       LOCKED so concurrent linking always wins cleanly) and commits them to
       ``deleted``.
    2. Only after that commit is the file unlinked. A crash or failure between
       the two steps leaves a ``deleted`` row plus a file, which
       :func:`reconcile_orphaned_storage_files` later removes. The reverse
       order is never used: unlinking before commit would allow a rollback to
       resurrect a ``pending`` row whose bytes are already gone.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=max_age_seconds)
    deleted_object_keys: list[str] = []

    async with content_db.transaction() as session:
        stmt = (
            select(Attachment)
            .where(
                Attachment.status == "pending",
                Attachment.thread_id.is_(None),
                Attachment.created_at < cutoff,
            )
            .order_by(Attachment.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        expired_attachments = (await session.execute(stmt)).scalars().all()
        for att in expired_attachments:
            att.status = "deleted"
            deleted_object_keys.append(att.object_key)

    # Transaction has committed: these rows can no longer roll back to a state
    # in which the files are required.
    for object_key in deleted_object_keys:
        _remove_stored_file(object_key)

    return len(deleted_object_keys)


async def reconcile_orphaned_storage_files(
    safety_age_seconds: int = 86400, batch_size: int = 100
) -> int:
    """Scan the configured upload root for physical files with no live metadata.

    A file is eligible when it is old enough (mtime grace period), has a
    server-generated object-key name, and its metadata is either absent or
    already terminal (``deleted``). This is the recovery path for:

    - a process crash between writing a file and inserting metadata;
    - a failed immediate unlink after a database error;
    - a failed unlink after the cleanup transaction committed ``deleted``.

    Safety properties:
    - only the configured ``images`` directory is scanned;
    - symlinks are ignored and never followed;
    - filenames must match ``SAFE_OBJECT_KEY_PATTERN``;
    - metadata is queried in one batch, then rechecked per file immediately
      before unlink;
    - any database error aborts the scan without deleting anything;
    - processing is bounded by ``batch_size`` and repeated runs are idempotent.
    """
    base_dir = Path(content_settings.storage_local_dir) / "images"
    if not base_dir.exists() or not base_dir.is_dir():
        return 0

    now_ts = datetime.now(UTC).timestamp()
    candidates: list[tuple[Path, str]] = []

    try:
        for entry in base_dir.iterdir():
            if len(candidates) >= batch_size:
                break

            # Security: never follow symlinks, even inside the upload root.
            if entry.is_symlink():
                continue
            if not entry.is_file():
                continue

            rel_key = f"uploads/images/{entry.name}"
            if not SAFE_OBJECT_KEY_PATTERN.match(rel_key):
                continue

            try:
                stat = entry.stat()
                if now_ts - stat.st_mtime >= safety_age_seconds:
                    candidates.append((entry, rel_key))
            except OSError:
                continue
    except OSError as exc:
        logger.error(
            "Failed to list upload storage directory (%s)", type(exc).__name__
        )
        return 0

    if not candidates:
        return 0

    candidate_keys = [c[1] for c in candidates]
    try:
        async with content_db.session() as session:
            stmt = select(Attachment.object_key, Attachment.status).where(
                Attachment.object_key.in_(candidate_keys)
            )
            metadata_state = {
                row.object_key: row.status for row in (await session.execute(stmt)).all()
            }
    except Exception as exc:
        # Safe abort: database unavailability must never cause mass deletion.
        logger.error(
            "Database query failed during reconciliation; aborting (%s)",
            type(exc).__name__,
        )
        return 0

    deleted_count = 0
    for file_path, rel_key in candidates:
        # Skip files that still have non-terminal metadata.
        if metadata_state.get(rel_key, "deleted") != "deleted":
            continue

        # Recheck immediately before deletion to narrow the upload/link race.
        try:
            async with content_db.session() as recheck_session:
                stmt_recheck = select(Attachment.status).where(
                    Attachment.object_key == rel_key
                )
                current_status = (
                    await recheck_session.execute(stmt_recheck)
                ).scalar_one_or_none()
        except Exception as exc:
            logger.warning(
                "Metadata recheck failed for %s; skipping (%s)",
                rel_key,
                type(exc).__name__,
            )
            continue

        if current_status is not None and current_status != "deleted":
            continue

        try:
            if file_path.is_symlink():
                continue
            if not file_path.is_file():
                continue
            file_path.unlink()
            deleted_count += 1
            logger.info("Reconciled orphaned storage object: %s", rel_key)
        except OSError as exc:
            logger.warning(
                "Failed to unlink orphaned object %s (%s)",
                rel_key,
                type(exc).__name__,
            )

    return deleted_count


async def _pending_attachment_cleanup_loop() -> None:
    """Background polling loop for pending attachment cleanup and reconciliation."""
    loop = asyncio.get_running_loop()
    next_reconcile_at = 0.0
    while True:
        try:
            await asyncio.sleep(content_settings.attachment_cleanup_interval_seconds)
            cleaned = await cleanup_expired_pending_attachments(
                max_age_seconds=content_settings.attachment_pending_ttl_seconds,
                batch_size=content_settings.attachment_cleanup_batch_size,
            )
            if cleaned > 0:
                logger.info("Purged %d expired pending attachments", cleaned)

            now = loop.time()
            if now >= next_reconcile_at:
                reconciled = await reconcile_orphaned_storage_files(
                    safety_age_seconds=content_settings.attachment_reconciliation_safety_age_seconds,
                    batch_size=content_settings.attachment_reconciliation_batch_size,
                )
                next_reconcile_at = now + float(
                    content_settings.attachment_reconciliation_interval_seconds
                )
                if reconciled > 0:
                    logger.info("Reconciled %d orphaned storage files", reconciled)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(
                "Error in pending attachment cleanup loop (%s)", type(exc).__name__
            )


@app.post(
    "/uploads/images",
    tags=["Uploads"],
    dependencies=[Depends(verify_csrf_header)],
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_image(
    request: Request,
    file: UploadFile = File(...),
    current_user: ContentUser = Depends(get_current_user),
) -> JSONResponse:
    """Upload an image attachment. Validates format via magic bytes and enforces 2MB limit."""
    # Read first chunk to inspect magic bytes
    header_chunk = await file.read(512)
    if not header_chunk:
        return error_response(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            code="UNSUPPORTED_MEDIA_TYPE",
            message="Uploaded file is empty or invalid.",
            request=request,
        )

    sniffed_mime = sniff_image_type(header_chunk)
    if not sniffed_mime:
        return error_response(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            code="UNSUPPORTED_MEDIA_TYPE",
            message="Unsupported media type. Only raster images (image/jpeg, image/png, image/webp) are accepted.",
            request=request,
        )

    # Stream the remainder while tracking size
    total_size = len(header_chunk)
    chunks = [header_chunk]

    chunk_size = 64 * 1024
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > MAX_FILE_SIZE_BYTES:
            return error_response(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                code="PAYLOAD_TOO_LARGE",
                message=f"File size exceeds maximum allowed limit of {MAX_FILE_SIZE_BYTES} bytes (2 MB).",
                request=request,
            )
        chunks.append(chunk)

    full_payload = b"".join(chunks)

    # Workstream 3: Deep image content, pixel decoding, and normalization
    is_valid, mime_or_err, normalized_payload, width, height = validate_and_normalize_image(full_payload)
    if not is_valid:
        return error_response(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            code="UNSUPPORTED_MEDIA_TYPE",
            message=f"Image validation failed: {mime_or_err}",
            request=request,
        )

    attachment_id = uuid.uuid4()
    object_key = generate_object_key(attachment_id, sniffed_mime)

    # Write to local persistent storage before inserting metadata. The file is
    # fsynced so a committed database row never points at unflushed bytes.
    ensure_storage_dir(content_settings.storage_local_dir)
    target_path = get_absolute_file_path(content_settings.storage_local_dir, object_key)

    try:
        with open(target_path, "wb") as f:
            f.write(normalized_payload)
            f.flush()
            os.fsync(f.fileno())
    except OSError as storage_exc:
        _remove_stored_file(object_key)
        logger.error(
            "Failed to write attachment file %s (%s)", object_key, type(storage_exc).__name__
        )
        return error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="STORAGE_ERROR",
            message="Failed to store uploaded image.",
            request=request,
        )

    # Phase B: File/metadata consistency with safe cleanup on DB failure
    try:
        async with content_db.transaction() as session:
            attachment = Attachment(
                id=attachment_id,
                thread_id=None,
                author_id=current_user.id,
                object_key=object_key,
                file_size=len(normalized_payload),
                mime_type=sniffed_mime,
                status="pending",
            )
            session.add(attachment)
    except Exception as db_exc:
        # The database transaction rolled back; the file is now unreferenced.
        # If this unlink fails, reconciliation will find it after the grace
        # period because no metadata row exists.
        _remove_stored_file(object_key)
        logger.error(
            "Attachment metadata persistence failed for %s (%s)",
            object_key,
            type(db_exc).__name__,
        )
        return error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="DATABASE_ERROR",
            message="Failed to persist attachment metadata. File upload rolled back.",
            request=request,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "id": str(attachment.id),
            "object_key": attachment.object_key,
            "file_size": attachment.file_size,
            "mime_type": attachment.mime_type,
            "status": attachment.status,
            "created_at": datetime.now(UTC).isoformat(),
        },
    )


@app.get("/uploads/images/{image_id}", tags=["Uploads"])
async def serve_image(
    image_id: uuid.UUID,
    request: Request,
    current_user: ContentUser | None = Depends(get_optional_current_user),
) -> Response:
    """Serve image attachment with strict visibility and privacy enforcement."""
    async with content_db.session() as session:
        stmt = (
            select(Attachment)
            .options(selectinload(Attachment.thread))
            .where(Attachment.id == image_id)
        )
        attachment = (await session.execute(stmt)).scalar_one_or_none()
        if not attachment or attachment.status == "deleted":
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="ATTACHMENT_NOT_FOUND",
                message=f"Attachment with ID '{image_id}' not found.",
                request=request,
            )

        # Phase D: Privacy & Visibility Policy Enforcement
        if attachment.status == "avatar":
            # Active avatars are public
            pass
        elif attachment.status == "pending":
            # Pending attachments are strictly private to their author
            if not current_user or current_user.id != attachment.author_id:
                return error_response(
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="ATTACHMENT_NOT_FOUND",
                    message=f"Attachment with ID '{image_id}' not found.",
                    request=request,
                )
        elif attachment.status == "linked":
            # If parent thread does not exist or is deleted -> 404
            thread = attachment.thread
            if not thread or thread.status == "deleted":
                return error_response(
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="ATTACHMENT_NOT_FOUND",
                    message=f"Attachment with ID '{image_id}' not found.",
                    request=request,
                )

            # If parent thread is hidden -> only author or moderator/admin may view
            if thread.status == "hidden":
                has_access = current_user and (
                    current_user.role in ("moderator", "admin")
                    or current_user.id == thread.author_id
                )
                if not has_access:
                    return error_response(
                        status_code=status.HTTP_404_NOT_FOUND,
                        code="ATTACHMENT_NOT_FOUND",
                        message=f"Attachment with ID '{image_id}' not found.",
                        request=request,
                    )
        else:
            # Any other state (orphaned, etc.) is not served
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="ATTACHMENT_NOT_FOUND",
                message=f"Attachment with ID '{image_id}' not found.",
                request=request,
            )

        file_path = get_absolute_file_path(
            content_settings.storage_local_dir, attachment.object_key
        )
        if not file_path.exists() or not file_path.is_file():
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="ATTACHMENT_FILE_MISSING",
                message="Attachment file is missing from storage.",
                request=request,
            )

        # Only published threads or active avatars are public. Pending and hidden/deleted
        # attachments must not be stored in shared caches.
        is_public = (
            attachment.status == "avatar"
            or (
                attachment.status == "linked"
                and attachment.thread is not None
                and attachment.thread.status == "published"
            )
        )
        return FileResponse(
            path=str(file_path),
            media_type=attachment.mime_type,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "public, max-age=86400" if is_public else "no-store, private",
            },
        )




# =============================================================================
# Internal Attachment Management (Avatar Lifecycle)
# =============================================================================
@app.post(
    "/internal/attachments/{attachment_id}/claim-avatar",
    tags=["Internal"],
    dependencies=[Depends(verify_internal_service_key)],
)
async def claim_avatar_attachment(
    attachment_id: uuid.UUID,
    request: Request,
) -> JSONResponse:
    """Claim a pending attachment as an active user avatar (Internal only)."""
    body = await request.json()
    user_id_str = body.get("user_id")
    if not user_id_str:
        return error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="MISSING_USER_ID",
            message="user_id is required in request payload.",
            request=request,
        )
    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        return error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_USER_ID",
            message="user_id must be a valid UUID.",
            request=request,
        )

    async with content_db.transaction() as session:
        stmt = (
            select(Attachment)
            .where(Attachment.id == attachment_id)
            .with_for_update()
        )
        att = (await session.execute(stmt)).scalar_one_or_none()
        if not att:
            return error_response(
                status_code=status.HTTP_404_NOT_FOUND,
                code="ATTACHMENT_NOT_FOUND",
                message="Attachment not found.",
                request=request,
            )

        if att.author_id != user_id:
            return error_response(
                status_code=status.HTTP_403_FORBIDDEN,
                code="FORBIDDEN_ATTACHMENT_CLAIM",
                message="Cannot claim an attachment belonging to another user.",
                request=request,
            )

        if att.status not in ("pending", "avatar"):
            return error_response(
                status_code=status.HTTP_409_CONFLICT,
                code="INVALID_ATTACHMENT_STATUS",
                message=f"Attachment cannot be claimed as avatar from status '{att.status}'.",
                request=request,
            )

        att.status = "avatar"
        att.linked_at = datetime.now(UTC)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "id": str(att.id),
            "object_key": att.object_key,
            "status": att.status,
        },
    )


@app.post(
    "/internal/attachments/{attachment_id}/release-avatar",
    tags=["Internal"],
    dependencies=[Depends(verify_internal_service_key)],
)
async def release_avatar_attachment(
    attachment_id: uuid.UUID,
    request: Request,
) -> JSONResponse:
    """Release or delete an avatar attachment when replaced or removed (Internal only)."""
    async with content_db.transaction() as session:
        stmt = (
            select(Attachment)
            .where(Attachment.id == attachment_id)
            .with_for_update()
        )
        att = (await session.execute(stmt)).scalar_one_or_none()
        if not att:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"status": "not_found", "id": str(attachment_id)},
            )

        # Mark deleted so cleanup / reconciliation purges it safely
        att.status = "deleted"
        object_key = att.object_key

    # Unlink file post-commit
    _remove_stored_file(object_key)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "deleted", "id": str(attachment_id)},
    )


# =============================================================================
# Internal Event Ingestion (DEC-005 View Count Propagation)
# =============================================================================
@app.post(
    "/internal/events",
    tags=["Internal"],
    dependencies=[Depends(verify_internal_service_key)],
)
async def ingest_internal_event(
    request: Request,
) -> JSONResponse:
    """Consume internal domain events such as ThreadViewCountIncremented (DEC-005)."""
    body = await request.json()
    event_id_str = body.get("event_id")
    event_type = body.get("event_type")
    data = body.get("data", {})

    if event_type == "ThreadViewCountIncremented":
        thread_id_str = data.get("thread_id")
        increment = data.get("increment", 1)
        if thread_id_str and event_id_str:
            event_id = uuid.UUID(event_id_str)
            thread_id = uuid.UUID(thread_id_str)
            async with content_db.transaction() as session:
                # 1. Atomic Idempotency Guard
                consumed_stmt = select(ConsumedEvent).where(ConsumedEvent.event_id == event_id)
                existing = (await session.execute(consumed_stmt)).scalar_one_or_none()
                if existing:
                    return JSONResponse(
                        status_code=status.HTTP_200_OK,
                        content={
                            "status": "processed",
                            "thread_id": thread_id_str,
                            "increment": 0,
                            "duplicate": True,
                        },
                    )

                # 2. Update view count
                query = select(Thread).where(Thread.id == thread_id)
                thread = (await session.execute(query)).scalar_one_or_none()
                if thread:
                    thread.view_count += increment

                # 3. Record consumption atomically
                consumed = ConsumedEvent(
                    id=uuid.uuid4(),
                    event_id=event_id,
                    event_type=event_type,
                    status="processed",
                    attempts=1,
                    processed_at=datetime.now(UTC),
                    metadata_={"thread_id": thread_id_str, "increment": increment},
                )
                session.add(consumed)

            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "processed",
                    "thread_id": thread_id_str,
                    "increment": increment,
                    "duplicate": False,
                },
            )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "ignored", "event_type": event_type},
    )
