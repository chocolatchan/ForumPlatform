"""Initial baseline migration for Moderation Service (forum_moderation).

Revision ID: moderation_0001
Revises: None
Create Date: 2026-10-20 18:43:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "moderation_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS moderation;")

    op.create_table(
        "reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("reporter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("comment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reported_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("assigned_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(target_type = 'thread' AND thread_id IS NOT NULL AND comment_id IS NULL AND reported_account_id IS NULL) OR "
            "(target_type = 'comment' AND comment_id IS NOT NULL AND thread_id IS NULL AND reported_account_id IS NULL) OR "
            "(target_type = 'account' AND reported_account_id IS NOT NULL AND thread_id IS NULL AND comment_id IS NULL)",
            name="ck_report_exactly_one_target",
        ),
        sa.CheckConstraint(
            "reported_account_id IS NULL OR reported_account_id <> reporter_id",
            name="ck_report_not_self_account",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'reviewing', 'actioned', 'dismissed')",
            name="ck_report_status",
        ),
        schema="moderation",
    )
    op.create_index("ix_reports_reporter", "reports", ["reporter_id"], schema="moderation")
    op.create_index("ix_reports_status", "reports", ["status", "created_at"], schema="moderation")

    op.create_table(
        "actions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "report_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("moderation.reports.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("moderator_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema="moderation",
    )
    op.create_index(
        "ix_moderation_actions_target",
        "actions",
        ["target_type", "target_id", "created_at"],
        schema="moderation",
    )
    op.create_index(
        "ix_moderation_actions_moderator",
        "actions",
        ["moderator_id", "created_at"],
        schema="moderation",
    )
    op.create_index("ix_moderation_actions_report", "actions", ["report_id"], schema="moderation")

    op.create_table(
        "outbox_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        schema="moderation",
    )
    op.create_index(
        "ix_moderation_outbox_unprocessed", "outbox_events", ["occurred_at"], schema="moderation"
    )


def downgrade() -> None:
    op.drop_table("outbox_events", schema="moderation")
    op.drop_table("actions", schema="moderation")
    op.drop_table("reports", schema="moderation")
