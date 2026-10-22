"""Add resend columns and attempt constraints to users email_change_requests.

Revision ID: users_0003
Revises: users_0002
Create Date: 2026-10-22 16:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "users_0003"
down_revision: str | None = "users_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "email_change_requests",
        sa.Column("resend_count", sa.SmallInteger(), nullable=False, server_default="0"),
        schema="users",
    )
    op.add_column(
        "email_change_requests",
        sa.Column(
            "resend_available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="users",
    )
    op.create_index(
        "ix_email_change_requests_account_id",
        "email_change_requests",
        ["account_id"],
        schema="users",
    )
    op.create_index(
        "ix_email_change_requests_new_email_norm",
        "email_change_requests",
        ["new_email_normalized"],
        schema="users",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_change_requests_new_email_norm",
        table_name="email_change_requests",
        schema="users",
    )
    op.drop_index(
        "ix_email_change_requests_account_id",
        table_name="email_change_requests",
        schema="users",
    )
    op.drop_column("email_change_requests", "resend_available_at", schema="users")
    op.drop_column("email_change_requests", "resend_count", schema="users")
