"""Interaction Service FastAPI application implementing votes, bookmarks, view tracking (DEC-005), and reputation."""

import base64
import hashlib
import hmac
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import Depends, FastAPI, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from src.common.errors import (
    error_response,
    register_canonical_error_handlers,
)
from src.common.logging import configure_logging
from src.common.rate_limit import get_client_ip
from src.services.interaction.auth import (
    InteractionUser,
    get_current_user,
    verify_csrf_header,
)
from src.services.interaction.config import interaction_settings
from src.services.interaction.database import interaction_db
from src.services.interaction.models import (
    Bookmark,
    InteractionOutboxEvent,
    ReputationLedger,
    ThreadView,
    Vote,
)
from src.services.interaction.schemas import (
    VoteRequest,
)

configure_logging(interaction_settings.service_name, interaction_settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    await interaction_db.close()


app = FastAPI(
    title="Interaction Service",
    version="1.1.0",
    lifespan=lifespan,
)

register_canonical_error_handlers(app)


# Inter-service HTTP client hook for dependency injection / mocking in tests
_content_http_client: httpx.AsyncClient | None = None


def get_content_http_client() -> httpx.AsyncClient:
    global _content_http_client
    if _content_http_client is None:
        _content_http_client = httpx.AsyncClient(timeout=5.0)
    return _content_http_client


def set_content_http_client(client: httpx.AsyncClient | None) -> None:
    global _content_http_client
    _content_http_client = client


def compute_view_session_hash(request: Request, secret: str) -> str:
    """Compute HMAC-SHA256 hash of session token or client IP for view deduplication (ADR-016)."""
    session_cookie = request.cookies.get(interaction_settings.session_cookie_name)
    identifier = session_cookie if session_cookie else get_client_ip(request)
    return hmac.new(secret.encode("utf-8"), identifier.encode("utf-8"), hashlib.sha256).hexdigest()


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


# --- Health & Readiness ---
@app.get("/health/live", tags=["Health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["Health"])
async def readiness() -> JSONResponse:
    db_healthy = await interaction_db.ping()
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
# Reputation
# =============================================================================
@app.get("/users/{user_id}/reputation", tags=["Interactions"])
async def get_user_reputation(user_id: uuid.UUID) -> dict[str, int | str]:
    """Return user reputation conforming to OpenAPI contract by summing reputation ledger."""
    async with interaction_db.session() as session:
        query = select(func.coalesce(func.sum(ReputationLedger.delta), 0)).where(
            ReputationLedger.user_id == user_id
        )
        score = (await session.execute(query)).scalar_one()
        return {"user_id": str(user_id), "reputation_score": int(score)}


# =============================================================================
# Voting & Reactions
# =============================================================================
@app.put(
    "/threads/{thread_id}/vote",
    tags=["Interactions"],
    dependencies=[Depends(get_current_user), Depends(verify_csrf_header)],
)
async def vote_thread(
    thread_id: uuid.UUID,
    req: VoteRequest,
    request: Request,
    current_user: InteractionUser = Depends(get_current_user),
) -> JSONResponse:
    """Vote or change/toggle vote on a thread."""
    if req.value not in (-1, 1):
        return error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="VALIDATION_ERROR",
            message="Vote value must be 1 or -1",
            details=[{"field": "value", "reason": "Input should be 1 or -1"}],
            request=request,
        )

    # Verify target thread existence and author via Content Service
    client = get_content_http_client()
    url = f"{interaction_settings.content_service_url.rstrip('/')}/threads/{thread_id}"
    try:
        resp = await client.get(
            url,
            headers={"X-Request-ID": request.headers.get("X-Request-ID", str(uuid.uuid4()))},
        )
    except Exception as exc:
        return error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="SERVICE_UNAVAILABLE",
            message=f"Unable to reach Content Service: {exc}",
            request=request,
        )

    if resp.status_code == 404:
        return error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="THREAD_NOT_FOUND",
            message=f"Thread with ID '{thread_id}' was not found.",
            request=request,
        )
    elif resp.status_code != 200:
        return error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_TARGET",
            message="Target thread cannot be verified.",
            request=request,
        )

    thread_info = resp.json()
    author_id = uuid.UUID(thread_info["author_id"])

    # Disallow self-voting
    if author_id == current_user.id:
        return error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="SELF_VOTE_NOT_ALLOWED",
            message="Voting on your own content is not permitted.",
            request=request,
        )

    my_vote: int | None = req.value
    reputation_delta: int = 0

    from sqlalchemy.exc import IntegrityError

    try:
        async with interaction_db.transaction() as session:
            # Check existing vote
            query = select(Vote).where(
                Vote.voter_id == current_user.id, Vote.thread_id == thread_id
            )
            existing_vote = (await session.execute(query)).scalar_one_or_none()

            if existing_vote:
                if existing_vote.value == req.value:
                    # Toggle off (cancel vote)
                    await session.delete(existing_vote)
                    my_vote = None
                    reputation_delta = -req.value * 5  # Revert prior reputation grant
                else:
                    # Change vote (e.g., from -1 to 1 or 1 to -1)
                    existing_vote.value = req.value
                    reputation_delta = req.value * 10  # Flip reputation (-5 to +5 is +10)
            else:
                # New vote
                new_vote = Vote(
                    id=uuid.uuid4(),
                    voter_id=current_user.id,
                    target_type="thread",
                    thread_id=thread_id,
                    comment_id=None,
                    value=req.value,
                )
                session.add(new_vote)
                reputation_delta = req.value * 5

            await session.flush()

            # Calculate updated aggregate score
            score_query = select(func.coalesce(func.sum(Vote.value), 0)).where(
                Vote.thread_id == thread_id
            )
            total_score = (await session.execute(score_query)).scalar_one()

            # Update reputation ledger for target author if delta != 0
            if reputation_delta != 0:
                ledger_query = select(ReputationLedger).where(
                    ReputationLedger.source_type == "thread",
                    ReputationLedger.source_id == thread_id,
                    ReputationLedger.caused_by_user_id == current_user.id,
                    ReputationLedger.reason == "thread_vote",
                )
                existing_ledger = (await session.execute(ledger_query)).scalar_one_or_none()
                if existing_ledger:
                    new_delta = existing_ledger.delta + reputation_delta
                    if new_delta == 0:
                        await session.delete(existing_ledger)
                    else:
                        existing_ledger.delta = new_delta
                else:
                    ledger_entry = ReputationLedger(
                        user_id=author_id,
                        source_type="thread",
                        source_id=thread_id,
                        caused_by_user_id=current_user.id,
                        delta=reputation_delta,
                        reason="thread_vote",
                    )
                    session.add(ledger_entry)

            # Write outbox event
            outbox = InteractionOutboxEvent(
                id=uuid.uuid4(),
                event_type="VoteChanged",
                aggregate_type="Thread",
                aggregate_id=thread_id,
                payload={
                    "target_type": "thread",
                    "target_id": str(thread_id),
                    "voter_id": str(current_user.id),
                    "author_id": str(author_id),
                    "my_vote": my_vote,
                    "score": int(total_score),
                    "reputation_delta": reputation_delta,
                },
            )
            session.add(outbox)
    except IntegrityError:
        return error_response(
            status_code=status.HTTP_409_CONFLICT,
            code="CONFLICT",
            message="Concurrent vote operation conflicted. Please retry.",
            request=request,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "target_type": "thread",
            "target_id": str(thread_id),
            "my_vote": my_vote,
            "score": int(total_score),
            "reputation_delta": reputation_delta,
        },
    )


