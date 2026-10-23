"""Add sending_since column for lease-based recovery of stuck email_deliveries rows.

Revision ID: notifications_0004
Revises: notifications_0003
Create Date: 2026-10-22 23:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "notifications_0004"
down_revision: str | None = "notifications_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "email_deliveries",
        sa.Column("sending_since", sa.DateTime(timezone=True), nullable=True),
        schema="notifications",
    )


def downgrade() -> None:
    op.drop_column("email_deliveries", "sending_since", schema="notifications")
