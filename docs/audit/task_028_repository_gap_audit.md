# Task 028: Post-Implementation Gap Audit Report

## 1. Executive Summary

This report establishes an evidence-based audit of the **ForumPlatform** codebase following the integration and stabilization of **Task 027-H1-R2** (Image Upload Lifecycle Residual Risk Remediation).
The audit covers repository integrity, contract completeness across all six microservices (User, Content, Interaction, Moderation, Notification, Search) and the API Gateway, transactional integrity, operational resilience, and security posture.

---

## 2. Repository & Baseline Integrity

- **Repository**: `https://github.com/chocolatchan/ForumPlatform`
- **Branch**: `master`
- **Baseline Commit**: `2313bd746957daf34a39c881a4f09caf4682ea49` (`027-H1-R2`)
- **Remote Synchronization**: `HEAD` matches `origin/master` exactly.
- **Working Tree**: Completely clean (zero untracked or modified files).

---

## 3. Service & Workflow Classification Matrix

Each workflow across the six microservices has been classified into one of five statuses:
- **VERIFIED**: Fully implemented, backed by end-to-end tests, security tests, or live infrastructure verification.
- **PARTIALLY IMPLEMENTED**: Core path exists, but related secondary capabilities or error handling have known limitations.
- **DEFECT CONFIRMED**: An existing implementation or test fails or exhibits incorrect behavior.
- **NOT IMPLEMENTED**: Defined in OpenAPI specification or ADR architecture but currently returning 501 or missing from routing/logic.
- **NOT TESTED**: Implemented in code, but lacks dedicated unit/integration automated tests.

