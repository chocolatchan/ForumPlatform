# Task 006 Change Log: User Service Authentication & Account Lifecycle

**Target:** Forum Platform User Service & Gateway Integration  
**Date:** 2026-10-20  
**Status:** Completed & Verified  

---

## 1. Overview of Changes

Implemented full database-backed authentication, account lifecycle, OTP verification, server-side session management, CSRF validation, and role-based access control in the User Service (`src/services/user/`) and API Gateway (`src/gateway/forwarder.py`).

All operations persist to PostgreSQL/SQLite, enforce architectural decision records (ADR-003, ADR-004, ADR-015, DEC-001, DEC-002), and are accessible through the API Gateway at `http://localhost:8000/api/v1` and centralized Swagger UI at `http://localhost:8000/docs`.

---

## 2. Detailed File Modifications

### `src/services/user/security.py` (New Module)
- Implemented Argon2id password hashing and verification (`hash_password`, `verify_password`) conforming to ADR-015 (`time_cost=3`, `memory_cost=65536`, `parallelism=4`, `hash_len=32`, `salt_len=16`).
- Implemented SHA-256 secret hashing (`hash_secret`) for high-entropy tokens and OTP hashes to prevent plaintext credential exposure in the database.
- Implemented cryptographically secure 6-digit numeric OTP generation (`generate_otp`) with configurable TTL (default 10 minutes) and constant-time comparison (`verify_otp_hash`).
- Implemented opaque 256-bit URL-safe session token generator (`generate_session_token`) conforming to ADR-003.
- Implemented HMAC-SHA256 synchronizer CSRF token generator and constant-time validator (`generate_csrf_token`, `verify_csrf_token`) conforming to ADR-004.

### `src/services/user/otp_delivery.py` (New Module)
- Created testing/development OTP delivery abstraction (`OtpDeliverySink`, `otp_delivery_sink`) to inspect generated verification codes in automated tests and local dev environments without exposing codes in public responses or logging credentials.

### `src/services/user/schemas.py` (New Module)
- Defined strict Pydantic v2 schemas conforming to canonical OpenAPI 3.1 specifications:
  - Request schemas: `RegisterRequest`, `VerifyOtpRequest`, `ResendOtpRequest`, `LoginRequest`, `UpdateProfileRequest`, `ChangeUserRoleRequest`.
  - Response schemas: `UserPublic`, `UserProfileOwn`, `UserAdminView`, `LoginResponse`, `SessionMetadata`, `RegistrationPendingResponse`, `NeutralAcceptedResponse`.

### `src/services/user/auth.py` (New Module)
- Implemented FastAPI dependency `get_current_session`:
  - Extracts `forum_session` cookie.
  - Queries `users.sessions` joined with `users.accounts` by token SHA-256 hash.
  - Validates session revocation (`revoked_at is None`).
  - Enforces absolute expiration (`absolute_expires_at` up to 24 hours per ADR-003).
  - Enforces idle timeout (`idle_expires_at` up to 4 hours per ADR-003).
  - Verifies user status (`is_banned == False`).
  - Performs sliding window idle refresh and atomic `last_seen_at` updates.
  - Returns `AuthenticatedContext`.
- Implemented `require_role(*roles)` dependency factory for role authorization (`member`, `moderator`, `admin`).
- Implemented `verify_csrf_header` dependency enforcing `X-CSRF-Token` header check on state-changing requests when a session cookie is present.

### `src/services/user/app.py` (Updated)
- Replaced Tier I placeholders with real database-backed authentication and account lifecycle flows:
  - `POST /auth/register`: Normalized input validation, pending registration storage, 6-digit OTP generation, neutral `202 Accepted` response (DEC-001).
  - `POST /auth/register/verify`: OTP verification, atomic `with_for_update()` lock, attempt counting (max 5), expiration enforcement, single-use invalidation, active `users.accounts` creation (`201 Created`).
  - `POST /auth/register/resend-otp`: Cooldown (60s) and quota (max 3 resends) enforcement, neutral `202 Accepted` response (DEC-001).
  - `POST /auth/login`: Argon2id password verification, opaque session issuance, Set-Cookie `forum_session` (`HttpOnly; SameSite=Lax; Path=/`), `200 OK` with user and session expiration metadata.
  - `POST /auth/logout`: Current session revocation (`revoked_at`, reason `user_logout`), deletion of `forum_session` cookie, `204 No Content`.
  - `GET /auth/session`: Active session metadata and user profile (`200 OK`).
  - `GET /profile`: Authenticated user's private profile (`200 OK`).
  - `PATCH /profile`: Protected profile field update with CSRF validation (`200 OK`).
  - `GET /users/{user_id}`: Public user profile lookup (`200 OK` or `404 Not Found`).
  - `GET /admin/users`: Admin-only user listing with cursor pagination support (`200 OK`, `403 Forbidden` for non-admin).
  - `PATCH /admin/users/{user_id}/role`: Admin-only role update with CSRF validation (`200 OK`, `403 Forbidden` for non-admin).
  - Maintained controlled `501 Not Implemented` endpoints for future features (password recovery, email change).

### `src/gateway/forwarder.py` (Updated)
- Enhanced reverse proxy streaming forwarder to preserve multi-valued upstream response headers (specifically `Set-Cookie` for `forum_session` issuance and deletion).

### `src/common/errors.py` (Updated)
- Registered canonical `HTTPException` handler in `register_canonical_error_handlers(app)` so all authentication and authorization exceptions (401, 403, 404, 409) return the standard RFC error envelope (`{"error": {"code": "...", "message": "...", "details": [], "request_id": "..."}}`).

### `src/common/models.py` (Updated)
- Added `ensure_utc(dt)` helper to normalize timestamps across PostgreSQL and SQLite database drivers without naive/aware datetime comparison errors.

### `tests/test_user_auth_lifecycle.py` (New Automated Test Suite)
- Added 12 comprehensive automated test cases covering registration, neutral duplicate response, validation errors, OTP attempt limits, expiration, single-use semantics, resend quota, login success, invalid credentials, banned user lockout, session timeouts, logout revocation, CSRF enforcement, role-based access control, and end-to-end Gateway proxying.

### `tests/test_crud_integration.py` & `tests/test_tier1_api.py` (Updated)
- Adapted existing regression tests to verify full OTP verification flow and admin session authentication for role modification endpoints.
