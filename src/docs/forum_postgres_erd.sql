-- Forum Platform Reconciled Schema (Task 002 Baseline)
-- PostgreSQL 16+
-- Logical Architecture: 6 independent databases per service:
--   1. forum_users         (User Service)
--   2. forum_content       (Content Service)
--   3. forum_interactions  (Interaction Service)
--   4. forum_moderation    (Moderation Service)
--   5. forum_notifications (Notification Service)
--   6. forum_search        (Search Service)
--
-- Single-Cluster / Development Representation:
-- For single-cluster development or automated testing, each service's persistence
-- is represented in its own schema below. In production or distributed deployment,
-- each schema maps 1:1 to an isolated PostgreSQL database instance.
-- In accordance with DEC-002, NO physical cross-service / cross-schema foreign keys
-- are permitted. All cross-service references are logical UUIDs.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Create service schemas (representing isolated service databases)
CREATE SCHEMA IF NOT EXISTS users;
CREATE SCHEMA IF NOT EXISTS content;
CREATE SCHEMA IF NOT EXISTS interactions;
CREATE SCHEMA IF NOT EXISTS moderation;
CREATE SCHEMA IF NOT EXISTS notifications;
CREATE SCHEMA IF NOT EXISTS search;

-- ============================================================================
-- 1. USERS SERVICE (Database: forum_users / Schema: users)
-- ============================================================================

CREATE TYPE users.account_role AS ENUM ('member', 'moderator', 'admin');
CREATE TYPE users.registration_status AS ENUM ('pending', 'verified', 'expired', 'cancelled');
CREATE TYPE users.otp_purpose AS ENUM ('registration', 'password_reset', 'email_change', 'username_recovery');
CREATE TYPE users.session_revocation_reason AS ENUM ('logout', 'password_changed', 'account_banned', 'role_changed', 'expired', 'admin_action');

CREATE TABLE users.accounts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username            VARCHAR(32) NOT NULL,
    username_normalized VARCHAR(32) NOT NULL,
    display_name        VARCHAR(50),
    email               VARCHAR(254) NOT NULL,
    email_normalized    VARCHAR(254) NOT NULL,
    password_hash       TEXT NOT NULL,
    role                users.account_role NOT NULL DEFAULT 'member',
    is_banned           BOOLEAN NOT NULL DEFAULT FALSE,
    ban_reason          TEXT,
    banned_until        TIMESTAMPTZ,
    bio                 VARCHAR(200),
    avatar_object_key   TEXT,
    email_verified_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    password_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_accounts_username_normalized UNIQUE (username_normalized),
    CONSTRAINT uq_accounts_email_normalized UNIQUE (email_normalized),
    CONSTRAINT ck_accounts_username_format CHECK (username ~ '^[A-Za-z0-9_]{3,32}$'),
    CONSTRAINT ck_accounts_username_normalized_lower CHECK (username_normalized = lower(username)),
    CONSTRAINT ck_accounts_email_normalized_lower CHECK (email_normalized = lower(email)),
    CONSTRAINT ck_accounts_ban_fields CHECK (
        (is_banned = FALSE AND ban_reason IS NULL AND banned_until IS NULL)
        OR is_banned = TRUE
    )
);

CREATE INDEX ix_accounts_role ON users.accounts(role);
CREATE INDEX ix_accounts_banned ON users.accounts(is_banned) WHERE is_banned = TRUE;

CREATE TABLE users.pending_registrations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username            VARCHAR(32) NOT NULL,
    username_normalized VARCHAR(32) NOT NULL,
    email               VARCHAR(254) NOT NULL,
    email_normalized    VARCHAR(254) NOT NULL,
    password_hash       TEXT NOT NULL,
    otp_hash            TEXT NOT NULL,
    otp_expires_at      TIMESTAMPTZ NOT NULL,
    otp_attempts        SMALLINT NOT NULL DEFAULT 0,
    resend_count        SMALLINT NOT NULL DEFAULT 0,
    resend_available_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '60 seconds'),
    status              users.registration_status NOT NULL DEFAULT 'pending',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_pending_otp_attempts CHECK (otp_attempts BETWEEN 0 AND 5),
    CONSTRAINT ck_pending_resend_count CHECK (resend_count BETWEEN 0 AND 3),
    CONSTRAINT ck_pending_expiry CHECK (otp_expires_at > created_at),
    CONSTRAINT ck_pending_username_format CHECK (username ~ '^[A-Za-z0-9_]{3,32}$'),
    CONSTRAINT ck_pending_username_normalized_lower CHECK (username_normalized = lower(username)),
    CONSTRAINT ck_pending_email_normalized_lower CHECK (email_normalized = lower(email))
);

