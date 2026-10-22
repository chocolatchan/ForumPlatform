# Task 026: Change Log

**Task:** Password Change Lifecycle Implementation  
**Date:** 2026-10-22  
**Target Repository:** `chocolatchan/ForumPlatform`  

---

### Modified Files

1. **`src/services/user/schemas.py`**
   - Updated `ChangePasswordRequest` to accept `current_password` matching the canonical OpenAPI specification and ADR contract, with alias support for `old_password`.
   - Imported `AliasChoices` from `pydantic`.

2. **`src/services/user/models.py`**
   - Configured `PasswordHistory.id` primary key mapping to support SQLite integer primary key auto-increment variant (`BigInteger().with_variant(Integer, "sqlite")`), identical to existing conventions in `interaction/models.py`.

3. **`src/services/user/app.py`**
   - Replaced `501 Not Implemented` placeholder on `POST /profile/change-password` with complete implementation:
     - Enforces `AuthenticatedContext` and `verify_csrf_header`.
     - Validates current password against Argon2id hash.
     - Enforces new password minimum length and prohibits identical re-use.
     - Hashes new password with Argon2id.
     - Records old password hash to `users.password_history`.
     - Atomically updates `account.password_hash` and `account.password_changed_at`.
     - Revokes all other active sessions for the account (`revocation_reason="password_changed"`).
     - Returns HTTP `204 No Content`.
   - Added development hook `POST /test/reset-rate-limiters` to allow programmatic test resets without container restarts.

4. **`frontend/src/api.js`**
   - Added `changePassword(currentPassword, newPassword)` client method targeting `POST /api/v1/profile/change-password`.

5. **`frontend/index.html`**
   - Added `// SECURITY & CREDENTIAL MUTATION` panel inside `#view-profile`.
   - Added form `#form-change-password` with `#input-current-password`, `#input-new-password`, `#input-confirm-password`, error container `#change-password-error`, and submit button `#btn-submit-change-password`.

6. **`frontend/src/app.js`**
   - Added client-side submit handler for `#form-change-password` with:
     - Required field validation.
     - Minimum 8-character validation.
     - Confirmation match validation.
     - Identity / duplicate password prevention validation.
     - Button busy state disabling during API invocation.
     - Alert banner dispatch and input clearing on success.

7. **`tests/test_user_auth_lifecycle.py`**
   - Added `test_password_change_lifecycle` covering 401 unauthenticated, 403 CSRF missing, 400 wrong password, 422 short password, 400 password re-use, 204 success, session invalidation across multi-device clients, and post-change login authentication.
   - Updated test fixture to clean rate limiter state between tests.

8. **`tests/test_browser_e2e.py`**
   - Added `test_browser_profile_password_change_journey` exercising real browser navigation, client validation, form submission, notification verification, logout, old password rejection, and new password login.

9. **`docs/audit/`**
   - `docs/audit/task_026_password_change_audit.md`
   - `docs/audit/task_026_validation_report.md`
   - `docs/audit/task_026_change_log.md`
