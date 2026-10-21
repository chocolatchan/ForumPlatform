"""Initial baseline migration for User Service (forum_users).

Revision ID: users_0001
Revises: None
Create Date: 2026-10-20 18:43:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "users_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS users;")

    op.create_table(
        "accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("username", sa.String(32), nullable=False),
        sa.Column("username_normalized", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(50), nullable=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("email_normalized", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="member"),
        sa.Column("is_banned", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("ban_reason", sa.Text(), nullable=True),
        sa.Column("banned_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bio", sa.String(200), nullable=True),
        sa.Column("avatar_object_key", sa.Text(), nullable=True),
        sa.Column(
            "email_verified_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("username_normalized", name="uq_accounts_username_normalized"),
        sa.UniqueConstraint("email_normalized", name="uq_accounts_email_normalized"),
        sa.CheckConstraint(
            "char_length(username) >= 3 AND char_length(username) <= 32",
            name="ck_accounts_username_length",
        ),
        sa.CheckConstraint(
            "username_normalized = lower(username)", name="ck_accounts_username_normalized_lower"
        ),
        sa.CheckConstraint(
            "email_normalized = lower(email)", name="ck_accounts_email_normalized_lower"
        ),
        schema="users",
    )
    op.create_index("ix_accounts_role", "accounts", ["role"], schema="users")
    op.create_index("ix_accounts_banned", "accounts", ["is_banned"], schema="users")

    op.create_table(
        "pending_registrations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("username", sa.String(32), nullable=False),
        sa.Column("username_normalized", sa.String(32), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("email_normalized", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("otp_hash", sa.Text(), nullable=False),
        sa.Column("otp_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("otp_attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("resend_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("resend_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("otp_attempts BETWEEN 0 AND 5", name="ck_pending_otp_attempts"),
        sa.CheckConstraint("resend_count BETWEEN 0 AND 3", name="ck_pending_resend_count"),
        schema="users",
    )
    op.create_index(
        "ix_pending_registration_username",
        "pending_registrations",
        ["username_normalized"],
        schema="users",
    )
    op.create_index(
        "ix_pending_registration_email",
        "pending_registrations",
        ["email_normalized"],
        schema="users",
    )
    op.create_index(
        "ix_pending_registration_expiry",
        "pending_registrations",
        ["otp_expires_at"],
        schema="users",
    )

    op.create_table(
        "one_time_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("resend_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("resend_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("attempts BETWEEN 0 AND 5", name="ck_one_time_attempts"),
        sa.CheckConstraint("resend_count BETWEEN 0 AND 3", name="ck_one_time_resends"),
        schema="users",
    )

    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_token_hash", sa.Text(), unique=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.String(32), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.CheckConstraint(
            "absolute_expires_at >= idle_expires_at", name="ck_session_expiry_order"
        ),
        schema="users",
    )
    op.create_index(
        "ix_sessions_account_active", "sessions", ["account_id", "last_seen_at"], schema="users"
    )

    op.create_table(
        "password_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema="users",
    )

    op.create_table(
        "email_change_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("new_email", sa.String(254), nullable=False),
        sa.Column("new_email_normalized", sa.String(254), nullable=False),
        sa.Column("otp_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        schema="users",
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
        schema="users",
    )
    op.create_index("ix_users_outbox_unprocessed", "outbox_events", ["occurred_at"], schema="users")


def downgrade() -> None:
    op.drop_table("outbox_events", schema="users")
    op.drop_table("email_change_requests", schema="users")
    op.drop_table("password_history", schema="users")
    op.drop_table("sessions", schema="users")
    op.drop_table("one_time_tokens", schema="users")
    op.drop_table("pending_registrations", schema="users")
    op.drop_table("accounts", schema="users")