CREATE UNIQUE INDEX uq_pending_registration_username_active
    ON users.pending_registrations(username_normalized)
    WHERE status IN ('pending', 'verified');
CREATE UNIQUE INDEX uq_pending_registration_email_active
    ON users.pending_registrations(email_normalized)
    WHERE status IN ('pending', 'verified');
CREATE INDEX ix_pending_registration_expiry
    ON users.pending_registrations(otp_expires_at)
    WHERE status = 'pending';

CREATE TABLE users.one_time_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id      UUID NOT NULL REFERENCES users.accounts(id) ON DELETE CASCADE,
    purpose         users.otp_purpose NOT NULL,
    token_hash      TEXT NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    attempts        SMALLINT NOT NULL DEFAULT 0,
    resend_count    SMALLINT NOT NULL DEFAULT 0,
    resend_after    TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '60 seconds'),
    consumed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_one_time_attempts CHECK (attempts BETWEEN 0 AND 5),
    CONSTRAINT ck_one_time_resends CHECK (resend_count BETWEEN 0 AND 3),
    CONSTRAINT ck_one_time_expiry CHECK (expires_at > created_at)
);

CREATE INDEX ix_one_time_tokens_account_purpose
    ON users.one_time_tokens(account_id, purpose, created_at DESC);
CREATE INDEX ix_one_time_tokens_expiry
    ON users.one_time_tokens(expires_at)
    WHERE consumed_at IS NULL;

CREATE TABLE users.sessions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          UUID NOT NULL REFERENCES users.accounts(id) ON DELETE CASCADE,
    session_token_hash  TEXT NOT NULL UNIQUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    idle_expires_at     TIMESTAMPTZ NOT NULL,
    absolute_expires_at TIMESTAMPTZ NOT NULL,
    revoked_at          TIMESTAMPTZ,
    revocation_reason   users.session_revocation_reason,
    user_agent          TEXT,
    ip_address          INET,
    CONSTRAINT ck_session_expiry_order CHECK (absolute_expires_at >= idle_expires_at),
    CONSTRAINT ck_session_revocation_pair CHECK (
        (revoked_at IS NULL AND revocation_reason IS NULL)
        OR (revoked_at IS NOT NULL AND revocation_reason IS NOT NULL)
    )
);

CREATE INDEX ix_sessions_account_active ON users.sessions(account_id, last_seen_at DESC) WHERE revoked_at IS NULL;
CREATE INDEX ix_sessions_expiry ON users.sessions(idle_expires_at, absolute_expires_at) WHERE revoked_at IS NULL;

CREATE TABLE users.password_history (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      UUID NOT NULL REFERENCES users.accounts(id) ON DELETE CASCADE,
    password_hash   TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_password_history_account_created ON users.password_history(account_id, created_at DESC);

CREATE TABLE users.email_change_requests (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id          UUID NOT NULL REFERENCES users.accounts(id) ON DELETE CASCADE,
    new_email           VARCHAR(254) NOT NULL,
    new_email_normalized VARCHAR(254) NOT NULL,
    otp_hash            TEXT NOT NULL,
    expires_at          TIMESTAMPTZ NOT NULL,
    attempts            SMALLINT NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified_at         TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    CONSTRAINT ck_email_change_attempts CHECK (attempts BETWEEN 0 AND 5),
    CONSTRAINT ck_email_change_normalized CHECK (new_email_normalized = lower(new_email))
);

CREATE UNIQUE INDEX uq_email_change_pending_address
    ON users.email_change_requests(new_email_normalized)
    WHERE completed_at IS NULL;
CREATE INDEX ix_email_change_account ON users.email_change_requests(account_id, created_at DESC);

CREATE TABLE users.outbox_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      VARCHAR(64) NOT NULL,
    aggregate_type  VARCHAR(32) NOT NULL,
    aggregate_id    UUID NOT NULL,
    payload         JSONB NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ
);
CREATE INDEX ix_users_outbox_unprocessed ON users.outbox_events(occurred_at) WHERE processed_at IS NULL;