@app.put(
    "/comments/{comment_id}/vote",
    tags=["Interactions"],
    dependencies=[Depends(get_current_user), Depends(verify_csrf_header)],
)
async def vote_comment(
    comment_id: uuid.UUID,
    req: VoteRequest,
    request: Request,
    current_user: InteractionUser = Depends(get_current_user),
) -> JSONResponse:
    """Vote or change/toggle vote on a comment."""
    if req.value not in (-1, 1):
        return error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="VALIDATION_ERROR",
            message="Vote value must be 1 or -1",
            details=[{"field": "value", "reason": "Input should be 1 or -1"}],
            request=request,
        )

    my_vote: int | None = req.value
    reputation_delta: int = 0

    async with interaction_db.transaction() as session:
        query = select(Vote).where(Vote.voter_id == current_user.id, Vote.comment_id == comment_id)
        existing_vote = (await session.execute(query)).scalar_one_or_none()

        if existing_vote:
            if existing_vote.value == req.value:
                # Cancel vote
                await session.delete(existing_vote)
                my_vote = None
                reputation_delta = -req.value * 2
            else:
                existing_vote.value = req.value
                reputation_delta = req.value * 4
        else:
            new_vote = Vote(
                id=uuid.uuid4(),
                voter_id=current_user.id,
                target_type="comment",
                thread_id=None,
                comment_id=comment_id,
                value=req.value,
            )
            session.add(new_vote)
            reputation_delta = req.value * 2

        await session.flush()

        score_query = select(func.coalesce(func.sum(Vote.value), 0)).where(
            Vote.comment_id == comment_id
        )
        total_score = (await session.execute(score_query)).scalar_one()

        outbox = InteractionOutboxEvent(
            id=uuid.uuid4(),
            event_type="VoteChanged",
            aggregate_type="Comment",
            aggregate_id=comment_id,
            payload={
                "target_type": "comment",
                "target_id": str(comment_id),
                "voter_id": str(current_user.id),
                "my_vote": my_vote,
                "score": int(total_score),
                "reputation_delta": reputation_delta,
            },
        )
        session.add(outbox)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "target_type": "comment",
            "target_id": str(comment_id),
            "my_vote": my_vote,
            "score": int(total_score),
            "reputation_delta": reputation_delta,
        },
    )


