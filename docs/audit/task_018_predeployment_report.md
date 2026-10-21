# Task 018: Pre-Deployment Audit Report

**Date**: 2026-10-21  
**Project**: Forum Platform Microservices  
**Host**: `dyuxworkstation` (Linux 7.2.9-1-cachyos, x86_64)  
**Evaluator**: Antigravity Autonomous Agent  
**Baseline Git Commit**: `050667ffbf5175ea2c61518555b766e3872019b5`  
**Current Milestone**: Task 018 In Progress (Staging Deployment & Operational Smoke Test)

---

## 1. Executive Summary & Inventory

Task 018 aims to validate the Forum Platform Release Candidate (`v1.0.0-rc1`) under realistic staging conditions, specifically evaluating two mandatory prerequisites:
- **Condition A (HTTPS Ingress & Secure Cookies)**: Verifying that reverse-proxy HTTPS ingress preserves public scheme headers and that session cookies are issued with `Secure=True`, `HttpOnly=True`, and `SameSite=Lax`.
- **Condition B (External SMTP Delivery & Real OTP Verification)**: Delivering an OTP to an external mailbox via a live SMTP provider and successfully completing account registration/verification.

### 1.1. Codebase & Service State
- **Docker Compose Topology**: 10 containers active and healthy (`forum_api_gateway`, `forum_user_service`, `forum_content_service`, `forum_interaction_service`, `forum_moderation_service`, `forum_notification_service`, `forum_search_service`, `forum_postgres`, `forum_redis`, `forum_mailpit`).
- **Logical Databases**: All 6 PostgreSQL logical databases are active and synchronized to their expected Alembic migration heads:
  - `forum_users`: `users_0002`
  - `forum_content`: `content_0003`
  - `forum_interactions`: `interactions_0002`
  - `forum_moderation`: `moderation_0003`
  - `forum_notifications`: `notifications_0002`
  - `forum_search`: `search_0002`
- **Quality Gates**: 100/100 automated pytest tests passing, 0 Ruff lint errors, 0 Pyright static typing errors.

---

## 2. Cookie Configuration Discrepancy Resolution

### 2.1. Identified Issue
In Task 017, an operational discrepancy was flagged:
- The User Service previously evaluated: `cookie_secure = user_settings.environment == "production"`.
- Under staging deployments (`ENVIRONMENT=staging`), this left `cookie_secure = False`, despite staging requiring HTTPS and secure cookies.

### 2.2. Implemented Correction
1. Added explicit `session_cookie_secure: bool | None = Field(default=None)` to `UserSettings` in `src/services/user/config.py`.
2. Implemented `is_cookie_secure()` method on `UserSettings`:
   ```python
   def is_cookie_secure(self) -> bool:
       if self.session_cookie_secure is not None:
           return self.session_cookie_secure
       return self.environment in ("staging", "production")
   ```
3. Updated `/auth/login` and `/auth/logout` handlers in `src/services/user/app.py` to use `cookie_secure = user_settings.is_cookie_secure()`.
4. Result:
   - Development (`ENVIRONMENT=development`): `Secure=False` (enables plain HTTP testing on localhost).
   - Staging (`ENVIRONMENT=staging`): `Secure=True` (enforces encrypted transmission over HTTPS).
   - Production (`ENVIRONMENT=production`): `Secure=True`.
   - Override (`SESSION_COOKIE_SECURE=false/true`): Explicitly configurable if required.

---

## 3. SMTP & Email Delivery Audit

### 3.1. Implementation Review
- **Configuration Fields**: `UserSettings` defines `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `smtp_use_tls`, `smtp_starttls`, `email_from`, and `dev_email_allowlist`.
- **Security & Allowlist**: If `dev_email_allowlist` is provided, addresses not listed fail-closed with `EmailDeliveryError`.
- **Enhanced Capabilities**: Added `smtp_use_tls` (for direct SSL on e.g. port 465) and `smtp_starttls` (for STARTTLS on e.g. port 587) in `_send_smtp_email_sync` in `src/services/user/otp_delivery.py`.

---

## 4. Staging Infrastructure & Live Acceptance Prerequisites

| Staging Prerequisite | Required for | Available in Environment? | Assessment Status |
|---|---|---|---|
| **Public Staging Hostname / DNS** | Ingress routing & TLS | Not provisioned / local workstation only (`192.168.1.212`) | **BLOCKED for Live Internet Test** |
| **Public TLS Certificate** | Condition A (HTTPS Ingress) | No CA-signed public cert for custom staging domain | **BLOCKED for Public CA Test** |
| **External SMTP Credentials** | Condition B (External OTP) | No external credentials provided in `.env` or session | **BLOCKED for Live External Inbox** |
| **Local Docker & Mailpit** | Baseline development & tests | 100% available and passing | **READY** |

As mandated by Section 13 of Task 018:
*A working local proxy configuration does not prove the public ingress works. A successful SMTP login does not prove real OTP delivery. When required external dependencies are unavailable, the live acceptance criteria must be classified as BLOCKED.*