-- ============================================================================
-- 2. CONTENT SERVICE (Database: forum_content / Schema: content)
-- ============================================================================

CREATE TYPE content.attachment_status AS ENUM ('pending', 'linked', 'orphaned', 'deleted');

CREATE TABLE content.categories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(80) NOT NULL,
    slug            VARCHAR(100) NOT NULL UNIQUE,
    description     TEXT,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    is_hidden       BOOLEAN NOT NULL DEFAULT FALSE,
    created_by      UUID NOT NULL, -- Logical reference to users.accounts.id
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_category_name_nonempty CHECK (length(trim(name)) > 0),
    CONSTRAINT ck_category_slug_format CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$')
);

CREATE INDEX ix_categories_visible_order ON content.categories(sort_order, name) WHERE is_hidden = FALSE;

CREATE TABLE content.threads (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id             UUID NOT NULL REFERENCES content.categories(id) ON DELETE RESTRICT,
    author_id               UUID NOT NULL, -- Logical reference to users.accounts.id
    title                   VARCHAR(150) NOT NULL,
    body                    TEXT NOT NULL,
    status                  VARCHAR(16) NOT NULL DEFAULT 'published',
    is_pinned               BOOLEAN NOT NULL DEFAULT FALSE,
    is_locked               BOOLEAN NOT NULL DEFAULT FALSE,
    best_answer_comment_id  UUID, -- Foreign key added below after content.comments table
    view_count              BIGINT NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    edited_at               TIMESTAMPTZ,
    CONSTRAINT ck_thread_title_length CHECK (char_length(title) BETWEEN 10 AND 150),
    CONSTRAINT ck_thread_body_length CHECK (char_length(trim(body)) >= 20),
    CONSTRAINT ck_thread_status CHECK (status IN ('draft', 'published', 'hidden', 'deleted')),
    CONSTRAINT ck_thread_view_count CHECK (view_count >= 0)
);

CREATE INDEX ix_threads_category_created ON content.threads(category_id, created_at DESC) WHERE status != 'deleted';
CREATE INDEX ix_threads_author_created ON content.threads(author_id, created_at DESC);
CREATE INDEX ix_threads_published_created ON content.threads(created_at DESC) WHERE status = 'published';
CREATE INDEX ix_threads_pinned ON content.threads(category_id, is_pinned DESC, created_at DESC) WHERE status != 'deleted';

CREATE TABLE content.thread_tags (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id       UUID NOT NULL REFERENCES content.threads(id) ON DELETE CASCADE,
    tag             VARCHAR(40) NOT NULL,
    tag_normalized  VARCHAR(40) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_thread_tag UNIQUE (thread_id, tag_normalized),
    CONSTRAINT ck_thread_tag_normalized CHECK (tag_normalized = lower(tag))
);

CREATE INDEX ix_thread_tags_normalized ON content.thread_tags(tag_normalized);

CREATE TABLE content.comments (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id           UUID NOT NULL REFERENCES content.threads(id) ON DELETE CASCADE,
    author_id           UUID NOT NULL, -- Logical reference to users.accounts.id
    parent_comment_id   UUID REFERENCES content.comments(id) ON DELETE RESTRICT,
    body                TEXT NOT NULL,
    is_deleted          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    edited_at           TIMESTAMPTZ,
    CONSTRAINT ck_comment_body_nonempty CHECK (length(trim(body)) > 0),
    CONSTRAINT ck_comment_parent_not_self CHECK (parent_comment_id IS NULL OR parent_comment_id <> id)
);

