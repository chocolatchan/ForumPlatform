"""Interaction Service ORM Models."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.common.models import Base, TimestampMixin
from src.common.outbox import OutboxEventMixin


class Vote(Base, TimestampMixin):
    """Content votes (+1 / -1)."""

    __tablename__ = "votes"
    __table_args__ = (
        CheckConstraint("value IN (-1, 1)", name="ck_vote_value"),
        CheckConstraint(
            "(target_type = 'thread' AND thread_id IS NOT NULL AND comment_id IS NULL) OR "
            "(target_type = 'comment' AND comment_id IS NOT NULL AND thread_id IS NULL)",
            name="ck_vote_exactly_one_target",
        ),
        UniqueConstraint("voter_id", "thread_id", name="uq_vote_thread"),
        UniqueConstraint("voter_id", "comment_id", name="uq_vote_comment"),
        {"schema": "interactions"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    voter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    comment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    value: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class Bookmark(Base):
    """User bookmarks."""

    __tablename__ = "bookmarks"
    __table_args__ = (
        UniqueConstraint("user_id", "thread_id", name="uq_bookmark_user_thread"),
        {"schema": "interactions"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ThreadView(Base):
    """Thread view deduplication log (30-minute window)."""

    __tablename__ = "thread_views"
    __table_args__ = (
        UniqueConstraint("thread_id", "session_key_hash", name="uq_thread_view_window"),
        {"schema": "interactions"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    session_key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ReputationLedger(Base):
    """Reputation change ledger."""

    __tablename__ = "reputation_ledger"
    __table_args__ = (
        CheckConstraint("delta <> 0", name="ck_reputation_delta_nonzero"),
        UniqueConstraint(
            "source_type", "source_id", "caused_by_user_id", "reason", name="uq_reputation_event"
        ),
        {"schema": "interactions"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    caused_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InteractionOutboxEvent(Base, OutboxEventMixin):
    """Transactional outbox for Interaction Service domain events."""

    __tablename__ = "outbox_events"
    __table_args__ = {"schema": "interactions"}
