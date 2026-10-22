# Task 029: Account Recovery & Password Reset Lifecycle Design

## 1. Overview and Architecture

This document defines the technical design and security architecture for **Task 029: Account Recovery & Password Reset Lifecycle** in ForumPlatform.
The design complies with:
- **ADR-003**: Server-side session authentication with HTTP-only cookies (`forum_session`), absolute/idle timeouts, and transactional session revocation.
- **ADR-004**: Synchronizer double-check CSRF protection via `X-CSRF-Token`.
- **ADR-015**: Cryptographic standards using Argon2id for password hashing, SHA-256 for high-entropy token hashing, and 5-generation password history enforcement.
- **DEC-001**: Neutral HTTP 202 Accepted anti-enumeration responses for sensitive identity operations.
- **DEC-002**: Database schema isolation (`forum_users` schema).

---

## 2. Recovery Lifecycle & State Machine

The recovery journey consists of three phases without requiring pre-authenticated session state:

```
[Unauthenticated User]
         |
         | 1. POST /auth/recovery/request (email)
         v
[User Service: Anti-Enumeration & Rate Limiting]
         |-- If email invalid format -> 422
         |-- If rate limit exceeded -> 429
         |-- Look up Account by normalized email
         |-- If found:
         |     Generate 6-digit OTP (15 min TTL)
         |     Store hash in one_time_tokens (purpose="password_recovery")
         |     Send via otp_delivery_sink / SMTP Mailpit
         |-- Always return HTTP 202 Accepted {"status": "accepted", "message": "..."}
         v
[User receives OTP via Email]
         |
         | 2. POST /auth/recovery/verify (email, otp)
         v
[User Service: OTP Verification & Ticket Issuance]
         |-- Rate limit verify attempts (IP-based)
         |-- Select unconsumed token for Account with purpose="password_recovery"
         |-- Verify expiry and attempts limit (<= 5)
         |-- Constant-time OTP hash comparison
         |-- On success:
         |     Atomically mark recovery OTP consumed
         |     Generate reset_token = secrets.token_urlsafe(32) (10 min TTL)
         |     Store hash in one_time_tokens (purpose="password_reset_ticket")
         |     Return HTTP 200 {"reset_token": "...", "expires_at": "..."}
         v
[User sets New Password]
         |
         | 3. POST /auth/recovery/reset (reset_token, new_password)
         v
[User Service: Transactional Password Reset & Session Revocation]
         |-- Hash incoming reset_token with SHA-256
         |-- SELECT OneTimeToken WHERE token_hash=... AND purpose="password_reset_ticket" WITH FOR UPDATE
         |-- Check expiry and not consumed
         |-- SELECT Account WITH FOR UPDATE
         |-- Query last 5 PasswordHistory rows:
         |     Verify new_password does not match current password or any of last 5 history hashes
         |     If match -> 400 PASSWORD_REUSE_FORBIDDEN
         |-- Hash new_password using Argon2id
         |-- Insert current password hash into password_history
         |-- Update Account password_hash and password_changed_at
         |-- Mark reset_token consumed (consumed_at = now)
         |-- Revoke ALL active sessions for Account (revoked_at=now, reason="password_reset")
         |-- Return HTTP 204 No Content
```

---

## 3. Database Model & Schema Mapping

The existing database table `users.one_time_tokens` already exists in migration `migrations/users/versions/20261020_0001_initial_users.py` and model `src/services/user/models.py:OneTimeToken`:

- `id`: UUID (Primary Key)
- `account_id`: UUID (Foreign Key `users.accounts.id`, ondelete="CASCADE")
- `purpose`: `String(32)` (`"password_recovery"` for OTP, `"password_reset_ticket"` for bearer reset token)
- `token_hash`: `Text` (SHA-256 hash of OTP or reset ticket)
- `expires_at`: `DateTime(timezone=True)` (15 minutes for OTP, 10 minutes for reset ticket)
- `attempts`: `SmallInteger` (Incremented on failed verification; check constraint $\le 5$)
- `resend_count`: `SmallInteger` (Incremented on resend requests; max 3)
- `resend_after`: `DateTime(timezone=True)` (Cool-off cooldown timestamp)
- `consumed_at`: `DateTime(timezone=True)` (Null until used)
- `created_at`: `DateTime(timezone=True)`

No new database tables or schema migrations are necessary.

---

## 4. Security & Privacy Controls

1. **Anti-Enumeration Protection**:
   `POST /auth/recovery/request` returns identical HTTP 202 `NeutralAccepted` schemas regardless of whether the email address exists in the database.
2. **Rate Limiting**:
   - `rate_limiter_recovery_request`: Max 5 requests / min per IP.
   - `rate_limiter_recovery_verify`: Max 10 attempts / min per IP.
   - `rate_limiter_recovery_reset`: Max 5 attempts / min per IP.
3. **Password History (5 Generations)**:
   A user cannot reuse their current password or any of their last 5 historical passwords.
4. **Session Termination**:
   Resetting a password revokes *all* active sessions (`users.sessions`), immediately invalidating hijacked or stale sessions across all devices.
5. **No Secret Logging**:
   Raw OTP codes, reset tickets, and passwords are never logged or stored in browser storage (`localStorage` or `sessionStorage`).

---

## 5. Frontend UI/UX Design

1. **Entry Point**:
   A dedicated `"// FORGOT PASSWORD?"` link on `#view-login` navigates to `#view-recovery-request`.
2. **Step 1: Request View (`#view-recovery-request`)**:
   Input for account email address with submit button `"SEND RECOVERY CODE"`. Shows neutral success notification.
3. **Step 2: Verify View (`#view-recovery-verify`)**:
   Input for 6-digit numeric OTP with submit button `"VERIFY CODE"`.
4. **Step 3: Reset View (`#view-recovery-reset`)**:
   Inputs for new password and password confirmation with toggle visibility buttons and password rule hints. Submit button `"RESET PASSWORD"`.
5. **Completion**:
   On successful reset, clears sensitive form variables, displays a confirmation banner, and redirects to `#view-login`.
