"""User Service ORM Models."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.common.models import Base, TimestampMixin
from src.common.outbox import OutboxEventMixin


class Account(Base, TimestampMixin):
    """User account entity."""

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(
            "char_length(username) >= 3 AND char_length(username) <= 32",
            name="ck_accounts_username_length",
        ),
        CheckConstraint(
            "username_normalized = lower(username)", name="ck_accounts_username_normalized_lower"
        ),
        CheckConstraint(
            "email_normalized = lower(email)", name="ck_accounts_email_normalized_lower"
        ),
        {"schema": "users"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    username_normalized: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    display_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    email_normalized: Mapped[str] = mapped_column(
        String(254), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member", index=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    ban_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    banned_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bio: Mapped[str | None] = mapped_column(String(200), nullable=True)
    avatar_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Intra-service relationships
    sessions: Mapped[list["Session"]] = relationship(
        "Session", back_populates="account", cascade="all, delete-orphan"
    )
    one_time_tokens: Mapped[list["OneTimeToken"]] = relationship(
        "OneTimeToken", back_populates="account", cascade="all, delete-orphan"
    )


class PendingRegistration(Base, TimestampMixin):
    """Pending registrations awaiting OTP verification."""

    __tablename__ = "pending_registrations"
    __table_args__ = (
        CheckConstraint("otp_attempts BETWEEN 0 AND 5", name="ck_pending_otp_attempts"),
        CheckConstraint("resend_count BETWEEN 0 AND 3", name="ck_pending_resend_count"),
        {"schema": "users"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    username_normalized: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(254), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    otp_hash: Mapped[str] = mapped_column(Text, nullable=False)
    otp_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    otp_attempts: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    resend_count: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    resend_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)


class OneTimeToken(Base):
    """One-time tokens for password reset and email change."""

    __tablename__ = "one_time_tokens"
    __table_args__ = (
        CheckConstraint("attempts BETWEEN 0 AND 5", name="ck_one_time_attempts"),
        {"schema": "users"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.accounts.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    resend_count: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    resend_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account: Mapped[Account] = relationship("Account", back_populates="one_time_tokens")


class Session(Base):
    """Server-side browser sessions."""

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("absolute_expires_at >= idle_expires_at", name="ck_session_expiry_order"),
        {"schema": "users"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.accounts.id", ondelete="CASCADE"), nullable=False
    )
    session_token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    account: Mapped[Account] = relationship("Account", back_populates="sessions")


class PasswordHistory(Base):
    """Password history for preventing password reuse."""

    __tablename__ = "password_history"
    __table_args__ = {"schema": "users"}

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.accounts.id", ondelete="CASCADE"), nullable=False
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmailChangeRequest(Base):
    """Email change verification requests."""

    __tablename__ = "email_change_requests"
    __table_args__ = {"schema": "users"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.accounts.id", ondelete="CASCADE"), nullable=False
    )
    new_email: Mapped[str] = mapped_column(String(254), nullable=False)
    new_email_normalized: Mapped[str] = mapped_column(String(254), nullable=False)
    otp_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserOutboxEvent(Base, OutboxEventMixin):
    """Transactional outbox for User Service domain events."""

    __tablename__ = "outbox_events"
    __table_args__ = {"schema": "users"}
