"""Migration for Search Service: extend documents table and add consumed_events.

Revision ID: search_0002
Revises: search_0001
Create Date: 2026-10-21 01:30:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "search_0002"
down_revision: str | None = "search_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add new columns to search.documents
    op.execute(
        """
        ALTER TABLE search.documents
            ADD COLUMN IF NOT EXISTS tags JSONB,
            ADD COLUMN IF NOT EXISTS is_visible BOOLEAN NOT NULL DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS view_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1,
            ADD COLUMN IF NOT EXISTS source_created_at TIMESTAMPTZ;
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_search_documents_visibility ON search.documents(is_visible);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_search_documents_view_count ON search.documents(view_count DESC);"
    )

    # 2. Create search.consumed_events table for idempotency
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS search.consumed_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            event_id UUID NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'processed',
            attempts INTEGER NOT NULL DEFAULT 1,
            error_summary TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            processed_at TIMESTAMPTZ,
            metadata JSONB,
            CONSTRAINT uq_search_consumed_event UNIQUE (event_id)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_search_consumed_events_event_id ON search.consumed_events(event_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS search.consumed_events;")
    op.execute(
        """
        ALTER TABLE search.documents
            DROP COLUMN IF EXISTS tags,
            DROP COLUMN IF EXISTS is_visible,
            DROP COLUMN IF EXISTS view_count,
            DROP COLUMN IF EXISTS version,
            DROP COLUMN IF EXISTS source_created_at;
        """
    )
