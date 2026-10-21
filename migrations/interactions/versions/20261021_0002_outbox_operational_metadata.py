"""Add retry and operational metadata columns to interactions outbox_events.

Revision ID: interactions_0002
Revises: interactions_0001
Create Date: 2026-10-21 10:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "interactions_0002"
down_revision: str | None = "interactions_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        schema="interactions",
    )
    op.add_column(
        "outbox_events",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        schema="interactions",
    )
    op.add_column(
        "outbox_events",
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        schema="interactions",
    )
    op.add_column(
        "outbox_events",
        sa.Column("last_error", sa.Text(), nullable=True),
        schema="interactions",
    )
    op.add_column(
        "outbox_events",
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        schema="interactions",
    )
    op.add_column(
        "outbox_events",
        sa.Column("locked_by", sa.String(64), nullable=True),
        schema="interactions",
    )
    op.create_index(
        "ix_interactions_outbox_pending",
        "outbox_events",
        ["status", "next_retry_at"],
        schema="interactions",
    )


def downgrade() -> None:
    op.drop_index("ix_interactions_outbox_pending", table_name="outbox_events", schema="interactions")
    op.drop_column("outbox_events", "locked_by", schema="interactions")
    op.drop_column("outbox_events", "locked_at", schema="interactions")
    op.drop_column("outbox_events", "last_error", schema="interactions")
    op.drop_column("outbox_events", "next_retry_at", schema="interactions")
    op.drop_column("outbox_events", "attempts", schema="interactions")
    op.drop_column("outbox_events", "status", schema="interactions")
