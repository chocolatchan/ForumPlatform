"""Initial baseline migration for Interaction Service (forum_interactions).

Revision ID: interactions_0001
Revises: None
Create Date: 2026-10-20 18:43:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "interactions_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS interactions;")

    op.create_table(
        "votes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("voter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("comment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("value", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("value IN (-1, 1)", name="ck_vote_value"),
        sa.CheckConstraint(
            "(target_type = 'thread' AND thread_id IS NOT NULL AND comment_id IS NULL) OR "
            "(target_type = 'comment' AND comment_id IS NOT NULL AND thread_id IS NULL)",
            name="ck_vote_exactly_one_target",
        ),
        sa.UniqueConstraint("voter_id", "thread_id", name="uq_vote_thread"),
        sa.UniqueConstraint("voter_id", "comment_id", name="uq_vote_comment"),
        schema="interactions",
    )
    op.create_index("ix_votes_voter", "votes", ["voter_id"], schema="interactions")
    op.create_index("ix_votes_thread", "votes", ["thread_id", "value"], schema="interactions")
    op.create_index("ix_votes_comment", "votes", ["comment_id", "value"], schema="interactions")

    op.create_table(
        "bookmarks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("user_id", "thread_id", name="uq_bookmark_user_thread"),
        schema="interactions",
    )
    op.create_index("ix_bookmarks_user", "bookmarks", ["user_id"], schema="interactions")

    op.create_table(
        "thread_views",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_key_hash", sa.Text(), nullable=False),
        sa.Column(
            "viewed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("thread_id", "session_key_hash", name="uq_thread_view_window"),
        schema="interactions",
    )
    op.create_index(
        "ix_thread_views_viewed_at", "thread_views", ["viewed_at"], schema="interactions"
    )

    op.create_table(
        "reputation_ledger",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("caused_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("delta <> 0", name="ck_reputation_delta_nonzero"),
        sa.UniqueConstraint(
            "source_type", "source_id", "caused_by_user_id", "reason", name="uq_reputation_event"
        ),
        schema="interactions",
    )
    op.create_index("ix_reputation_user", "reputation_ledger", ["user_id"], schema="interactions")

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
        schema="interactions",
    )
    op.create_index(
        "ix_interactions_outbox_unprocessed",
        "outbox_events",
        ["occurred_at"],
        schema="interactions",
    )


def downgrade() -> None:
    op.drop_table("outbox_events", schema="interactions")
    op.drop_table("reputation_ledger", schema="interactions")
    op.drop_table("thread_views", schema="interactions")
    op.drop_table("bookmarks", schema="interactions")
    op.drop_table("votes", schema="interactions")