CREATE INDEX ix_comments_thread_created ON content.comments(thread_id, created_at) WHERE is_deleted = FALSE;
CREATE INDEX ix_comments_author_created ON content.comments(author_id, created_at DESC);
CREATE INDEX ix_comments_parent ON content.comments(parent_comment_id);

-- Enforce the rule: comments have at most two levels and parent must belong to same thread.
CREATE OR REPLACE FUNCTION content.validate_comment_parent()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_thread UUID;
    parent_parent UUID;
BEGIN
    IF NEW.parent_comment_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT thread_id, parent_comment_id
      INTO parent_thread, parent_parent
      FROM content.comments
     WHERE id = NEW.parent_comment_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'parent comment % does not exist', NEW.parent_comment_id
            USING ERRCODE = '23503';
    END IF;

    IF parent_thread <> NEW.thread_id THEN
        RAISE EXCEPTION 'parent comment must belong to the same thread'
            USING ERRCODE = '23514';
    END IF;

    IF parent_parent IS NOT NULL THEN
        RAISE EXCEPTION 'comments are limited to two levels'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_validate_comment_parent
BEFORE INSERT OR UPDATE OF parent_comment_id, thread_id
ON content.comments
FOR EACH ROW EXECUTE FUNCTION content.validate_comment_parent();

ALTER TABLE content.threads
    ADD CONSTRAINT fk_thread_best_answer
    FOREIGN KEY (best_answer_comment_id) REFERENCES content.comments(id) ON DELETE SET NULL;

CREATE TABLE content.attachments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id       UUID REFERENCES content.threads(id) ON DELETE SET NULL,
    author_id       UUID NOT NULL, -- Logical reference to users.accounts.id
    object_key      TEXT NOT NULL UNIQUE,
    file_size       INTEGER NOT NULL,
    mime_type       VARCHAR(32) NOT NULL,
    status          content.attachment_status NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    linked_at       TIMESTAMPTZ,
    CONSTRAINT ck_attachment_size CHECK (file_size > 0 AND file_size <= 2097152),
    CONSTRAINT ck_attachment_mime CHECK (mime_type IN ('image/jpeg', 'image/png', 'image/webp'))
);

CREATE INDEX ix_attachments_thread ON content.attachments(thread_id) WHERE thread_id IS NOT NULL;
CREATE INDEX ix_attachments_orphan ON content.attachments(status, created_at) WHERE status = 'pending';

CREATE TABLE content.outbox_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      VARCHAR(64) NOT NULL,
    aggregate_type  VARCHAR(32) NOT NULL,
    aggregate_id    UUID NOT NULL,
    payload         JSONB NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ
);
CREATE INDEX ix_content_outbox_unprocessed ON content.outbox_events(occurred_at) WHERE processed_at IS NULL;

-- ============================================================================
-- 3. INTERACTIONS SERVICE (Database: forum_interactions / Schema: interactions)
-- ============================================================================

CREATE TYPE interactions.target_type AS ENUM ('thread', 'comment');

CREATE TABLE interactions.votes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    voter_id        UUID NOT NULL, -- Logical reference to users.accounts.id
    target_type     interactions.target_type NOT NULL,
    thread_id       UUID, -- Logical reference to content.threads.id (No cross-service FK)
    comment_id      UUID, -- Logical reference to content.comments.id (No cross-service FK)
    value           SMALLINT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_vote_value CHECK (value IN (-1, 1)),
    CONSTRAINT ck_vote_exactly_one_target CHECK (
        (target_type = 'thread' AND thread_id IS NOT NULL AND comment_id IS NULL)
        OR
        (target_type = 'comment' AND comment_id IS NOT NULL AND thread_id IS NULL)
    ),
    CONSTRAINT uq_vote_thread UNIQUE (voter_id, thread_id),
    CONSTRAINT uq_vote_comment UNIQUE (voter_id, comment_id)
);

CREATE INDEX ix_votes_thread_value ON interactions.votes(thread_id, value) WHERE thread_id IS NOT NULL;
CREATE INDEX ix_votes_comment_value ON interactions.votes(comment_id, value) WHERE comment_id IS NOT NULL;
CREATE INDEX ix_votes_voter_created ON interactions.votes(voter_id, created_at DESC);