# =============================================================================
# Bookmarks
# =============================================================================
@app.put(
    "/threads/{thread_id}/bookmark",
    tags=["Interactions"],
    dependencies=[Depends(get_current_user), Depends(verify_csrf_header)],
)
async def bookmark_thread(
    thread_id: uuid.UUID,
    request: Request,
    current_user: InteractionUser = Depends(get_current_user),
) -> JSONResponse:
    """Bookmark thread for authenticated user."""
    # Verify thread existence via Content Service
    client = get_content_http_client()
    url = f"{interaction_settings.content_service_url.rstrip('/')}/threads/{thread_id}"
    try:
        resp = await client.get(
            url,
            headers={"X-Request-ID": request.headers.get("X-Request-ID", str(uuid.uuid4()))},
        )
    except Exception as exc:
        return error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="SERVICE_UNAVAILABLE",
            message=f"Unable to reach Content Service: {exc}",
            request=request,
        )

    if resp.status_code == 404:
        return error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="THREAD_NOT_FOUND",
            message=f"Thread with ID '{thread_id}' was not found.",
            request=request,
        )
    elif resp.status_code != 200:
        return error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_TARGET",
            message="Target thread cannot be verified.",
            request=request,
        )

    from sqlalchemy.exc import IntegrityError

    try:
        async with interaction_db.transaction() as session:
            query = select(Bookmark).where(
                Bookmark.user_id == current_user.id, Bookmark.thread_id == thread_id
            )
            existing = (await session.execute(query)).scalar_one_or_none()
            if not existing:
                bm = Bookmark(
                    id=uuid.uuid4(),
                    user_id=current_user.id,
                    thread_id=thread_id,
                )
                session.add(bm)
    except IntegrityError:
        pass

    return JSONResponse(status_code=status.HTTP_200_OK, content={"bookmarked": True})


@app.delete(
    "/threads/{thread_id}/bookmark",
    tags=["Interactions"],
    dependencies=[Depends(get_current_user), Depends(verify_csrf_header)],
)
async def unbookmark_thread(
    thread_id: uuid.UUID,
    request: Request,
    current_user: InteractionUser = Depends(get_current_user),
) -> Response:
    """Remove bookmark owned by authenticated user."""
    async with interaction_db.transaction() as session:
        query = select(Bookmark).where(
            Bookmark.user_id == current_user.id, Bookmark.thread_id == thread_id
        )
        existing = (await session.execute(query)).scalar_one_or_none()
        if existing:
            await session.delete(existing)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get(
    "/profile/bookmarks",
    tags=["Interactions"],
    dependencies=[Depends(get_current_user)],
)
async def list_own_bookmarks(
    request: Request,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    current_user: InteractionUser = Depends(get_current_user),
) -> JSONResponse:
    """List own bookmarked threads with cursor pagination, enriching thread details via Content Service."""
    cursor_dt = decode_cursor(cursor)

    async with interaction_db.session() as session:
        query = (
            select(Bookmark)
            .where(Bookmark.user_id == current_user.id)
            .order_by(Bookmark.created_at.desc())
        )
        if cursor_dt is not None:
            query = query.where(Bookmark.created_at < cursor_dt)

        query = query.limit(limit + 1)
        bookmarks = (await session.execute(query)).scalars().all()

        has_next = len(bookmarks) > limit
        items_slice = bookmarks[:limit]

        next_cursor = None
        if has_next and items_slice:
            next_cursor = encode_cursor(items_slice[-1].created_at)

        # Enrich threads via Content Service
        client = get_content_http_client()
        items = []
        for bm in items_slice:
            th_url = (
                f"{interaction_settings.content_service_url.rstrip('/')}/threads/{bm.thread_id}"
            )
            try:
                th_resp = await client.get(
                    th_url,
                    headers={
                        "X-Request-ID": request.headers.get("X-Request-ID", str(uuid.uuid4()))
                    },
                )
                if th_resp.status_code == 200:
                    items.append(th_resp.json())
                else:
                    # Provide fallback thread representation if deleted or unreachable
                    items.append(
                        {
                            "id": str(bm.thread_id),
                            "author_id": str(current_user.id),
                            "category_id": "00000000-0000-0000-0000-000000000001",
                            "title": f"Bookmarked Thread {bm.thread_id}",
                            "body": "Thread content unavailable.",
                            "tags": [],
                            "status": "published",
                            "is_pinned": False,
                            "is_locked": False,
                            "best_answer_comment_id": None,
                            "view_count": 0,
                            "created_at": bm.created_at.isoformat()
                            if bm.created_at
                            else datetime.now(UTC).isoformat(),
                            "updated_at": bm.created_at.isoformat()
                            if bm.created_at
                            else datetime.now(UTC).isoformat(),
                        }
                    )
            except Exception:
                items.append(
                    {
                        "id": str(bm.thread_id),
                        "author_id": str(current_user.id),
                        "category_id": "00000000-0000-0000-0000-000000000001",
                        "title": f"Bookmarked Thread {bm.thread_id}",
                        "body": "Thread content unavailable.",
                        "tags": [],
                        "status": "published",
                        "is_pinned": False,
                        "is_locked": False,
                        "best_answer_comment_id": None,
                        "view_count": 0,
                        "created_at": bm.created_at.isoformat()
                        if bm.created_at
                        else datetime.now(UTC).isoformat(),
                        "updated_at": bm.created_at.isoformat()
                        if bm.created_at
                        else datetime.now(UTC).isoformat(),
                    }
                )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"items": items, "next_cursor": next_cursor},
        )


