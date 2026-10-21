"""Search Service FastAPI application."""

import base64
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from src.common.errors import register_canonical_error_handlers
from src.common.logging import configure_logging
from src.common.metrics import MetricsRegistry, setup_metrics_endpoint
from src.services.search.auth import (
    SearchUser,
    get_auth_http_client,
    get_optional_user,
    require_role,
    verify_csrf_header,
    verify_internal_service_key,
)
from src.services.search.config import search_settings
from src.services.search.database import search_db
from src.services.search.events import SearchEventHandlerRegistry, sanitize_search_text
from src.services.search.models import SearchDocument
from src.services.search.schemas import ConsumedEventPayload

logger = logging.getLogger(__name__)

configure_logging(search_settings.service_name, search_settings.log_level)
metrics_registry = MetricsRegistry(search_settings.service_name)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    await search_db.close()


app = FastAPI(
    title="Search Service",
    version="1.0.0",
    lifespan=lifespan,
)

register_canonical_error_handlers(app)
setup_metrics_endpoint(app, metrics_registry, db_manager=search_db)


# --- Cursor Pagination Helpers ---
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


def serialize_search_item(d: SearchDocument) -> dict:
    created_dt = d.source_created_at or d.source_updated_at or d.indexed_at
    if created_dt and created_dt.tzinfo is None:
        created_dt = created_dt.replace(tzinfo=UTC)
    iso_created = created_dt.isoformat() if created_dt else datetime.now(UTC).isoformat()

    body_text = d.body or ""
    snippet = (body_text[:150] + "...") if len(body_text) > 150 else body_text

    return {
        "thread_id": str(d.source_id),
        "title": d.title or "Untitled",
        "snippet": snippet,
        "category_id": str(d.category_id or uuid.UUID("00000000-0000-0000-0000-000000000001")),
        "author_id": str(d.author_id or uuid.UUID("00000000-0000-0000-0000-000000000001")),
        "created_at": iso_created,
    }


# --- Health & Readiness ---
@app.get("/health/live", tags=["Health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["Health"])
async def readiness() -> JSONResponse:
    db_healthy = await search_db.ping()
    if not db_healthy:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "database": "unreachable"},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "ready", "database": "connected"},
    )


# --- Search Endpoints ---
@app.get("/search/suggestions", tags=["Search"])
async def search_suggestions(
    q: str = Query(min_length=2),
    limit: int = Query(default=5, ge=1, le=20),
) -> dict[str, list[str]]:
    """Return search suggestions conforming to OpenAPI schema."""
    cleaned_q = q.strip()
    if len(cleaned_q) < 2:
        return {"suggestions": []}

    async with search_db.session() as session:
        query = (
            select(SearchDocument.title)
            .where(
                SearchDocument.is_visible.is_(True),
                SearchDocument.title.ilike(f"%{cleaned_q}%"),
            )
            .distinct()
            .limit(limit)
        )
        titles = (await session.execute(query)).scalars().all()
        return {"suggestions": [t for t in titles if t]}


