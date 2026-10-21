"""Initial baseline migration for Content Service (forum_content).

Revision ID: content_0001
Revises: None
Create Date: 2026-10-20 18:43:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "content_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS content;")

    op.create_table(
        "categories",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("slug", sa.String(100), unique=True, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_category_name_nonempty"),
        schema="content",
    )
    op.create_index("ix_categories_slug", "categories", ["slug"], schema="content")

    op.create_table(
        "threads",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content.categories.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("best_answer_comment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("view_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("char_length(title) BETWEEN 10 AND 150", name="ck_thread_title_length"),
        sa.CheckConstraint("char_length(trim(body)) >= 20", name="ck_thread_body_length"),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'hidden', 'deleted')", name="ck_thread_status"
        ),
        sa.CheckConstraint("view_count >= 0", name="ck_thread_view_count"),
        schema="content",
    )
    op.create_index(
        "ix_threads_category", "threads", ["category_id", "created_at"], schema="content"
    )
    op.create_index("ix_threads_author", "threads", ["author_id", "created_at"], schema="content")
    op.create_index("ix_threads_status", "threads", ["status"], schema="content")

    op.create_table(
        "thread_tags",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content.threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tag", sa.String(40), nullable=False),
        sa.Column("tag_normalized", sa.String(40), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("thread_id", "tag_normalized", name="uq_thread_tag"),
        schema="content",
    )
    op.create_index(
        "ix_thread_tags_normalized", "thread_tags", ["tag_normalized"], schema="content"
    )

    op.create_table(
        "comments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content.threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "parent_comment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content.comments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(trim(body)) > 0", name="ck_comment_body_nonempty"),
        sa.CheckConstraint(
            "parent_comment_id IS NULL OR parent_comment_id <> id",
            name="ck_comment_parent_not_self",
        ),
        schema="content",
    )
    op.create_index("ix_comments_thread", "comments", ["thread_id", "created_at"], schema="content")
    op.create_index("ix_comments_author", "comments", ["author_id", "created_at"], schema="content")

    op.create_foreign_key(
        "fk_thread_best_answer",
        "threads",
        "comments",
        ["best_answer_comment_id"],
        ["id"],
        source_schema="content",
        referent_schema="content",
        ondelete="SET NULL",
    )

    op.create_table(
        "attachments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content.threads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_key", sa.Text(), unique=True, nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("file_size > 0 AND file_size <= 2097152", name="ck_attachment_size"),
        sa.CheckConstraint(
            "mime_type IN ('image/jpeg', 'image/png', 'image/webp')", name="ck_attachment_mime"
        ),
        schema="content",
    )
    op.create_index("ix_attachments_thread", "attachments", ["thread_id"], schema="content")
    op.create_index(
        "ix_attachments_orphan", "attachments", ["status", "created_at"], schema="content"
    )

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
        schema="content",
    )
    op.create_index(
        "ix_content_outbox_unprocessed", "outbox_events", ["occurred_at"], schema="content"
    )


def downgrade() -> None:
    op.drop_table("outbox_events", schema="content")
    op.drop_table("attachments", schema="content")
    op.drop_constraint("fk_thread_best_answer", "threads", schema="content", type_="foreignkey")
    op.drop_table("comments", schema="content")
    op.drop_table("thread_tags", schema="content")
    op.drop_table("threads", schema="content")
    op.drop_table("categories", schema="content")
