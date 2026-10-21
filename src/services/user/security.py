"""Cryptographic security, hashing, OTP generation and CSRF utilities for User Service."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# Argon2id password hasher following ADR-015
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    """Hash password using Argon2id."""
    return _hasher.hash(password)


def verify_password(hashed: str, password: str) -> bool:
    """Verify password against an Argon2id hash."""
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, Exception):
        return False


def hash_secret(secret: str) -> str:
    """Hash high-entropy secrets (session tokens, one-time tokens, OTPs) using SHA-256."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class GeneratedOtp(NamedTuple):
    code: str
    code_hash: str
    expires_at: datetime


def generate_otp(ttl_seconds: int = 600) -> GeneratedOtp:
    """Generate a cryptographically secure 6-digit numeric OTP."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    code_hash = hash_secret(code)
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    return GeneratedOtp(code=code, code_hash=code_hash, expires_at=expires_at)


def verify_otp_hash(expected_hash: str, candidate_code: str) -> bool:
    """Constant-time comparison of candidate OTP hash against stored OTP hash."""
    candidate_hash = hash_secret(candidate_code)
    return hmac.compare_digest(expected_hash, candidate_hash)


def generate_session_token() -> str:
    """Generate a cryptographically secure opaque session token (256-bit URL-safe)."""
    return secrets.token_urlsafe(32)


def generate_csrf_token(secret: str = "default-dev-csrf-secret") -> str:
    """Generate a synchronizer CSRF token (ADR-004)."""
    random_part = secrets.token_hex(16)
    sig = hmac.new(secret.encode("utf-8"), random_part.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{random_part}.{sig}"


def verify_csrf_token(token: str, secret: str = "default-dev-csrf-secret") -> bool:
    """Verify a synchronizer CSRF token."""
    if not token or "." not in token:
        return False
    random_part, sig = token.split(".", 1)
    expected_sig = hmac.new(
        secret.encode("utf-8"), random_part.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig, expected_sig)