@app.get("/search", tags=["Search"])
async def search_threads(
    q: str | None = None,
    category_id: uuid.UUID | None = None,
    tag: str | None = None,
    sort: str = Query(default="relevance", pattern=r"^(relevance|newest|most_viewed)$"),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
) -> dict[str, list | str | None]:
    """Return search results page conforming to SearchPage OpenAPI schema."""
    cursor_dt = decode_cursor(cursor)

    async with search_db.session() as session:
        query = select(SearchDocument).where(SearchDocument.is_visible.is_(True))

        if category_id:
            query = query.where(SearchDocument.category_id == category_id)

        if tag:
            norm_tag = tag.strip().lower()
            from sqlalchemy import String, cast

            query = query.where(cast(SearchDocument.tags, String).ilike(f'%"{norm_tag}"%'))

        if q:
            clean_q = q.strip()
            if clean_q:
                # Text filtering using ilike across title and body
                query = query.where(
                    SearchDocument.title.ilike(f"%{clean_q}%")
                    | SearchDocument.body.ilike(f"%{clean_q}%")
                )

        # Ordering & Pagination
        if sort == "newest":
            query = query.order_by(
                SearchDocument.source_created_at.desc(), SearchDocument.id.desc()
            )
            if cursor_dt is not None:
                query = query.where(SearchDocument.source_created_at < cursor_dt)
        elif sort == "most_viewed":
            query = query.order_by(
                SearchDocument.view_count.desc(),
                SearchDocument.source_created_at.desc(),
                SearchDocument.id.desc(),
            )
            if cursor_dt is not None:
                query = query.where(SearchDocument.source_created_at < cursor_dt)
        else:  # relevance / default
            query = query.order_by(SearchDocument.indexed_at.desc(), SearchDocument.id.desc())
            if cursor_dt is not None:
                query = query.where(SearchDocument.indexed_at < cursor_dt)

        query = query.limit(limit + 1)
        docs = (await session.execute(query)).scalars().all()

        has_next = len(docs) > limit
        items = docs[:limit]

        next_cursor = None
        if has_next and items:
            last_doc = items[-1]
            if sort in ("newest", "most_viewed") and last_doc.source_created_at:
                next_cursor = encode_cursor(last_doc.source_created_at)
            elif last_doc.indexed_at:
                next_cursor = encode_cursor(last_doc.indexed_at)

        return {
            "items": [serialize_search_item(d) for d in items],
            "next_cursor": next_cursor,
        }


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
    """Internal idempotent domain event consumer for search index updates."""
    async with search_db.transaction() as session:
        doc = await SearchEventHandlerRegistry.process_event(session, event)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "processed",
            "event_id": str(event.event_id),
            "event_type": event.event_type,
            "document_updated": doc is not None,
        },
    )


# --- Admin Index Reconciliation / Rebuild Endpoint ---
@app.post(
    "/admin/reindex",
    tags=["Search Admin"],
    dependencies=[Depends(require_role("admin")), Depends(verify_csrf_header)],
)
async def trigger_reindex(
    request: Request,
    current_user: SearchUser = Depends(get_optional_user),
) -> JSONResponse:
    """Reconcile search index by fetching published threads from Content Service (DEC-002)."""
    client = get_auth_http_client()
    content_url = f"{search_settings.content_service_url.rstrip('/')}/threads?limit=50"

    try:
        resp = await client.get(
            content_url,
            headers={"X-Request-ID": request.headers.get("X-Request-ID", str(uuid.uuid4()))},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Content Service unreachable for reindexing: {exc}",
        ) from exc

    if resp.status_code != status.HTTP_200_OK:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch threads from Content Service",
        )

    threads_data = resp.json().get("items", [])
    reindexed_count = 0
    now = datetime.now(UTC)

    async with search_db.transaction() as session:
        for t in threads_data:
            thread_id = uuid.UUID(t["id"])
            stmt = select(SearchDocument).where(
                SearchDocument.source_type == "thread",
                SearchDocument.source_id == thread_id,
            )
            doc = (await session.execute(stmt)).scalar_one_or_none()

            author_id = uuid.UUID(t["author_id"]) if t.get("author_id") else None
            category_id = uuid.UUID(t["category_id"]) if t.get("category_id") else None
            tags = [tag.lower() for tag in t.get("tags", [])]
            is_visible = t.get("status") == "published"
            view_count = t.get("view_count", 0)

            created_at_val = None
            if t.get("created_at"):
                with suppress(Exception):
                    created_at_val = datetime.fromisoformat(t["created_at"])

            if not doc:
                doc = SearchDocument(
                    id=uuid.uuid4(),
                    source_type="thread",
                    source_id=thread_id,
                    title=sanitize_search_text(t.get("title")),
                    body=sanitize_search_text(t.get("body", "")),
                    author_id=author_id,
                    category_id=category_id,
                    tags=tags,
                    is_visible=is_visible,
                    view_count=view_count,
                    version=1,
                    source_created_at=created_at_val or now,
                    source_updated_at=now,
                    indexed_at=now,
                )
                session.add(doc)
            else:
                doc.title = sanitize_search_text(t.get("title"))
                if t.get("body"):
                    doc.body = sanitize_search_text(t["body"])
                doc.author_id = author_id
                doc.category_id = category_id
                doc.tags = tags
                doc.is_visible = is_visible
                doc.view_count = view_count
                doc.version += 1
                doc.source_updated_at = now
                doc.indexed_at = now

            reindexed_count += 1

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "reindexed", "count": reindexed_count},
    )
