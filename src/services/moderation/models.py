"""Moderation Service ORM Models (DEC-004 Reconciled)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.common.models import Base, TimestampMixin
from src.common.outbox import OutboxEventMixin


class Report(Base, TimestampMixin):
    """Abuse reports against threads, comments, or accounts."""

    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint(
            "(target_type = 'thread' AND thread_id IS NOT NULL AND comment_id IS NULL AND reported_account_id IS NULL) OR "
            "(target_type = 'comment' AND comment_id IS NOT NULL AND thread_id IS NULL AND reported_account_id IS NULL) OR "
            "(target_type = 'account' AND reported_account_id IS NOT NULL AND thread_id IS NULL AND comment_id IS NULL)",
            name="ck_report_exactly_one_target",
        ),
        CheckConstraint(
            "reported_account_id IS NULL OR reported_account_id <> reporter_id",
            name="ck_report_not_self_account",
        ),
        CheckConstraint(
            "status IN ('pending', 'reviewing', 'actioned', 'dismissed')",
            name="ck_report_status",
        ),
        {"schema": "moderation"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reporter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    comment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    reported_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    actions: Mapped[list["ModerationAction"]] = relationship(
        "ModerationAction", back_populates="report"
    )


class ModerationAction(Base):
    """Audit log of moderation decisions and enforcement actions."""

    __tablename__ = "actions"
    __table_args__ = {"schema": "moderation"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("moderation.reports.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    moderator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    report: Mapped[Report | None] = relationship("Report", back_populates="actions")


class ModerationOutboxEvent(Base, OutboxEventMixin):
    """Transactional outbox for Moderation Service domain events."""

    __tablename__ = "outbox_events"
    __table_args__ = {"schema": "moderation"}
