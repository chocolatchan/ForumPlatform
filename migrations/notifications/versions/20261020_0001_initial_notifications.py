"""Initial baseline migration for Notification Service (forum_notifications).

Revision ID: notifications_0001
Revises: None
Create Date: 2026-10-20 18:45:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "notifications_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS notifications;")

    op.create_table(
        "preferences",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("in_app_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema="notifications",
    )

    op.create_table(
        "items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("recipient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("target_type", sa.String(24), nullable=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(is_read = FALSE AND read_at IS NULL) OR (is_read = TRUE AND read_at IS NOT NULL)",
            name="ck_notification_read_state",
        ),
        schema="notifications",
    )
    op.create_index(
        "ix_notifications_inbox",
        "items",
        ["recipient_id", sa.text("created_at DESC")],
        schema="notifications",
    )
    op.create_index(
        "ix_notifications_unread",
        "items",
        ["recipient_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("is_read = FALSE"),
        schema="notifications",
    )


def downgrade() -> None:
    op.drop_table("items", schema="notifications")
    op.drop_table("preferences", schema="notifications")
