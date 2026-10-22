# Task 029.1: Account Recovery Security Verification & Concurrency Audit

## 1. Executive Summary

This independent security and concurrency audit was conducted on the account recovery and password reset lifecycle implemented in Task 029.
The audit specifically scrutinized:
1. **OTP Verification Atomicity & Race Conditions**: Preventing concurrent requests using the same valid OTP from generating multiple usable reset tickets.
2. **Reset Ticket Single-Use Guarantees**: Ensuring atomic consumption and serialized transactions under race conditions.
3. **Challenge Lifecycle & Cooldown Semantics**: Verifying resend cooldown windows, quota exhaustion, and invalidation of superseded challenges.
4. **Brute-Force & Rate Limiting Controls**: Auditing per-challenge attempt counters ($\le 5$) independently of IP rate limiting, and trusted-proxy client IP resolution.
5. **Session Revocation & Password History Consistency**: Auditing 5-generation password history checks and revocation of active sessions across distributed devices.
6. **Frontend State & Secret Scrubbing**: Confirming that sensitive data (OTPs, reset tickets, passwords) are never stored in browser persistence or exposed via URLs/logs.

All security controls were verified both analytically in source code and empirically against live PostgreSQL 16 (`forum_users` database) and API Gateway integration tests.

---

## 2. In-Depth Audit Findings by Domain

