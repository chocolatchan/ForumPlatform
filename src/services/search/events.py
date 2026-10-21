"""Event processing and handler registry for Search Service."""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.search.models import ConsumedEvent, SearchDocument
from src.services.search.schemas import ConsumedEventPayload

logger = logging.getLogger(__name__)


def sanitize_search_text(text: str | None, max_len: int = 50000) -> str:
    """Sanitize and trim text for indexing."""
    if not text:
        return ""
    clean = text.strip()
    if len(clean) > max_len:
        return clean[:max_len]
    return clean


class SearchEventHandlerRegistry:
    """Dispatches domain events to dedicated handlers for search index updating."""

    SUPPORTED_EVENTS = {
        "ThreadCreated",
        "ThreadUpdated",
        "ThreadDeleted",
        "ThreadLockToggled",
        "ThreadPinToggled",
        "ThreadViewCountIncremented",
        "ModerationActionApplied",
    }

    @classmethod
    async def process_event(
        cls,
        session: AsyncSession,
        event: ConsumedEventPayload,
    ) -> SearchDocument | None:
        """Process a domain event atomically within the provided database session.

        Returns updated/created SearchDocument, or None if skipped/deduplicated/removed.
        """
        event_type = event.event_type
        if event_type not in cls.SUPPORTED_EVENTS:
            logger.info("Ignoring unsupported event type: %s", event_type)
            return None

        # 1. Atomic idempotency check: check if event_id already consumed
        stmt = select(ConsumedEvent).where(ConsumedEvent.event_id == event.event_id)
        existing_consumed = (await session.execute(stmt)).scalar_one_or_none()
        if existing_consumed:
            logger.info("Duplicate event %s already consumed by search.", event.event_id)
            return None

        handler_map = {
            "ThreadCreated": cls._handle_thread_created,
            "ThreadUpdated": cls._handle_thread_updated,
            "ThreadDeleted": cls._handle_thread_deleted,
            "ThreadLockToggled": cls._handle_thread_lock_toggled,
            "ThreadPinToggled": cls._handle_thread_pin_toggled,
            "ThreadViewCountIncremented": cls._handle_thread_view_count_incremented,
            "ModerationActionApplied": cls._handle_moderation_action_applied,
        }
        handler = handler_map.get(event_type)
        if not handler:
            return None

        doc = await handler(session, event)

        # 2. Record event consumption atomically
        consumed = ConsumedEvent(
            id=uuid.uuid4(),
            event_id=event.event_id,
            event_type=event.event_type,
            status="processed",
            attempts=1,
            processed_at=datetime.now(UTC),
            metadata_={"producer": event.producer, "occurred_at": str(event.occurred_at)},
        )
        session.add(consumed)

        return doc

    @classmethod
    async def _handle_thread_created(
        cls,
        session: AsyncSession,
        event: ConsumedEventPayload,
    ) -> SearchDocument | None:
        data = event.data
        thread_id_str = data.get("thread_id")
        if not thread_id_str:
            return None
        thread_id = uuid.UUID(thread_id_str)
        author_id = uuid.UUID(data["author_id"]) if data.get("author_id") else None
        category_id = uuid.UUID(data["category_id"]) if data.get("category_id") else None
        title = sanitize_search_text(data.get("title"))
        body = sanitize_search_text(data.get("body", ""))
        tags = data.get("tags") or []
        status = data.get("status", "published")
        now = datetime.now(UTC)

        # Stale check / upsert
        stmt = select(SearchDocument).where(
            SearchDocument.source_type == "thread",
            SearchDocument.source_id == thread_id,
        )
        doc = (await session.execute(stmt)).scalar_one_or_none()
        if not doc:
            doc = SearchDocument(
                id=uuid.uuid4(),
                source_type="thread",
                source_id=thread_id,
                title=title,
                body=body,
                author_id=author_id,
                category_id=category_id,
                tags=tags,
                is_visible=(status == "published"),
                view_count=0,
                version=1,
                source_created_at=now,
                source_updated_at=now,
                indexed_at=now,
            )
            session.add(doc)
        else:
            doc.title = title
            if body:
                doc.body = body
            doc.author_id = author_id
            doc.category_id = category_id
            doc.tags = tags
            doc.is_visible = status == "published"
            doc.version += 1
            doc.source_updated_at = now
            doc.indexed_at = now

        return doc

    @classmethod
    async def _handle_thread_updated(
        cls,
        session: AsyncSession,
        event: ConsumedEventPayload,
    ) -> SearchDocument | None:
        data = event.data
        thread_id_str = data.get("thread_id")
        if not thread_id_str:
            return None
        thread_id = uuid.UUID(thread_id_str)
        now = datetime.now(UTC)

        stmt = select(SearchDocument).where(
            SearchDocument.source_type == "thread",
            SearchDocument.source_id == thread_id,
        )
        doc = (await session.execute(stmt)).scalar_one_or_none()
        if not doc:
            # If not indexed yet, create it
            doc = SearchDocument(
                id=uuid.uuid4(),
                source_type="thread",
                source_id=thread_id,
                title=sanitize_search_text(data.get("title")),
                body=sanitize_search_text(data.get("body", "")),
                author_id=uuid.UUID(data["author_id"]) if data.get("author_id") else None,
                category_id=uuid.UUID(data["category_id"]) if data.get("category_id") else None,
                tags=data.get("tags") or [],
                is_visible=(data.get("status", "published") == "published"),
                view_count=0,
                version=1,
                source_created_at=now,
                source_updated_at=now,
                indexed_at=now,
            )
            session.add(doc)
            return doc

        # Update fields
        if "title" in data:
            doc.title = sanitize_search_text(data["title"])
        if "body" in data and data["body"]:
            doc.body = sanitize_search_text(data["body"])
        if "tags" in data and data["tags"] is not None:
            doc.tags = data["tags"]
        if "category_id" in data and data["category_id"]:
            doc.category_id = uuid.UUID(data["category_id"])
        if "status" in data:
            doc.is_visible = data["status"] == "published"

        doc.version += 1
        doc.source_updated_at = now
        doc.indexed_at = now
        return doc

    @classmethod
    async def _handle_thread_deleted(
        cls,
        session: AsyncSession,
        event: ConsumedEventPayload,
    ) -> SearchDocument | None:
        data = event.data
        thread_id_str = data.get("thread_id")
        if not thread_id_str:
            return None
        thread_id = uuid.UUID(thread_id_str)

        stmt = select(SearchDocument).where(
            SearchDocument.source_type == "thread",
            SearchDocument.source_id == thread_id,
        )
        doc = (await session.execute(stmt)).scalar_one_or_none()
        if doc:
            doc.is_visible = False
            doc.version += 1
            doc.source_updated_at = datetime.now(UTC)
            doc.indexed_at = datetime.now(UTC)
        return doc

    @classmethod
    async def _handle_thread_lock_toggled(
        cls,
        session: AsyncSession,
        event: ConsumedEventPayload,
    ) -> SearchDocument | None:
        # A locked thread remains visible in search results per architecture guidelines
        data = event.data
        thread_id_str = data.get("thread_id")
        if not thread_id_str:
            return None
        thread_id = uuid.UUID(thread_id_str)

        stmt = select(SearchDocument).where(
            SearchDocument.source_type == "thread",
            SearchDocument.source_id == thread_id,
        )
        doc = (await session.execute(stmt)).scalar_one_or_none()
        if doc:
            doc.version += 1
            doc.source_updated_at = datetime.now(UTC)
        return doc

    @classmethod
    async def _handle_thread_pin_toggled(
        cls,
        session: AsyncSession,
        event: ConsumedEventPayload,
    ) -> SearchDocument | None:
        data = event.data
        thread_id_str = data.get("thread_id")
        if not thread_id_str:
            return None
        thread_id = uuid.UUID(thread_id_str)

        stmt = select(SearchDocument).where(
            SearchDocument.source_type == "thread",
            SearchDocument.source_id == thread_id,
        )
        doc = (await session.execute(stmt)).scalar_one_or_none()
        if doc:
            doc.version += 1
            doc.source_updated_at = datetime.now(UTC)
        return doc

    @classmethod
    async def _handle_thread_view_count_incremented(
        cls,
        session: AsyncSession,
        event: ConsumedEventPayload,
    ) -> SearchDocument | None:
        # DEC-005 ThreadViewCountIncremented
        data = event.data
        thread_id_str = data.get("thread_id")
        if not thread_id_str:
            return None
        thread_id = uuid.UUID(thread_id_str)
        inc = data.get("increment", 1)

        stmt = select(SearchDocument).where(
            SearchDocument.source_type == "thread",
            SearchDocument.source_id == thread_id,
        )
        doc = (await session.execute(stmt)).scalar_one_or_none()
        if doc:
            doc.view_count += inc
            doc.source_updated_at = datetime.now(UTC)
        return doc

    @classmethod
    async def _handle_moderation_action_applied(
        cls,
        session: AsyncSession,
        event: ConsumedEventPayload,
    ) -> SearchDocument | None:
        # Moderation actions: lock, pin, soft_delete, etc.
        data = event.data
        target_type = data.get("target_type")
        target_id_str = data.get("target_id")
        action = data.get("action")

        if target_type != "thread" or not target_id_str:
            return None

        thread_id = uuid.UUID(target_id_str)
        stmt = select(SearchDocument).where(
            SearchDocument.source_type == "thread",
            SearchDocument.source_id == thread_id,
        )
        doc = (await session.execute(stmt)).scalar_one_or_none()
        if not doc:
            return None

        if action in ("soft_delete", "delete", "hide"):
            doc.is_visible = False
        elif action in ("unhide", "restore"):
            doc.is_visible = True

        doc.version += 1
        doc.source_updated_at = datetime.now(UTC)
        doc.indexed_at = datetime.now(UTC)
        return doc