# =============================================================================
# View Tracking & Deduplication (DEC-005)
# =============================================================================
@app.post("/threads/{thread_id}/view", tags=["Interactions"])
async def record_thread_view(thread_id: uuid.UUID, request: Request) -> Response:
    """Record a thread view with anti-inflation deduplication (30-minute window).

    Emits ThreadViewCountIncremented outbox event if first view within window (DEC-005).
    """
    # Verify thread exists via Content Service
    client = get_content_http_client()
    url = f"{interaction_settings.content_service_url.rstrip('/')}/threads/{thread_id}"
    try:
        resp = await client.get(
            url,
            headers={"X-Request-ID": request.headers.get("X-Request-ID", str(uuid.uuid4()))},
        )
    except Exception as exc:
        return error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="SERVICE_UNAVAILABLE",
            message=f"Unable to reach Content Service: {exc}",
            request=request,
        )

    if resp.status_code == 404:
        return error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="THREAD_NOT_FOUND",
            message=f"Thread with ID '{thread_id}' was not found.",
            request=request,
        )
    elif resp.status_code != 200:
        return error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_TARGET",
            message="Target thread cannot be verified.",
            request=request,
        )

    session_key_hash = compute_view_session_hash(request, interaction_settings.view_hmac_secret)
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=interaction_settings.view_window_seconds)

    from sqlalchemy.exc import IntegrityError

    try:
        async with interaction_db.transaction() as session:
            query = select(ThreadView).where(
                ThreadView.thread_id == thread_id,
                ThreadView.session_key_hash == session_key_hash,
            )
            existing_view = (await session.execute(query)).scalar_one_or_none()

            if existing_view:
                # Check viewed_at window
                viewed_at_utc = existing_view.viewed_at
                if viewed_at_utc.tzinfo is None:
                    viewed_at_utc = viewed_at_utc.replace(tzinfo=UTC)

                if viewed_at_utc > cutoff:
                    # Deduplicated view (already recorded within window) -> 204 No Content
                    return Response(status_code=status.HTTP_204_NO_CONTENT)
                else:
                    # Prior view expired (>30m ago) -> Refresh view timestamp
                    existing_view.viewed_at = now
            else:
                # First view -> Insert record
                new_view = ThreadView(
                    thread_id=thread_id,
                    session_key_hash=session_key_hash,
                    viewed_at=now,
                )
                session.add(new_view)

            # Emit DEC-005 ThreadViewCountIncremented event to transactional outbox
            outbox = InteractionOutboxEvent(
                id=uuid.uuid4(),
                event_type="ThreadViewCountIncremented",
                aggregate_type="Thread",
                aggregate_id=thread_id,
                payload={
                    "thread_id": str(thread_id),
                    "increment": 1,
                    "recorded_at": now.isoformat(),
                },
            )
            session.add(outbox)
    except IntegrityError:
        # Concurrent view insertion race for same session_key_hash is cleanly deduplicated
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