CREATE TABLE interactions.bookmarks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL, -- Logical reference to users.accounts.id
    thread_id       UUID NOT NULL, -- Logical reference to content.threads.id (No cross-service FK)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_bookmark_user_thread UNIQUE (user_id, thread_id)
);

CREATE INDEX ix_bookmarks_user_created ON interactions.bookmarks(user_id, created_at DESC);

CREATE TABLE interactions.thread_views (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id       UUID NOT NULL, -- Logical reference to content.threads.id (No cross-service FK)
    session_key_hash TEXT NOT NULL,
    viewed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_thread_view_window UNIQUE (thread_id, session_key_hash)
);

CREATE INDEX ix_thread_views_viewed_at ON interactions.thread_views(viewed_at);

CREATE TABLE interactions.reputation_ledger (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id             UUID NOT NULL, -- Logical reference to author whose reputation changes
    source_type         interactions.target_type NOT NULL,
    source_id           UUID NOT NULL, -- Logical reference to thread_id or comment_id
    caused_by_user_id   UUID NOT NULL, -- Logical reference to actor
    delta               INTEGER NOT NULL,
    reason              VARCHAR(40) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_reputation_delta_nonzero CHECK (delta <> 0),
    CONSTRAINT uq_reputation_event UNIQUE (source_type, source_id, caused_by_user_id, reason)
);

CREATE INDEX ix_reputation_user_created ON interactions.reputation_ledger(user_id, created_at DESC);

CREATE TABLE interactions.outbox_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      VARCHAR(64) NOT NULL,
    aggregate_type  VARCHAR(32) NOT NULL,
    aggregate_id    UUID NOT NULL,
    payload         JSONB NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ
);
CREATE INDEX ix_interactions_outbox_unprocessed ON interactions.outbox_events(occurred_at) WHERE processed_at IS NULL;

-- ============================================================================
-- 4. MODERATION SERVICE (Database: forum_moderation / Schema: moderation)
-- ============================================================================

CREATE TYPE moderation.report_reason AS ENUM ('spam', 'abuse', 'harassment', 'wrong_category', 'misinformation', 'other');
CREATE TYPE moderation.report_status AS ENUM ('pending', 'reviewing', 'actioned', 'dismissed');
CREATE TYPE moderation.target_type AS ENUM ('thread', 'comment', 'account');
CREATE TYPE moderation.action_type AS ENUM ('pin', 'lock', 'move_category', 'soft_delete', 'ban', 'unban', 'role_change', 'dismiss_report');

CREATE TABLE moderation.reports (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reporter_id         UUID NOT NULL, -- Logical reference to users.accounts.id
    target_type         moderation.target_type NOT NULL,
    thread_id           UUID, -- Logical reference to content.threads.id (No cross-service FK)
    comment_id          UUID, -- Logical reference to content.comments.id (No cross-service FK)
    reported_account_id UUID, -- Logical reference to users.accounts.id (No cross-service FK)
    reason              moderation.report_reason NOT NULL,
    details             TEXT,
    status              moderation.report_status NOT NULL DEFAULT 'pending',
    assigned_to         UUID, -- Logical reference to moderator users.accounts.id
    resolution_note     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ,
    CONSTRAINT ck_report_exactly_one_target CHECK (
        (target_type = 'thread' AND thread_id IS NOT NULL AND comment_id IS NULL AND reported_account_id IS NULL)
        OR (target_type = 'comment' AND comment_id IS NOT NULL AND thread_id IS NULL AND reported_account_id IS NULL)
        OR (target_type = 'account' AND reported_account_id IS NOT NULL AND thread_id IS NULL AND comment_id IS NULL)
    ),
    CONSTRAINT ck_report_not_self_account CHECK (reported_account_id IS NULL OR reported_account_id <> reporter_id)
);

-- Unique index prevents duplicate pending/reviewing reports from same reporter for same target
CREATE UNIQUE INDEX uq_reporter_thread_active ON moderation.reports(reporter_id, thread_id)
    WHERE thread_id IS NOT NULL AND status IN ('pending', 'reviewing');
