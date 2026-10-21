# Task 018 / 018-R1: SMTP & OTP Acceptance Report

**Date**: 2026-10-21  
**Project**: Forum Platform Microservices  
**Scope**: Local Mailpit Integration, Live Registration & OTP Verification, Expiry/Single-Use Enforcement, Delivery Failure Safety, and External SMTP Driver Support.

---

## 1. Acceptance Separation & Criteria Definition

In alignment with **Task 018-R1**, email delivery and OTP verification are evaluated under two profiles:
1. **Local Development & Demo Acceptance**: Uses Mailpit (Docker Compose service `forum_mailpit:1025`) as the default SMTP catcher. Verifies that real emails are received in Mailpit via API Gateway registration, OTP codes are extracted from Mailpit, verification succeeds, single-use replays are rejected, and delivery failures fail-closed without unverified account residue.
2. **External SMTP Integration Acceptance**: Supports live third-party SMTP providers (SendGrid, Amazon SES, Mailgun, etc.) over SSL/TLS or STARTTLS with authenticated credentials when configured.

---

## 2. Live Local & Demo Verification Evidence (Mailpit)

### 2.1. Registration & Real Mailpit Delivery
- **Action**: Submitted registration for `tester@example.test` via the API Gateway:
  ```bash
  curl -s -i -X POST http://localhost:8000/api/v1/auth/register \
    -H "Content-Type: application/json" \
    -d '{"username": "mailpit_demo_live", "email": "tester@example.test", "password": "SecurePassword123!"}'
  ```
- **Response**: `HTTP/1.1 202 Accepted` (`{"status":"pending_verification","message":"If the registration details are eligible, a verification code has been sent."}`).
- **Mailpit Query**:
  ```bash
  curl -s http://localhost:8025/api/v1/messages
  ```
- **Result**: Exactly 1 email captured:
  - **ID**: `1WcAsJzfUT4P7qWHuqvQcD`
  - **From**: `noreply@forumplatform.local`
  - **To**: `tester@example.test`
  - **Subject**: `Your Forum Verification Code [Registration]`
  - **Snippet**: `Hello, Your verification code is: 329697 This code will expire in 10 minutes...`

### 2.2. OTP Verification via Gateway
- **Action**: Submitted the captured OTP code (`329697`) via the Gateway:
  ```bash
  curl -s -i -X POST http://localhost:8000/api/v1/auth/register/verify \
    -H "Content-Type: application/json" \
    -d '{"email": "tester@example.test", "otp": "329697"}'
  ```
- **Result**: `HTTP/1.1 201 Created`
  - **Response Payload**: `{"user":{"id":"56db156c-5539-4303-9bd5-c46534927ea4","username":"mailpit_demo_live","display_name":null,"bio":null,"role":"member"}}`
  - Account created and activated.

### 2.3. Single-Use Replay Rejection
- **Action**: Resubmitted the identical OTP code (`329697`) for `tester@example.test`.
- **Result**: `HTTP/1.1 400 Bad Request`
  - **Payload**: `{"error":{"code":"INVALID_OTP","message":"Invalid or expired verification challenge.","details":[],"request_id":"..."}}`
  - Demonstrates strict single-use OTP semantics.

### 2.4. Delivery Failure Handling & Fail-Closed Safety
- **Action**: Submitted registration for unlisted recipient `unlisted_hacker@external.com` (rejected by allowlist).
- **Result**:
  - API returned neutral `202 Accepted` to prevent account enumeration.
  - User Service log: `Email delivery rejected by allowlist: recipient=unlisted_hacker@external.com`.
  - Database Audit:
    - `users.pending_registrations`: `status = 'delivery_failed'`.
    - `users.accounts`: Exactly 0 rows for `unlisted_hacker@external.com`.
  - Proves zero unverified account residue on delivery failure.

---

## 3. External SMTP Driver Verification

### 3.1. Implementation & Unit Test Coverage
- In `src/services/user/otp_delivery.py` and `src/services/user/config.py`:
  - `smtp_use_tls`: Supports direct SSL on port 465 via `smtplib.SMTP_SSL`.
  - `smtp_starttls`: Supports opportunistic STARTTLS on port 587 via `client.starttls()`.
- Verified in `tests/test_email_delivery.py`:
  - Connection refused, recipient refused, allowlist normalization, and safe delivery failure handling all pass cleanly (10/10 tests passed).

### 3.2. Live External Provider Status
- **External Credentials**: None provided in the local environment.
- **Controlled External Mailbox**: None configured.
- **Evaluation**: In accordance with Task 018-R1, external cloud provider delivery is marked **NOT TESTED**, while the code and protocol drivers are fully tested.

---

## 4. Decision Summary for Email Delivery

| Scope | Required Evidence | Result | Notes |
|---|---|---|---|
| **Local / Demo Email Delivery** | Mailpit receipt, OTP extraction, verification via Gateway, single-use rejection, failure cleanup | **PASS** | Fully validated live against running Docker Compose stack |
| **External SMTP Integration** | Live email dispatched to Internet mailbox via third-party SMTP provider | **NOT TESTED** | Code supported & tested; optional for local/demo |
