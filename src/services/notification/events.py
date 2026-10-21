"""Event processing and handler registry for Notification Service."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.notification.models import ConsumedEvent, NotificationItem, NotificationPreference
from src.services.notification.schemas import ConsumedEventPayload

logger = logging.getLogger(__name__)


def sanitize_text(text: str | None, max_len: int = 160) -> str:
    """Sanitize and clamp untrusted notification text for display safety."""
    if not text:
        return ""
    # Strip dangerous HTML/script characters or whitespace
    clean = text.replace("<", "&lt;").replace(">", "&gt;").strip()
    if len(clean) > max_len:
        return clean[: max_len - 3] + "..."
    return clean


class EventHandlerRegistry:
    """Dispatches domain events to dedicated handlers for notification creation."""

    SUPPORTED_EVENTS = {
        "UserRegistered",
        "CommentCreated",
        "BestAnswerChanged",
        "VoteChanged",
        "ModerationActionApplied",
    }

    @classmethod
    async def process_event(
        cls,
        session: AsyncSession,
        event: ConsumedEventPayload,
    ) -> list[NotificationItem]:
        """Process a domain event atomically within the provided database session.

        Returns list of newly created NotificationItem records, or empty list if skipped/deduplicated.
        """
        event_type = event.event_type
        if event_type not in cls.SUPPORTED_EVENTS:
            logger.info("Ignoring unsupported event type: %s", event_type)
            return []

        handler_map = {
            "UserRegistered": cls._handle_user_registered,
            "CommentCreated": cls._handle_comment_created,
            "BestAnswerChanged": cls._handle_best_answer_changed,
            "VoteChanged": cls._handle_vote_changed,
            "ModerationActionApplied": cls._handle_moderation_action_applied,
        }
        handler = handler_map.get(event_type)
        if not handler:
            return []

        # Handler determines target recipient(s) and notification metadata
        notifications_to_create = await handler(event)
        if not notifications_to_create:
            return []

        created_items: list[NotificationItem] = []

        for notif_data in notifications_to_create:
            recipient_id = notif_data["recipient_id"]

            # 1. Idempotency check: verify if (event_id, recipient_id) already consumed
            stmt = select(ConsumedEvent).where(
                ConsumedEvent.event_id == event.event_id,
                ConsumedEvent.recipient_id == recipient_id,
            )
            existing_consumed = (await session.execute(stmt)).scalar_one_or_none()
            if existing_consumed:
                logger.info(
                    "Duplicate event %s for recipient %s already consumed.",
                    event.event_id,
                    recipient_id,
                )
                continue

            # 2. Check user notification preferences
            pref_stmt = select(NotificationPreference).where(
                NotificationPreference.user_id == recipient_id
            )
            pref = (await session.execute(pref_stmt)).scalar_one_or_none()
            # Default preferences: in_app_enabled=True
            in_app_enabled = pref.in_app_enabled if pref is not None else True

            if not in_app_enabled:
                logger.info(
                    "Recipient %s has disabled in-app notifications. Skipping notification item creation.",
                    recipient_id,
                )
                # Still record event consumption as processed so it won't be retried
                consumed = ConsumedEvent(
                    id=uuid.uuid4(),
                    event_id=event.event_id,
                    event_type=event.event_type,
                    recipient_id=recipient_id,
                    status="skipped_by_preference",
                    attempts=1,
                    processed_at=datetime.now(UTC),
                )
                session.add(consumed)
                continue

            # 3. Create NotificationItem
            item = NotificationItem(
                id=uuid.uuid4(),
                recipient_id=recipient_id,
                actor_id=notif_data.get("actor_id"),
                type=notif_data["type"],
                title=sanitize_text(notif_data["title"], max_len=160),
                body=sanitize_text(notif_data["body"], max_len=1000),
                target_type=notif_data.get("target_type"),
                target_id=notif_data.get("target_id"),
                is_read=False,
                created_at=datetime.now(UTC),
                read_at=None,
            )
            session.add(item)
            created_items.append(item)

            # 4. Record ConsumedEvent record atomically
            consumed = ConsumedEvent(
                id=uuid.uuid4(),
                event_id=event.event_id,
                event_type=event.event_type,
                recipient_id=recipient_id,
                status="processed",
                attempts=1,
                processed_at=datetime.now(UTC),
                metadata_={"notification_id": str(item.id)},
            )
            session.add(consumed)

        return created_items

    @staticmethod
    async def _handle_user_registered(event: ConsumedEventPayload) -> list[dict[str, Any]]:
        """User account created/activated welcome notification."""
        user_id_raw = event.data.get("user_id") or event.data.get("id")
        if not user_id_raw:
            return []
        try:
            recipient_id = uuid.UUID(str(user_id_raw))
        except ValueError:
            return []

        username = event.data.get("username", "there")
        return [
            {
                "recipient_id": recipient_id,
                "actor_id": None,
                "type": "welcome",
                "title": "Welcome to the Forum!",
                "body": f"Hello {username}, your account has been registered and verified successfully.",
                "target_type": "account",
                "target_id": recipient_id,
            }
        ]

    @staticmethod
    async def _handle_comment_created(event: ConsumedEventPayload) -> list[dict[str, Any]]:
        """Notify parent comment author or thread author about new comment. Exclude self-notifications."""
        author_id_raw = event.data.get("author_id")
        recipient_id_raw = event.data.get("recipient_id") or event.data.get("thread_author_id")
        comment_id_raw = event.data.get("comment_id")

        if not recipient_id_raw:
            return []

        try:
            recipient_id = uuid.UUID(str(recipient_id_raw))
            actor_id = uuid.UUID(str(author_id_raw)) if author_id_raw else None
            comment_id = uuid.UUID(str(comment_id_raw)) if comment_id_raw else None
        except ValueError:
            return []

        # Exclude self-notifications
        if actor_id and recipient_id == actor_id:
            return []

        parent_comment_id = event.data.get("parent_comment_id")
        title = "New reply to your comment" if parent_comment_id else "New comment on your thread"
        body = event.data.get("body_preview") or "Someone commented on your discussion."

        return [
            {
                "recipient_id": recipient_id,
                "actor_id": actor_id,
                "type": "comment",
                "title": title,
                "body": body,
                "target_type": "comment",
                "target_id": comment_id,
            }
        ]

    @staticmethod
    async def _handle_best_answer_changed(event: ConsumedEventPayload) -> list[dict[str, Any]]:
        """Notify comment author that their comment was marked as best answer."""
        author_id_raw = event.data.get("comment_author_id")
        thread_id_raw = event.data.get("thread_id")
        comment_id_raw = event.data.get("comment_id")

        if not author_id_raw:
            return []

        try:
            recipient_id = uuid.UUID(str(author_id_raw))
            thread_id = uuid.UUID(str(thread_id_raw)) if thread_id_raw else None
            comment_id = uuid.UUID(str(comment_id_raw)) if comment_id_raw else None
        except ValueError:
            return []

        return [
            {
                "recipient_id": recipient_id,
                "actor_id": None,
                "type": "best_answer",
                "title": "Your answer was accepted!",
                "body": "The author accepted your answer as the best answer for their thread.",
                "target_type": "thread",
                "target_id": thread_id or comment_id,
            }
        ]

    @staticmethod
    async def _handle_vote_changed(event: ConsumedEventPayload) -> list[dict[str, Any]]:
        """Notify author about positive reaction (upvote). Exclude self-votes and toggles off."""
        author_id_raw = event.data.get("author_id")
        voter_id_raw = event.data.get("voter_id")
        my_vote = event.data.get("my_vote")
        target_type = event.data.get("target_type", "thread")
        target_id_raw = event.data.get("target_id")

        if not author_id_raw or my_vote != 1:
            # Only notify on positive upvotes, not downvotes or cancellations
            return []

        try:
            recipient_id = uuid.UUID(str(author_id_raw))
            actor_id = uuid.UUID(str(voter_id_raw)) if voter_id_raw else None
            target_id = uuid.UUID(str(target_id_raw)) if target_id_raw else None
        except ValueError:
            return []

        # Exclude self-notifications
        if actor_id and recipient_id == actor_id:
            return []

        return [
            {
                "recipient_id": recipient_id,
                "actor_id": actor_id,
                "type": "upvote",
                "title": f"Someone upvoted your {target_type}",
                "body": f"Your {target_type} received a new upvote.",
                "target_type": target_type,
                "target_id": target_id,
            }
        ]

    @staticmethod
    async def _handle_moderation_action_applied(
        event: ConsumedEventPayload,
    ) -> list[dict[str, Any]]:
        """Notify affected account or content author when moderation action is applied."""
        action = event.data.get("action")
        target_type = event.data.get("target_type")
        target_id_raw = event.data.get("target_id")
        recipient_id_raw = event.data.get("affected_user_id") or event.data.get("author_id")
        reason = event.data.get("reason", "Violated community guidelines")

        # For account actions (ban/unban/role_change), recipient is the target account itself
        if target_type == "account" and target_id_raw and not recipient_id_raw:
            recipient_id_raw = target_id_raw

        if not recipient_id_raw or not target_id_raw:
            return []

        try:
            recipient_id = uuid.UUID(str(recipient_id_raw))
            target_id = uuid.UUID(str(target_id_raw))
        except ValueError:
            return []

        action_str = str(action) if action is not None else "unknown"
        action_titles = {
            "lock": "Your thread was locked by a moderator",
            "pin": "Your thread was pinned by a moderator",
            "soft_delete": f"Your {target_type} was removed by a moderator",
            "ban": "Your account has been banned",
            "unban": "Your account has been unbanned",
            "role_change": "Your account role has changed",
            "dismiss_report": "Your report was reviewed and dismissed",
        }
        title = action_titles.get(action_str, f"Moderation action applied: {action_str}")

        return [
            {
                "recipient_id": recipient_id,
                "actor_id": None,
                "type": "moderation",
                "title": title,
                "body": f"Reason: {reason}",
                "target_type": target_type,
                "target_id": target_id,
            }
        ]
