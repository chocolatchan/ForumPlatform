# Task 020: Change Log

## Overview
This change log summarizes all modifications made during the execution of **Task 020: Browser-Level Validation, Frontend Security & Demo Readiness**.

---

## 1. Browser Test Automation Infrastructure
- **Installed Tooling:** `playwright` (v1.58.0) and `pytest-playwright` in `./venv` with headless Mozilla Firefox binary (`/home/chocolat/.cache/ms-playwright/firefox-1555`).
- **Added Automated Test Suite:** `tests/test_browser_e2e.py`:
  - `test_browser_public_home_feed`: Verifies unauthenticated home feed, brand rendering, and guest UI controls.
  - `test_browser_registration_mailpit_otp_and_login`: Validates end-to-end registration, neutral enumeration protection, live Mailpit OTP capture, login, session cookie attributes (`HttpOnly`, `SameSite=Lax`), profile verification, and logout.
  - `test_browser_invalid_credentials_error_handling`: Exercises incorrect username/password and verifies safe 401 error messaging.
  - `test_browser_incorrect_otp_failure_state`: Validates error state and retry persistence on wrong verification code.
  - `test_browser_thread_creation_lifecycle`: Verifies category loading, markdown discussion publishing, and live DOM rendering.
  - `test_browser_rbac_and_restricted_navigation`: Evaluates client-side routing guard against unauthorized access to administrative panels.
  - `test_browser_xss_injection_payload_safety`: Evaluates escaping and neutralization of `<script>` and `<img>` DOM injection attempts.

---

## 2. Frontend Application Hardening (`frontend/`)
- `frontend/index.html`:
  - Corrected script import path to `/static/src/app.js` to align with FastAPI gateway static asset mounting.
- `frontend/src/app.js`:
  - Added HTML escaping (`escapeHtml()`) to dynamic category IDs, user profiles, and error messages.
  - Enhanced `navigateTo(viewId, preserveAlert = false)` to prevent wiping success notices during intentional view changes (e.g. registration -> OTP, OTP -> login).
  - Injected client-side route guard in `navigateTo` for `view-admin` protecting against non-moderator/admin access.
  - Exposed `escapeHtml`, `navigateTo`, and `auth` on `window` object for automated test harness evaluation.
- `frontend/src/api.js`:
  - Aligned `createThread` payload parameter key to `{ title, body, category_id }` matching OpenAPI `CreateThreadRequest` specification.

---

## 3. Backend RBAC Consistency Hardening (`src/services/user/app.py`)
- `PATCH /admin/users/{user_id}/role`:
  - Replaced `require_role("admin", "moderator")` with `require_role("admin")`.
  - Aligned backend endpoint with OpenAPI specification (`summary: Change user role (Admin)`), ensuring that only administrators can alter user privilege tiers while moderators are restricted to content and thread management.

---

## 4. Documentation & Audit Artifacts
- Created `docs/audit/task_020_browser_e2e_report.md`: Complete matrix of browser journeys, test findings, and verification results.
- Created `docs/audit/task_020_frontend_security_report.md`: Audit of HttpOnly cookies, Web Storage isolation, XSS escaping, and CSRF protection.
- Created `docs/audit/task_020_rbac_consistency_report.md`: Audit of privilege matrix across OpenAPI, backend dependencies, and frontend UI.
- Created `docs/audit/task_020_quality_warnings_report.md`: Investigation of 193 pytest warnings from `httpx` and `fastapi`.
- Created `docs/audit/task_020_demo_acceptance_report.md`: Reproducible local developer and demonstration script.
- Updated `docs/audit/implementation_readiness.md`: Recorded Task 020 milestone completion.
