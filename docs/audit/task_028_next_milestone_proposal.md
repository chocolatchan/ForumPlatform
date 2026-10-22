# Task 028: Next Milestone Selection & Implementation Roadmap

## 1. Executive Summary

Based on the evidence collected in the Task 028 Post-Implementation Gap Audit, the core architecture of ForumPlatform is solid, resilient, and well-covered by automated tests. Out of 150 automated test scenarios across unit, integration, and full Playwright browser suites, 149 pass consistently.

However, three distinct areas remain either incomplete or require immediate stabilization. Below are the three recommended next milestones, prioritized by business impact, security posture, and contract completeness.

---

## 2. Recommended Next Milestones

### Milestone 1 (Top Priority): Task 029 — Account Recovery & Password Reset Lifecycle
**Theme**: Complete Core Identity Lifecycle & Remediate Stale Test Contract.

- **Objective**: Implement the remaining user recovery endpoints (`POST /auth/recovery/request`, `POST /auth/recovery/verify`, `POST /auth/recovery/reset`), complete the end-to-end account recovery user journey on the frontend, and fix the stale test assertion in `test_tier1_api.py`.
- **Scope**:
  1. **Backend (User Service)**:
     - `POST /auth/recovery/request`: Rate-limited by IP and email; neutral HTTP 202 response regardless of account existence (prevent account enumeration); generate high-entropy alphanumeric OTP stored with 15-minute TTL; deliver via `otp_delivery_sink` (SMTP / Mailpit).
     - `POST /auth/recovery/verify`: Rate-limited OTP verification; on success, generate a short-lived cryptographically signed reset ticket (or ephemeral reset token) valid for 10 minutes.
     - `POST /auth/recovery/reset`: Validates reset ticket; applies new password hash (Argon2id); enforces 5-generation password history (`password_history` table); revokes all active sessions for the account; invalidates the reset ticket.
  2. **Frontend UI**:
     - Add "Forgot Password?" trigger on the login modal/view.
     - Add recovery request view (Email input), OTP verification step, and new password form.
  3. **Contract Test Remediation**:
     - Update `tests/test_tier1_api.py:195` to assert `401 Unauthorized` for unauthenticated image uploads instead of outdated `501`.
- **Dependencies**: Mailpit / SMTP delivery infrastructure (already verified in Task 018/026), existing `password_history` schema.
- **Acceptance Criteria**:
  - Full automated coverage in `tests/test_user_auth_lifecycle.py` and real browser journey in `tests/test_browser_e2e.py`.
  - Zero account enumeration risk (neutral responses).
  - All 150+ test suites passing with 100% success rate.
- **Explicit Exclusions**: SMS-based recovery; third-party OAuth/OIDC recovery.

---

### Milestone 2 (Medium Priority): Task 030 — User Profile Avatars & Email Change Lifecycle
**Theme**: Complete Account Customization & Credential Management.

- **Objective**: Implement profile avatar image uploads linked to `users.accounts.avatar_object_key`, and the two-phase email change verification workflow.
- **Scope**:
  1. **Avatar Upload**:
     - Integrate with Content Service upload storage or expose user avatar endpoint.
     - Normalize avatar dimensions ($256 \times 256$ px PNG/JPEG/WEBP) via Pillow.
     - Render avatar in user profile modal and navigation bar.
  2. **Email Change Workflow**:
     - `POST /profile/change-email/request` and `POST /profile/change-email/verify`.
     - Two-phase verification ensuring the user proves access to the new email address before updating `users.accounts.email`.
- **Dependencies**: Task 027 image upload and storage normalization mechanisms; Task 026 session management.
- **Acceptance Criteria**:
  - Profile avatar persists and displays across page reloads.
  - Email changes update database only upon OTP confirmation from new mailbox.
- **Explicit Exclusions**: Gravatar integration; animated GIF avatar support.

---

### Milestone 3 (Lower Priority): Task 031 — Outbound Event Notification Worker (Email Dispatch)
**Theme**: Multi-Channel User Engagement & External Notifications.

- **Objective**: Extend Notification Service background worker to deliver email alerts for subscribed forum events (thread replies, mention notifications) according to user preferences.
- **Scope**:
  1. Connect `notification_service` outbox worker or subscriber to SMTP client.
  2. Respect `notification_preferences` flags (e.g. `email_on_reply`, `email_on_mention`).
  3. Bounded delivery rate and non-blocking retry mechanism.
- **Dependencies**: Mailpit SMTP container, Notification Service outbox tables.
- **Acceptance Criteria**:
  - Valid emails delivered to Mailpit when a subscribed user receives a thread reply.
  - Opt-out in preferences stops email delivery while preserving in-app notifications.
- **Explicit Exclusions**: WebPush notifications; mobile APNS/FCM push.

---

## 3. Recommended Immediate Action

The recommended next implementation milestone is **Task 029: Account Recovery & Password Reset Lifecycle**.
This milestone addresses the most critical functional gap (GAP-002) identified during the audit and restores the test suite to a pristine 100% pass rate by resolving the stale test assertion (GAP-001).
