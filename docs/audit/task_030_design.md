# Task 030 Design: Avatar Lifecycle & Email Change Lifecycle

## Executive Summary

Task 030 completes two fundamental user account lifecycle workflows in ForumPlatform:
1. **Avatar Lifecycle**: End-to-end setting, replacing, serving, and deleting user profile avatars while preserving database isolation (DEC-002), reusing the image storage pipeline from Task 027, enforcing ownership invariants, and maintaining visibility access controls.
2. **Email Change Lifecycle**: Two-phase verification workflow (`POST /profile/change-email/request` and `POST /profile/change-email/verify`) protected by CSPRNG OTP generation, rate limiting, anti-enumeration responses (DEC-001), 60-second cooldown windows, resend quotas, and row-level PostgreSQL transaction locking with strict uniqueness constraints.

---

## Architecture & Service Boundaries

### 1. Database Isolation & Communication (DEC-002)
- **User Service (`forum_users`)**: Authoritative owner of `users.accounts` (`avatar_object_key`, `email`, `email_normalized`), sessions, and `users.email_change_requests`.
- **Content Service (`forum_content`)**: Authoritative owner of file uploads, image normalization/re-encoding, and `content.attachments` (`status="pending" | "linked" | "avatar" | "deleted"`).
- **Inter-service Communication**:
  - Direct database cross-schema querying is strictly avoided.
  - User Service communicates with Content Service via internal authenticated REST calls protected by `X-Internal-Key` (`user_settings.internal_service_secret`):
    - `POST /internal/attachments/{attachment_id}/claim-avatar`: Validates that the attachment exists, belongs to `user_id`, and transitions its status from `pending` to `avatar`.
    - `POST /internal/attachments/{attachment_id}/release-avatar`: Transitions status to `deleted` and asynchronously unlinks physical storage.

---

## Component Deep Dive

### Part A: Avatar Lifecycle

```
[Browser Client]
       │
       ▼ (1) Upload image (POST /uploads/images)
 [API Gateway] ──> [Content Service] ──> Save normalized file & Attachment(status="pending")
       │
       ▼ (2) Claim avatar (PUT /profile/avatar {image_id})
 [API Gateway] ──> [User Service]
                        │ (Internal REST with X-Internal-Key)
                        ├──> [Content Service] (POST /internal/attachments/{id}/claim-avatar)
                        │         - Checks author_id == user_id
                        │         - Updates Attachment.status = "avatar"
                        │
                        ├──> Updates Account.avatar_object_key = image_id
                        │
                        └──> Releases old avatar if replaced
```

#### Avatar Invariants:
1. **Storage Pipeline Reuse**: Avatars leverage `/uploads/images` with magic byte sniffing, Pillow normalization, and EXIF stripping (Task 027).
2. **Ownership Guard**: User Service passes authenticated `user.id` to Content Service. Attempts to claim an attachment uploaded by another user fail with `403 FORBIDDEN` (`FORBIDDEN_ATTACHMENT_CLAIM`).
3. **Status Safety**: Non-pending attachments or attachments linked to threads cannot be claimed.
4. **Public Serving**: Content Service `GET /uploads/images/{image_id}` allows public unauthenticated caching (`Cache-Control: public, max-age=86400`) if `attachment.status == "avatar"`.
5. **Deletion**: `DELETE /profile/avatar` clears `Account.avatar_object_key` and notifies Content Service to mark the attachment `deleted` and unlink physical storage.

---

### Part B: Email Change Lifecycle

```
[Authenticated User]
       │
       ▼ (1) POST /profile/change-email/request {new_email, password}
 [User Service]
       ├── Verify session & CSRF header
       ├── Verify password (re-authentication)
       ├── Check new_email != current_email
       ├── Anti-enumeration query: If taken by another account, return neutral 202 Accepted without sending OTP
       ├── Lock Account row with FOR UPDATE
       ├── Check resend cooldown (60s) & quota (max 3 resends)
       ├── Generate 6-digit OTP (10 min TTL)
       ├── Store hash in users.email_change_requests
       └── Deliver OTP via Mailpit/SMTP allowlist

[Authenticated User]
       │
       ▼ (2) POST /profile/change-email/verify {otp}
 [User Service]
       ├── Lock EmailChangeRequest row with FOR UPDATE
       ├── Check expiry (expires_at) and attempts (attempts <= 5)
       ├── Constant-time OTP hash verification (verify_otp_hash)
       ├── Lock Account row & re-check uniqueness: Account.email_normalized == new_email_normalized
       ├── Update Account.email and Account.email_normalized
       └── Mark EmailChangeRequest.completed_at = now
```

#### Security Properties:
1. **Anti-Enumeration (DEC-001)**: If a requested new email is already associated with another active account, the endpoint returns HTTP 202 without sending an email or creating a token.
2. **Row Locking & Serialization**: Concurrent requests for the same account lock the `Account` row to serialize initial creation and enforce the 60-second cooldown window.
3. **Single Consumption**: OTP verification locks the `EmailChangeRequest` row; concurrent submissions cannot both consume the challenge.
4. **Fail-Closed Delivery**: If delivery via SMTP or allowlist fails, the pending request is marked completed/invalidated so no orphaned usable OTP is left active.
