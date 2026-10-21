"""Add consumed_events table for Notification Service.

Revision ID: notifications_0002
Revises: notifications_0001
Create Date: 2026-10-21 00:50:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "notifications_0002"
down_revision: str | None = "notifications_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "consumed_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("recipient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'processed'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.UniqueConstraint("event_id", "recipient_id", name="uq_consumed_event_recipient"),
        schema="notifications",
    )
    op.create_index(
        "ix_notifications_consumed_events_event_id",
        "consumed_events",
        ["event_id"],
        schema="notifications",
    )
    op.create_index(
        "ix_notifications_consumed_events_recipient_id",
        "consumed_events",
        ["recipient_id"],
        schema="notifications",
    )


def downgrade() -> None:
    op.drop_table("consumed_events", schema="notifications")
