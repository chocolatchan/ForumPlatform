"""Add email_deliveries table for Notification Service.

Revision ID: notifications_0003
Revises: notifications_0002
Create Date: 2026-10-22 22:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "notifications_0003"
down_revision: str | None = "notifications_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_deliveries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_email", sa.String(254), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("category", sa.String(32), nullable=False, server_default=sa.text("'optional_activity'")),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("event_id", "recipient_id", name="uq_email_delivery_event_recipient"),
        schema="notifications",
    )
    op.create_index(
        "ix_notifications_email_deliveries_status_next_attempt",
        "email_deliveries",
        ["status", "next_attempt_at"],
        schema="notifications",
    )
    op.create_index(
        "ix_notifications_email_deliveries_recipient_id",
        "email_deliveries",
        ["recipient_id"],
        schema="notifications",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notifications_email_deliveries_recipient_id",
        table_name="email_deliveries",
        schema="notifications",
    )
    op.drop_index(
        "ix_notifications_email_deliveries_status_next_attempt",
        table_name="email_deliveries",
        schema="notifications",
    )
    op.drop_table("email_deliveries", schema="notifications")
