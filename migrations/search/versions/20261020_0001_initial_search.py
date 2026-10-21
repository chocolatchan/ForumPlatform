"""Initial baseline migration for Search Service (forum_search).

Revision ID: search_0001
Revises: None
Create Date: 2026-10-20 18:45:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "search_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS search;")

    # We use raw DDL for documents table because of generated column with tsvector
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS search.documents (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_type         VARCHAR(24) NOT NULL,
            source_id           UUID NOT NULL,
            title               TEXT,
            body                TEXT,
            author_id           UUID,
            category_id         UUID,
            search_vector       TSVECTOR GENERATED ALWAYS AS (
                setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
                setweight(to_tsvector('simple', coalesce(body, '')), 'B')
            ) STORED,
            source_updated_at   TIMESTAMPTZ NOT NULL,
            indexed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_search_document_source UNIQUE (source_type, source_id),
            CONSTRAINT ck_search_source_type CHECK (source_type IN ('thread', 'comment', 'account'))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_search_documents_vector ON search.documents USING GIN(search_vector);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_search_documents_category ON search.documents(category_id) WHERE category_id IS NOT NULL;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_search_documents_author ON search.documents(author_id) WHERE author_id IS NOT NULL;"
    )


def downgrade() -> None:
    op.drop_table("documents", schema="search")
