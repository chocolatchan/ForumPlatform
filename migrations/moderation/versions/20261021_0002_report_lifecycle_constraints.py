"""Add check constraint for report status and partial unique indexes for active reports.

Revision ID: moderation_0002
Revises: moderation_0001
Create Date: 2026-10-21 00:20:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "moderation_0002"
down_revision: str | None = "moderation_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add check constraint on report status
    op.create_check_constraint(
        "ck_report_status",
        "reports",
        "status IN ('pending', 'reviewing', 'actioned', 'dismissed')",
        schema="moderation",
    )

    # 2. Add partial unique indexes for active reports (pending, reviewing) per reporter and target
    op.create_index(
        "uq_reporter_thread_active",
        "reports",
        ["reporter_id", "thread_id"],
        unique=True,
        postgresql_where=sa_text("thread_id IS NOT NULL AND status IN ('pending', 'reviewing')"),
        schema="moderation",
    )
    op.create_index(
        "uq_reporter_comment_active",
        "reports",
        ["reporter_id", "comment_id"],
        unique=True,
        postgresql_where=sa_text("comment_id IS NOT NULL AND status IN ('pending', 'reviewing')"),
        schema="moderation",
    )
    op.create_index(
        "uq_reporter_account_active",
        "reports",
        ["reporter_id", "reported_account_id"],
        unique=True,
        postgresql_where=sa_text(
            "reported_account_id IS NOT NULL AND status IN ('pending', 'reviewing')"
        ),
        schema="moderation",
    )


def sa_text(sql: str):
    import sqlalchemy as sa

    return sa.text(sql)


def downgrade() -> None:
    op.drop_index("uq_reporter_account_active", table_name="reports", schema="moderation")
    op.drop_index("uq_reporter_comment_active", table_name="reports", schema="moderation")
    op.drop_index("uq_reporter_thread_active", table_name="reports", schema="moderation")
    op.drop_constraint("ck_report_status", "reports", type_="check", schema="moderation")
