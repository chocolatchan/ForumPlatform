"""Content Service ORM Models."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.common.models import Base, TimestampMixin
from src.common.outbox import OutboxEventMixin


class Category(Base, TimestampMixin):
    """Discussion categories."""

    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_category_name_nonempty"),
        {"schema": "content"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    threads: Mapped[list["Thread"]] = relationship("Thread", back_populates="category")


class Thread(Base, TimestampMixin):
    """Forum threads."""

    __tablename__ = "threads"
    __table_args__ = (
        CheckConstraint("char_length(title) BETWEEN 10 AND 150", name="ck_thread_title_length"),
        CheckConstraint("char_length(trim(body)) >= 20", name="ck_thread_body_length"),
        CheckConstraint(
            "status IN ('draft', 'published', 'hidden', 'deleted')", name="ck_thread_status"
        ),
        CheckConstraint("view_count >= 0", name="ck_thread_view_count"),
        {"schema": "content"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content.categories.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="published", nullable=False, index=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    best_answer_comment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    view_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    category: Mapped[Category] = relationship("Category", back_populates="threads")
    comments: Mapped[list["Comment"]] = relationship(
        "Comment", back_populates="thread", cascade="all, delete-orphan"
    )
    tags: Mapped[list["ThreadTag"]] = relationship(
        "ThreadTag", back_populates="thread", cascade="all, delete-orphan", lazy="selectin"
    )
    attachments: Mapped[list["Attachment"]] = relationship("Attachment", back_populates="thread")


class ThreadTag(Base):
    """Thread tags."""

    __tablename__ = "thread_tags"
    __table_args__ = (
        UniqueConstraint("thread_id", "tag_normalized", name="uq_thread_tag"),
        {"schema": "content"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content.threads.id", ondelete="CASCADE"), nullable=False
    )
    tag: Mapped[str] = mapped_column(String(40), nullable=False)
    tag_normalized: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    thread: Mapped[Thread] = relationship("Thread", back_populates="tags")


class Comment(Base, TimestampMixin):
    """Thread comments and replies (max 2 levels)."""

    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint("length(trim(body)) > 0", name="ck_comment_body_nonempty"),
        CheckConstraint(
            "parent_comment_id IS NULL OR parent_comment_id <> id",
            name="ck_comment_parent_not_self",
        ),
        {"schema": "content"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content.threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    parent_comment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content.comments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    thread: Mapped[Thread] = relationship("Thread", back_populates="comments")
    replies: Mapped[list["Comment"]] = relationship(
        "Comment", back_populates="parent", cascade="all"
    )
    parent: Mapped["Comment | None"] = relationship(
        "Comment", back_populates="replies", remote_side=[id]
    )


class Attachment(Base):
    """Thread image attachments."""

    __tablename__ = "attachments"
    __table_args__ = (
        CheckConstraint("file_size > 0 AND file_size <= 2097152", name="ck_attachment_size"),
        CheckConstraint(
            "mime_type IN ('image/jpeg', 'image/png', 'image/webp')", name="ck_attachment_mime"
        ),
        {"schema": "content"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content.threads.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    thread: Mapped[Thread | None] = relationship("Thread", back_populates="attachments")


class ContentOutboxEvent(Base, OutboxEventMixin):
    """Transactional outbox for Content Service domain events."""

    __tablename__ = "outbox_events"
    __table_args__ = {"schema": "content"}


class ConsumedEvent(Base):
    """Event consumption idempotency and tracking record for Content Service."""

    __tablename__ = "consumed_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_content_consumed_event"),
        {"schema": "content"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="processed", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
