"""Add consumed_events table to content schema for event idempotency.

Revision ID: content_0003
Revises: content_0002
Create Date: 2026-10-21 11:15:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "content_0003"
down_revision: str | None = "content_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "consumed_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="processed"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.UniqueConstraint("event_id", name="uq_content_consumed_event"),
        schema="content",
    )
    op.create_index(
        "ix_content_consumed_events_event_id",
        "consumed_events",
        ["event_id"],
        schema="content",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_content_consumed_events_event_id",
        table_name="consumed_events",
        schema="content",
    )
    op.drop_table("consumed_events", schema="content")