CREATE UNIQUE INDEX uq_reporter_comment_active ON moderation.reports(reporter_id, comment_id)
    WHERE comment_id IS NOT NULL AND status IN ('pending', 'reviewing');
CREATE UNIQUE INDEX uq_reporter_account_active ON moderation.reports(reporter_id, reported_account_id)
    WHERE reported_account_id IS NOT NULL AND status IN ('pending', 'reviewing');

CREATE INDEX ix_reports_queue ON moderation.reports(status, created_at) WHERE status IN ('pending', 'reviewing');

CREATE TABLE moderation.actions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id           UUID REFERENCES moderation.reports(id) ON DELETE SET NULL, -- Intra-service FK
    moderator_id        UUID NOT NULL, -- Logical reference to users.accounts.id
    action              moderation.action_type NOT NULL,
    target_type         moderation.target_type NOT NULL,
    target_id           UUID NOT NULL, -- Logical reference to target entity
    reason              TEXT NOT NULL,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_moderation_actions_target ON moderation.actions(target_type, target_id, created_at DESC);
CREATE INDEX ix_moderation_actions_moderator ON moderation.actions(moderator_id, created_at DESC);
CREATE INDEX ix_moderation_actions_report ON moderation.actions(report_id) WHERE report_id IS NOT NULL;

CREATE TABLE moderation.outbox_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      VARCHAR(64) NOT NULL,
    aggregate_type  VARCHAR(32) NOT NULL,
    aggregate_id    UUID NOT NULL,
    payload         JSONB NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ
);
CREATE INDEX ix_moderation_outbox_unprocessed ON moderation.outbox_events(occurred_at) WHERE processed_at IS NULL;

-- ============================================================================
-- 5. NOTIFICATIONS SERVICE (Database: forum_notifications / Schema: notifications)
-- ============================================================================

CREATE TYPE notifications.notification_type AS ENUM (
    'reply', 'comment', 'mention', 'vote', 'best_answer',
    'moderation', 'account', 'system'
);

CREATE TABLE notifications.preferences (
    user_id             UUID PRIMARY KEY, -- Logical reference to users.accounts.id
    in_app_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    email_enabled       BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE notifications.items (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recipient_id        UUID NOT NULL, -- Logical reference to users.accounts.id
    actor_id            UUID, -- Logical reference to users.accounts.id, nullable for system
    type                notifications.notification_type NOT NULL,
    title               VARCHAR(160) NOT NULL,
    body                TEXT NOT NULL,
    target_type         VARCHAR(24),
    target_id           UUID,
    is_read             BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at             TIMESTAMPTZ,
    CONSTRAINT ck_notification_read_state CHECK (
        (is_read = FALSE AND read_at IS NULL)
        OR (is_read = TRUE AND read_at IS NOT NULL)
    )
);

CREATE INDEX ix_notifications_inbox ON notifications.items(recipient_id, created_at DESC);
CREATE INDEX ix_notifications_unread ON notifications.items(recipient_id, created_at DESC) WHERE is_read = FALSE;

-- ============================================================================
-- 6. SEARCH SERVICE (Database: forum_search / Schema: search)
-- ============================================================================

CREATE TABLE search.documents (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type         VARCHAR(24) NOT NULL,
    source_id           UUID NOT NULL, -- Logical reference to content entity
    title               TEXT,
    body                TEXT,
    author_id           UUID, -- Logical reference
    category_id         UUID, -- Logical reference
    search_vector       TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(body, '')), 'B')
    ) STORED,
    source_updated_at   TIMESTAMPTZ NOT NULL,
    indexed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_search_document_source UNIQUE (source_type, source_id),
    CONSTRAINT ck_search_source_type CHECK (source_type IN ('thread', 'comment', 'account'))
);

CREATE INDEX ix_search_documents_vector ON search.documents USING GIN(search_vector);
CREATE INDEX ix_search_documents_category ON search.documents(category_id) WHERE category_id IS NOT NULL;
CREATE INDEX ix_search_documents_author ON search.documents(author_id) WHERE author_id IS NOT NULL;

COMMIT;
