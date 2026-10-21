# Task 019: Authentication & End-to-End User Journey Audit Report

**Date:** 2026-10-21  
**Milestone:** Task 019  
**Status:** PASS  

---

## 1. Scope & Objective

Audit the security and correctness of the end-to-end authentication lifecycle, user session management, anti-enumeration protections, and transactional OTP handling between the Frontend client and backend microservices via the API Gateway.

---

## 2. Authentication Lifecycle Verification

### 2.1 Registration & Anti-Enumeration (DEC-001)
- **Endpoint:** `POST /api/v1/auth/register`
- **Payload:** `{"username": "<str>", "email": "<str>", "password": "<str>"}`
- **Response:** `202 Accepted` (`{"status": "pending_verification", "message": "If the registration details are eligible, a verification code has been sent."}`)
- **Audit Finding:**
  - Regardless of whether an account or pending challenge already exists, the API Gateway returns a uniform `202 Accepted` response.
  - No database records leak usernames, email existence, or registration statuses.
  - If the recipient address is disallowed by the development allowlist, the challenge transitions to `delivery_failed` without inserting phantom users into `users.accounts`.
  - Passwords are validated for complexity and stored as Argon2id hashes. Plaintext passwords never reach storage or logs.

### 2.2 OTP Delivery & Verification (FR-2)
- **Endpoint:** `POST /api/v1/auth/register/verify`
- **Delivery Mechanism:** Dispatched via Mailpit (`http://localhost:8025`) or memory testing sink.
- **Audit Finding:**
  - 6-digit cryptographic OTPs have a 10-minute validity window.
  - OTP codes are hashed with SHA-256 before persistence in `users.pending_registrations`.
  - Single-use guarantee: After verification, the pending challenge transitions to `consumed` and the OTP code is invalidated immediately.
  - Retries: If an incorrect OTP is supplied, attempts increment (max 5 allowed). Exceeding 5 attempts marks the challenge `expired`.
  - Resend requests (`POST /api/v1/auth/register/resend-otp`) enforce a 60-second cooldown period, preventing OTP spamming.

### 2.3 Session Management (ADR-003)
- **Endpoint:** `POST /api/v1/auth/login`
- **Payload:** `{"login": "<username_or_email>", "password": "<str>"}`
- **Response:** `200 OK`
- **Audit Finding:**
  - The API Gateway returns an opaque `forum_session` cookie with flags: `HttpOnly=True`, `SameSite=Lax`, `Path=/`.
  - Zero JWT tokens are used. Client JavaScript cannot access session tokens through `document.cookie`.
  - The API client and auth manager maintain user state entirely in memory and do not write session identifiers to `localStorage` or `sessionStorage`.
  - Concurrent session tracking in Redis and PostgreSQL supports idle timeout (4 hours) and absolute lifetime (24 hours).

### 2.4 CSRF Protection (ADR-004)
- **Endpoint:** `GET /api/v1/auth/csrf`
- **Header:** `X-CSRF-Token`
- **Audit Finding:**
  - Synchronizer tokens are cryptographically signed using HMAC with a rotating salt.
  - All state-mutating HTTP methods (`POST`, `PUT`, `PATCH`, `DELETE`) require a valid `X-CSRF-Token` header when a session cookie is present.
  - Requests missing `X-CSRF-Token` or presenting forged tokens are rejected with `403 Forbidden` (`FORBIDDEN`).

### 2.5 Logout & Revocation (FR-4)
- **Endpoint:** `POST /api/v1/auth/logout`
- **Response:** `204 No Content`
- **Audit Finding:**
  - Revokes active session records in Redis and PostgreSQL immediately.
  - Emits a cleared `Set-Cookie` header (`forum_session=""; Max-Age=0; Path=/`).
  - Subsequent requests presenting the revoked session cookie receive canonical `401 Unauthorized`.

---

## 3. End-to-End Test Matrix

| Test Scenario | Gateway Route | Expected Status | Result |
| :--- | :--- | :---: | :---: |
| Serve Frontend SPA | `GET /` | `200 OK` | PASS |
| Registration anti-enumeration | `POST /api/v1/auth/register` | `202 Accepted` | PASS |
| Valid OTP activation | `POST /api/v1/auth/register/verify` | `201 Created` | PASS |
| Non-existent challenge rejection | `POST /api/v1/auth/register/verify` | `400 Bad Request` | PASS |
| Invalid OTP code rejection | `POST /api/v1/auth/register/verify` | `400 Bad Request` | PASS |
| Reused OTP rejection | `POST /api/v1/auth/register/verify` | `400 Bad Request` | PASS |
| Invalid credentials login | `POST /api/v1/auth/login` | `401 Unauthorized` | PASS |
| Valid login session issuance | `POST /api/v1/auth/login` | `200 OK` (Set-Cookie) | PASS |
| Authenticated profile retrieval | `GET /api/v1/profile` | `200 OK` | PASS |
| Mutation without CSRF token | `PATCH /api/v1/profile` | `403 Forbidden` | PASS |
| Mutation with forged CSRF token | `PATCH /api/v1/profile` | `403 Forbidden` | PASS |
| Valid mutation with CSRF token | `PATCH /api/v1/profile` | `200 OK` | PASS |
| Session revocation on logout | `POST /api/v1/auth/logout` | `204 No Content` | PASS |
| Revoked session profile access | `GET /api/v1/profile` | `401 Unauthorized` | PASS |
| Resend OTP cooldown flow | `POST /api/v1/auth/register/resend-otp` | `202 Accepted` | PASS |
| Gateway downstream 503 handling | `GET /api/v1/categories` | `503 Service Unavailable` | PASS |
