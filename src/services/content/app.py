"""Content Service FastAPI application implementing core discussion and content workflows."""

import base64
import re
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.common.errors import (
    error_response,
    not_implemented_response,
    register_canonical_error_handlers,
)
from src.common.logging import configure_logging
from src.common.metrics import MetricsRegistry, setup_metrics_endpoint
from src.common.outbox_worker import OutboxWorker
from src.services.content.auth import (
    ContentUser,
    get_current_user,
    require_role,
    verify_csrf_header,
    verify_internal_service_key,
)
from src.services.content.config import content_settings
from src.services.content.database import content_db
from src.services.content.models import (
    Category,
    Comment,
    ConsumedEvent,
    ContentOutboxEvent,
    Thread,
    ThreadTag,
)
from src.services.content.schemas import (
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
    yield
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
    sort: str = Query(default="newest", enum=["newest", "most_viewed", "most_voted"]),
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
# Uploads
# =============================================================================
@app.post("/uploads/images", tags=["Uploads"])
async def upload_image(request: Request) -> JSONResponse:
    return not_implemented_response(request, "POST /uploads/images")


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
