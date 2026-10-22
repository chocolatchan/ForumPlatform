# Task 029: Account Recovery & Password Reset Lifecycle Change Log

## Date: 2026-10-22

### Summary of Changes

Implemented end-to-end account password recovery and reset lifecycle across the backend (User Service), frontend client application, contract tests, and Playwright browser integration suite.

---

### Files Modified & Created

#### 1. `src/services/user/config.py`
- Added configuration rate-limiting options:
  - `rate_limit_recovery_request_max: int = 5`
  - `rate_limit_recovery_verify_max: int = 10`
  - `rate_limit_recovery_reset_max: int = 5`

#### 2. `src/services/user/security.py`
- Added `generate_reset_token()` helper producing a 256-bit URL-safe token using `secrets.token_urlsafe(32)`.

#### 3. `src/services/user/app.py`
- Integrated sliding window rate limiters for recovery operations:
  - `rate_limiter_recovery_request`
  - `rate_limiter_recovery_verify`
  - `rate_limiter_recovery_reset`
  - Included them in test hook `POST /test/reset-rate-limiters`.
- Implemented `POST /auth/recovery/request`:
  - Returns neutral HTTP 202 `Accepted` anti-enumeration response (DEC-001).
  - Generates 6-digit OTP (15-minute TTL) stored in `users.one_time_tokens` (`purpose="password_recovery"`).
  - Delivers message via `otp_delivery_sink` / SMTP Mailpit.
  - Fail-closed cleanup: invalidates token record if email delivery fails.
- Implemented `POST /auth/recovery/verify`:
  - Enforces 5-attempt limit and expiration check.
  - Constant-time verification of OTP hash.
  - Consumes OTP token and issues a 10-minute reset ticket (`purpose="password_reset_ticket"`).
- Implemented `POST /auth/recovery/reset`:
  - Validates and locks single-use reset ticket.
  - Enforces 5-generation password history (checks current password and up to 5 preceding hashes in `users.password_history`).
  - Hashes new password with Argon2id.
  - Transactionally updates password, records history, consumes ticket, and revokes all active sessions for the account in `users.sessions`.
  - Returns HTTP 204 `No Content`.

#### 4. `frontend/index.html`
- Added `"// FORGOT PASSWORD?"` navigation button in `#view-login`.
- Created `#view-recovery-request` for email input.
- Created `#view-recovery-verify` for 6-digit OTP code entry.
- Created `#view-recovery-reset` with password and confirmation inputs, visibility toggles, and error banner.

#### 5. `frontend/src/api.js`
- Added client API methods:
  - `requestRecovery(email)` $\to$ `POST /auth/recovery/request`
  - `verifyRecovery(email, otp)` $\to$ `POST /auth/recovery/verify`
  - `resetPassword(resetToken, newPassword)` $\to$ `POST /auth/recovery/reset`

#### 6. `frontend/src/app.js`
- Implemented state handling and transitions for recovery views:
  - `recoveryState = { email: "", resetToken: null }`
  - Client-side confirmation matching check.
  - Safe memory cleanup via `clearRecoveryState()` (zero persistence in `localStorage`/`sessionStorage`).

#### 7. `tests/test_tier1_api.py`
- Remediated stale assertion: unauthenticated `POST /uploads/images` now asserts `401 Unauthorized` instead of `501 Not Implemented`.
- Replaced 501 test target with genuine unimplemented endpoint `POST /profile/change-email/request`.

#### 8. `tests/test_user_auth_lifecycle.py`
- Added `test_password_recovery_and_reset_lifecycle`:
  - Tests anti-enumeration neutral 202.
  - Tests invalid/bad OTP rejection.
  - Tests OTP replay rejection.
  - Tests reset token issuance and one-time use.
  - Tests password reuse rejection against current password and password history.
  - Tests active session revocation.
  - Tests old password rejection and new password successful authentication.

#### 9. `tests/test_browser_e2e.py`
- Added `test_browser_account_recovery_and_password_reset_journey` (Scenario 15):
  - End-to-end browser user journey from registration $\to$ forgot password $\to$ Mailpit OTP extraction $\to$ verify code $\to$ set new password $\to$ mismatch validation $\to$ successful reset $\to$ old password failure $\to$ new password login.

#### 10. Documentation
- Created `docs/audit/task_029_recovery_design.md`.
- Created `docs/audit/task_029_validation_report.md`.
- Created `docs/audit/task_029_change_log.md`.