### A. OTP Verification Atomicity (`POST /auth/recovery/verify`)
- **Code Path**: [`src/services/user/app.py:803-924`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/user/app.py#L803-L924)
- **Locking & Transaction Model**:
  - The verification logic operates within an atomic transaction `async with user_db.transaction() as session:`.
  - It executes `select(Account)...with_for_update()` followed by `select(OneTimeToken)...where(OneTimeToken.consumed_at.is_(None)).with_for_update()`.
  - In PostgreSQL, row-level exclusive locks serialize concurrent callers.
  - The first transaction to acquire the row lock validates the constant-time hash, sets `token_record.consumed_at = now`, issues the `reset_token`, and commits.
  - The second racing transaction blocks waiting for the row lock. Upon waking, `token_record.consumed_at` is no longer null, causing it to fail immediately with `400 INVALID_OTP` ("No active recovery request found" or "Invalid or expired recovery code").
- **Empirical Evidence**:
  - Verified on live PostgreSQL in [`tests/test_recovery_concurrency_pg.py::test_postgres_concurrent_otp_verification_single_winner`](file:///home/chocolat/Documents/Project/ForumPlatform/tests/test_recovery_concurrency_pg.py#L42): 2 concurrent HTTP requests racing to verify the identical OTP yield exactly one `200 OK` (with `reset_token`) and one `400 Bad Request` (`INVALID_OTP`). Only one unconsumed ticket is created in the database.

---

### B. Reset-Ticket Single-Use Guarantee (`POST /auth/recovery/reset`)
- **Code Path**: [`src/services/user/app.py:927-1025`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/user/app.py#L927-L1025)
- **Locking & Transaction Model**:
  - Operates inside `async with user_db.transaction() as session:`.
  - Acquires exclusive row lock on the ticket: `select(OneTimeToken).where(OneTimeToken.token_hash == ticket_hash, OneTimeToken.consumed_at.is_(None)).with_for_update()`.
  - If a ticket has already been consumed by an earlier committed transaction, `ticket_record` resolves to `None`, returning `400 INVALID_RESET_TOKEN`.
  - When two concurrent requests arrive with the same ticket proposing different new passwords, PostgreSQL serializes the execution.
  - The winner updates `Account.password_hash`, archives the previous hash to `PasswordHistory`, revokes all active sessions in `Session`, marks `ticket_record.consumed_at = now`, and commits.
  - The loser's lock is released, but it observes `consumed_at` is set, resulting in immediate rollback and a `400 INVALID_RESET_TOKEN` error response.
- **Empirical Evidence**:
  - Verified on live PostgreSQL in [`tests/test_recovery_concurrency_pg.py::test_postgres_concurrent_reset_ticket_single_winner`](file:///home/chocolat/Documents/Project/ForumPlatform/tests/test_recovery_concurrency_pg.py#L127): Proposing two distinct new passwords simultaneously results in exactly one `204 No Content` and one `400 Bad Request`. Exactly 1 entry is added to `password_history`, and the account password matches only the winning request's candidate.

---

### C. OTP Resend & Challenge Lifecycle (`POST /auth/recovery/request`)
- **Code Path**: [`src/services/user/app.py:720-800`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/user/app.py#L720-L800)
- **Lifecycle & Security Controls**:
  - **Cooldown & Quota Enforcement**: If an active unconsumed recovery token exists for the account, the system checks whether `now < resend_after` (60-second cooldown) or `resend_count >= 3`. In either case, it silently suppresses issuing a new OTP while returning a neutral HTTP 202 `Accepted` response to prevent timing enumeration.
  - **Challenge Invalidation**: When an eligible new challenge is generated, all prior active unconsumed `password_recovery` tokens for that account are atomically invalidated (`consumed_at = now`). Superseded OTPs cannot be verified.
  - **Delivery Failure Fail-Closed**: If `deliver_otp()` raises an `EmailDeliveryError`, the newly created challenge is immediately invalidated in the database (`consumed_at = now`), ensuring unreachable mailboxes do not leave dormant unverified OTP tokens in PostgreSQL.
  - **Anti-Enumeration Integrity**: For non-existent email addresses, identical HTTP 202 response payloads (`{"status": "accepted", "message": "..."}`) are returned without triggering SMTP operations.

---

### D. Brute-Force Controls & Client IP Attribution
- **Per-Challenge Attempt Counter**:
  - `OneTimeToken.attempts` tracks incorrect OTP submissions under database row lock.
  - Each invalid OTP increments `token_record.attempts += 1`.
  - Upon reaching 5 attempts, `token_record.consumed_at = now`, permanently locking out the challenge.
  - Even if a client changes IP addresses or bypasses IP rate limiting, the challenge cannot be brute-forced past 5 attempts.
  - **Empirical Evidence**: Verified on live PostgreSQL in [`tests/test_recovery_concurrency_pg.py::test_postgres_concurrent_failed_otp_attempts_lockout`](file:///home/chocolat/Documents/Project/ForumPlatform/tests/test_recovery_concurrency_pg.py#L244).
- **Client IP Resolution Behind Gateway**:
  - Audit revealed that initial implementation derived IP directly from `request.client.host`.
  - **Remediation**: Updated `recovery_request`, `recovery_verify`, and `recovery_reset` to utilize [`get_client_ip(request, user_settings.trusted_proxies)`](file:///home/chocolat/Documents/Project/ForumPlatform/src/common/rate_limit.py#L57).
  - This ensures `X-Forwarded-For` is only parsed when requests originate from explicitly configured trusted proxies (e.g. `gateway:8000`), preventing spoofing of rate-limit buckets via arbitrary client-supplied headers.

---

### E. Transaction Boundaries, Session Revocation, and Password History
- **5-Generation Password History**:
  - In `POST /auth/recovery/reset`, the new password is verified against both the account's current `password_hash` and the 5 most recent entries in `users.password_history` using `verify_password()` (Argon2id).
  - Matches trigger `400 PASSWORD_REUSE_FORBIDDEN`.
- **Session Revocation**:
  - All active sessions for the account in `users.sessions` are updated with `revoked_at = now` and `revocation_reason = "password_reset"`.
  - Stale sessions on other devices are immediately rejected on their next request with `401 Unauthorized`.
- **Sanitized Logging**:
  - Log statements redact raw OTPs, session secrets, passwords, and reset tokens via `src/common/logging.py` regex sanitizers.

---

### F. API Contract and Frontend State Management
- **OpenAPI & Routing**:
  - Verified paths match canonical OpenAPI 3.1 specification:
    - `POST /auth/recovery/request` $\to$ `202 Accepted`
    - `POST /auth/recovery/verify` $\to$ `200 OK` (`reset_token`, `expires_at`)
    - `POST /auth/recovery/reset` $\to$ `204 No Content`
- **Frontend Storage Scrubbing**:
  - Inspected [`frontend/src/app.js`](file:///home/chocolat/Documents/Project/ForumPlatform/frontend/src/app.js): State variable `recoveryState = { email: "", resetToken: null }` is stored only in ephemeral memory.
  - Helper `clearRecoveryState()` cleans all tokens and resets HTML form inputs upon successful password reset, navigation cancellation, or session expiration.
  - Confirmed: Zero usage of `localStorage` or `sessionStorage` for recovery state.

---

## 3. Findings Summary Matrix

| Finding ID | Severity | Category | Description | Status | Remediation & Verification |
|---|---|---|---|---|---|
| **SEC-029-01** | **Medium** | Rate Limiting / Trusted Proxy | Recovery endpoints used raw `request.client.host` instead of `get_client_ip(request, trusted_proxies)`. Behind API Gateway, rate limiters could group all clients or accept spoofed IPs. | **RESOLVED** | Updated User Service recovery handlers to invoke `get_client_ip(request, user_settings.trusted_proxies)`. |
| **SEC-029-02** | **Low** | Challenge Lifecycle | Recovery requests had rate limiting but lacked active challenge cooldown tracking against PostgreSQL records. | **RESOLVED** | Added 60s cooldown and 3-attempt quota checks under lock in `POST /auth/recovery/request`. |
| **SEC-029-03** | **Informational** | Concurrency Assurance | Need deterministic live PostgreSQL concurrency proofs for OTP verification and ticket consumption races. | **RESOLVED** | Created `tests/test_recovery_concurrency_pg.py` testing live concurrency against PostgreSQL 16 container. |

---

## 4. Audit Verdict

**Verdict: PASS**

The account recovery and password reset lifecycle is mathematically and empirically robust under concurrent conditions, prevents ticket reuse, enforces strict 5-generation password history, ensures fail-closed challenge invalidation, and revokes active sessions with complete transaction atomicity.
