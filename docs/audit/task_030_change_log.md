# Task 030 Change Log

## Date: 2026-10-22

### Summary of Changes
Implemented Task 030: Avatar Lifecycle & Email Change Lifecycle in ForumPlatform.

---

### Backend Service Changes

#### 1. Database Migrations
- **`migrations/users/versions/20261022_0003_email_change_resend_columns.py`**:
  - Added `resend_count` (`SmallInteger`, default 0) and `resend_available_at` (`DateTime(timezone=True)`) to `users.email_change_requests`.
  - Added indexes on `account_id` and `new_email_normalized`.

#### 2. User Service Models & Schemas
- **`src/services/user/models.py`**:
  - Updated `EmailChangeRequest` model with `CheckConstraint` on `attempts` ($0 \dots 5$) and `resend_count` ($0 \dots 3$).
  - Added `resend_count` and `resend_available_at` fields.
- **`src/services/user/schemas.py`**:
  - Updated `UserProfileOwn` and `UserProfilePublic` schemas to include `avatar_url: str | None = None`.
  - Added `SetAvatarRequest` with `image_id: uuid.UUID`.
  - Updated `ChangeEmailRequest` to accept `new_email: str` and optional `password: str | None = None`.
  - Added `ChangeEmailResendRequest`.

#### 3. User Service Application Logic (`src/services/user/app.py`)
- **Avatar Endpoints**:
  - `PUT /profile/avatar`: Claims uploaded attachment via internal Content Service call and updates `Account.avatar_object_key`. Releases prior avatar if replacing.
  - `DELETE /profile/avatar`: Clears `Account.avatar_object_key` and notifies Content Service to release storage.
  - Updated `GET /profile`, `PATCH /profile`, and `GET /users/{user_id}` to compute and return `avatar_url` (`/uploads/images/{avatar_object_key}`).
- **Email Change Endpoints**:
  - `POST /profile/change-email/request`: Two-phase request initiation with re-authentication password check, anti-enumeration neutral 202 response, account row-locking cooldown guard, and OTP delivery.
  - `POST /profile/change-email/resend-otp`: OTP resend respecting 60-second cooldown and maximum 3 resend quota.
  - `POST /profile/change-email/verify`: Atomic OTP verification under `EmailChangeRequest` row lock with re-checked account email uniqueness and account update.
- **Security Hardening (Task 029 Residual Risk)**:
  - Fixed account row locking in `POST /auth/recovery/request` to serialize concurrent initial requests and prevent race condition on cooldown.

#### 4. Content Service Internal Endpoints (`src/services/content/app.py`)
- Added internal authenticated endpoints protected by `X-Internal-Key`:
  - `POST /internal/attachments/{attachment_id}/claim-avatar`: Verifies ownership (`author_id == user_id`) and status, transitions attachment status to `avatar`.
  - `POST /internal/attachments/{attachment_id}/release-avatar`: Transitions attachment status to `deleted` and unlinks physical file from storage.
- Updated `serve_image` (`GET /uploads/images/{image_id}`): Allows unauthenticated public caching (`Cache-Control: public, max-age=86400`) if `attachment.status == "avatar"`.

---

### Frontend UI Changes

#### 1. Markup (`frontend/index.html`)
- Added **Avatar & Visual Identity** section to `#view-profile`:
  - Avatar preview round box (`#avatar-preview-box`).
  - File picker (`#input-avatar-file`), upload button (`#btn-upload-avatar`), and delete button (`#btn-delete-avatar`).
  - Inline status message box (`#avatar-status-msg`).
- Added **Email Address Mutation** section to `#view-profile`:
  - Step 1: Request form (`#form-request-email-change`) with new email and current password confirmation.
  - Step 2: Verification step (`#email-verify-step`) with OTP input, resend button with cooldown, and cancel button.

#### 2. API Client (`frontend/src/api.js`)
- Added `setAvatar(imageId)`, `deleteAvatar()`.
- Added `requestEmailChange(newEmail, password)`, `verifyEmailChange(otp)`, and `resendEmailChangeOtp()`.

#### 3. Client Application (`frontend/src/app.js`)
- Implemented event handlers for avatar file selection, Content Service upload, avatar claim, and avatar removal.
- Implemented email change request flow, OTP submission, resend handling, and cancellation.
- Updated `loadProfileDetails()` to render avatar preview and toggle Remove Avatar button based on `avatar_url`.

---

### OpenAPI Contract Updates
- **`src/docs/forum_public_api.openapi.yaml`**:
  - Documented `PUT /profile/avatar` and `DELETE /profile/avatar`.
  - Documented `POST /profile/change-email/resend-otp`.
  - Added `avatar_url` property to `UserProfilePublic` and `UserProfileOwn`.

---

### Test Suites Added & Updated
- **`tests/test_avatar_email_change_pg.py`** (New): Comprehensive PostgreSQL concurrency, authorization, and lifecycle test suite covering avatar claim/serve/delete and email change anti-enumeration, attempt lockouts, and concurrent verification.
- **`tests/test_browser_e2e.py`**: Added Scenario 16 testing live browser profile avatar and email change forms, and verified zero secret leaks in client storage.
- **`tests/test_tier1_api.py`**: Updated contract assertion from 501 placeholder to 401 Unauthorized for `/profile/change-email/request`.
