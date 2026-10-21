"""Add retry and operational metadata columns to moderation outbox_events.

Revision ID: moderation_0003
Revises: moderation_0002
Create Date: 2026-10-21 10:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "moderation_0003"
down_revision: str | None = "moderation_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        schema="moderation",
    )
    op.add_column(
        "outbox_events",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        schema="moderation",
    )
    op.add_column(
        "outbox_events",
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        schema="moderation",
    )
    op.add_column(
        "outbox_events",
        sa.Column("last_error", sa.Text(), nullable=True),
        schema="moderation",
    )
    op.add_column(
        "outbox_events",
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        schema="moderation",
    )
    op.add_column(
        "outbox_events",
        sa.Column("locked_by", sa.String(64), nullable=True),
        schema="moderation",
    )
    op.create_index(
        "ix_moderation_outbox_pending",
        "outbox_events",
        ["status", "next_retry_at"],
        schema="moderation",
    )


def downgrade() -> None:
    op.drop_index("ix_moderation_outbox_pending", table_name="outbox_events", schema="moderation")
    op.drop_column("outbox_events", "locked_by", schema="moderation")
    op.drop_column("outbox_events", "locked_at", schema="moderation")
    op.drop_column("outbox_events", "last_error", schema="moderation")
    op.drop_column("outbox_events", "next_retry_at", schema="moderation")
    op.drop_column("outbox_events", "attempts", schema="moderation")
    op.drop_column("outbox_events", "status", schema="moderation")