| Service / Workflow | Status | Description & Evidence |
|---|---|---|
| **User: Registration & Activation** | **VERIFIED** | Handled in [`src/services/user/app.py`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/user/app.py#L175). Emits OTP via Mailpit/SMTP, neutral 202 on duplicates, rate-limited. Verified by `tests/test_user_auth_lifecycle.py` and `tests/test_browser_e2e.py`. |
| **User: Session Lifecycle & Auth** | **VERIFIED** | Secure HTTP-only `forum_session` cookie, sliding window idle expiration, absolute expiration, CSRF double-check headers. Verified by browser E2E and gateway tests. |
| **User: Password Change** | **VERIFIED** | [`/profile/change-password`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/user/app.py#L731) validates Argon2id hash, enforces 5-generation password history, revokes secondary sessions, transactional rollback. Verified in `tests/test_user_auth_lifecycle.py` and browser E2E. |
| **User: Password Recovery & Reset** | **NOT IMPLEMENTED** | [`/auth/recovery/request`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/user/app.py#L702), `/auth/recovery/verify`, `/auth/recovery/reset` return explicit controlled HTTP 501 `NOT_IMPLEMENTED`. Frontend UI has no recovery form. |
| **User: Email Change** | **NOT IMPLEMENTED** | [`/profile/change-email/request`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/user/app.py#L717) and `verify` return explicit controlled HTTP 501 `NOT_IMPLEMENTED`. |
| **User: Avatar Upload & Management** | **NOT IMPLEMENTED** | Database column `avatar_object_key` exists in `users.accounts` table schema and ERD, but no avatar upload or update route exists. |
| **Content: Category Management** | **VERIFIED** | Hierarchical categories, admin-restricted CRUD, validation. Verified in `tests/test_content_service.py`. |
| **Content: Thread Lifecycle** | **VERIFIED** | Thread creation, Markdown formatting, editing, soft deletion, category validation, transactional outbox event. Verified in `tests/test_content_service.py` and `tests/test_browser_e2e.py`. |
| **Content: Image Upload & Attachment Lifecycle** | **VERIFIED** | Magic byte validation, Pillow raster verification and normalization, EXIF stripping, 5 MB Gateway streaming pass-through, orphan filesystem reconciliation, row-level concurrency locking on live PostgreSQL. Verified in `tests/test_image_validation.py`, `tests/test_content_service.py`, `tests/test_attachment_concurrency_pg.py`. |
| **Content: Comments & Best Answer** | **VERIFIED** | Comment creation, hierarchical replies, author/admin best answer selection with thread status update. Verified in `tests/test_content_service.py` and `tests/test_browser_e2e.py`. |
| **Interaction: Voting & Reputation** | **VERIFIED** | Up/down voting on threads/comments, score aggregation, reputation calculation, outbox event generation. Verified in `tests/test_interaction_service.py` and `tests/test_browser_e2e.py`. |
| **Interaction: Bookmarks** | **VERIFIED** | User-isolated bookmarking and list retrieval (`/profile/bookmarks`). Verified in `tests/test_interaction_service.py` and `tests/test_browser_e2e.py`. |
| **Interaction: View Tracking & Deduplication** | **VERIFIED** | Sliding window deduplication by session/IP hash, view event outbox propagation (`DEC-005`). Verified in `tests/test_interaction_service.py` and `tests/test_outbox_hardening.py`. |
| **Moderation: Reporting & Queue** | **VERIFIED** | User reporting on threads/comments, deduplication, moderator queue retrieval with filtering, lock/action status tracking. Verified in `tests/test_moderation_service.py` and `tests/test_browser_e2e.py`. |
| **Moderation: Enforcement Actions** | **VERIFIED** | Pin/lock/hide actions, penalty audit logging, outbox event propagation to Content and Search services. Verified in `tests/test_moderation_service.py` and `tests/test_browser_e2e.py`. |
| **Notification: In-App Inbox & Preferences** | **VERIFIED** | In-app notification creation from ingested domain events (replies, votes, moderation), unread counts, mark-as-read, preference toggle. Verified in `tests/test_notification_service.py`. |
| **Notification: External Delivery (Email/Push)** | **PARTIALLY IMPLEMENTED** | Schema contains email notification preference flags, but outbox workers in notification service currently only handle in-app persistence. External email notifications for forum events (non-auth) are deferred. |
| **Search: Full-Text Search & Ingestion** | **VERIFIED** | PostgreSQL `tsvector` indexation, event ingestion idempotency, debounced frontend autocomplete & full search, visibility policy enforcement (hiding deleted/moderated threads). Verified in `tests/test_search_service.py` and `tests/test_browser_e2e.py`. |

---

## 4. Detailed Gap Findings

### Finding GAP-001: Outdated Test Assertion in `tests/test_tier1_api.py` (Stale 501 Expectation)
- **Severity**: Medium
- **Status**: **DEFECT CONFIRMED** (in test suite)
- **File**: [`tests/test_tier1_api.py:195-201`](file:///home/chocolat/Documents/Project/ForumPlatform/tests/test_tier1_api.py#L195-L201)
- **Description**: `test_unimplemented_endpoints_return_501_not_implemented` was written during Task 024 before Image Upload was implemented in Task 027. It asserts that unauthenticated `POST /uploads/images` returns `501 Not Implemented`. In Task 027, image uploads were fully implemented with authentication guards, returning `401 Unauthorized` for unauthenticated requests. Consequently, this test fails.
- **Impact**: Running `pytest tests/test_tier1_api.py` produces a false test failure on an otherwise completely functioning system.
- **Remediation**: Update `test_tier1_api.py` to assert `401 Unauthorized` for unauthenticated `POST /uploads/images`, or update the 501 assertion to test a truly unimplemented route (such as `POST /profile/change-email/request`).

---

### Finding GAP-002: Password Recovery & Account Reset Not Implemented
- **Severity**: High
- **Status**: **NOT IMPLEMENTED**
- **Files**:
  - [`src/services/user/app.py:702-715`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/user/app.py#L702-L715)
  - [`src/docs/forum_public_api.openapi.yaml`](file:///home/chocolat/Documents/Project/ForumPlatform/src/docs/forum_public_api.openapi.yaml)
- **Description**: The endpoints `POST /auth/recovery/request`, `POST /auth/recovery/verify`, and `POST /auth/recovery/reset` exist only as controlled `501 Not Implemented` stubs. Users who forget their password have no recovery path and cannot regain access to their accounts.
- **Impact**: Critical end-user functionality gap. In production, users cannot self-serve account recovery.
- **Remediation**: Implement the three-step password recovery lifecycle: rate-limited email lookup, neutral 202 response (to prevent account enumeration), OTP token generation delivered via SMTP/Mailpit, verification yielding a time-limited reset ticket, and password reset enforcing the 5-generation password history.

---

### Finding GAP-003: Email Change Verification Workflow Not Implemented
- **Severity**: Low
- **Status**: **NOT IMPLEMENTED**
- **Files**: [`src/services/user/app.py:718-729`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/user/app.py#L718-L729)
- **Description**: `POST /profile/change-email/request` and `POST /profile/change-email/verify` return `501 Not Implemented`.
- **Impact**: Authenticated users cannot update their registered email address.
- **Remediation**: Implement two-phase email change verification (re-verifying ownership of the new email address before updating account records).

---

### Finding GAP-004: User Profile Avatar Uploads Not Implemented
- **Severity**: Low
- **Status**: **NOT IMPLEMENTED**
- **Files**:
  - [`src/services/user/models.py:59`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/user/models.py#L59) (`avatar_object_key` column exists)
  - [`src/services/user/app.py`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/user/app.py)
- **Description**: The user schema includes `avatar_object_key`, but there is no endpoint to upload, serve, or link avatar images to user accounts.
- **Impact**: Users cannot customize profile avatars.
- **Remediation**: Leverage Content Service image storage or provide a dedicated avatar upload endpoint with dimension resizing ($256 \times 256$ px) and link to `account.avatar_object_key`.

---

### Finding GAP-005: Notification Service Email/Push Delivery Deferred
- **Severity**: Low
- **Status**: **PARTIALLY IMPLEMENTED**
- **Files**: [`src/services/notification/app.py`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/notification/app.py)
- **Description**: The Notification Service currently creates and delivers in-app notifications only. Outbox events are consumed and stored in the notification database, but email dispatch for thread replies or mentions is not hooked into the background worker.
- **Impact**: Users only receive notifications when logged in and browsing the forum.
- **Remediation**: Add SMTP notification sender task respecting user preferences in `notification_preferences`.

---

## 5. Security and Operational Architecture Assessment

- **Authentication & CSRF**: All mutating endpoints enforce strict CSRF header verification (`X-CSRF-Token`) matching the cryptographically secure token set in cookies during session setup.
- **Rate Limiting**: Sliding window rate limiters protect registration, login, OTP verification, and OTP resend. Test-only endpoints are completely absent from Gateway routing and protected with 404 guards in production mode.
- **Data Isolation**: Database isolation strictly maintained across all six PostgreSQL database schemas (`forum_users`, `forum_content`, `forum_interactions`, `forum_moderation`, `forum_notifications`, `forum_search`). Zero cross-database foreign keys.
- **Transactional Outbox**: All service domain mutations write to their respective local `outbox_events` table within the same ACID transaction. Workers utilize `with_for_update(skip_locked=True)` for non-blocking at-least-once asynchronous event delivery with dead-letter queue support.
- **Storage & Upload Security**: Strict magic-byte inspection, Pillow raster decompression & dimension validation ($8192 \times 8192$ px, $16\text{M}$ pixel ceiling), EXIF data stripping, non-root directory traversal prevention, Gateway streaming proxy with cumulative 5 MB cap, and autonomous filesystem reconciliation for orphaned physical files.
