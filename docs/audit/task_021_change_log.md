# Task 021: Change Log & Implementation Decisions

**Date:** 2026-10-21  
**Scope:** Complete record of code modifications, bug fixes, hardening measures, and architectural preservation details executed under Task 020-R1 and Task 021.

---

## 1. Summary of Changes

### 1.1 Frontend Hardening (`frontend/src/app.js`)
- **Alert Race Condition Fix:**
  - Added `clearTimeout(alertTimeout)` in `showAlert()` to prevent race conditions during rapid state transitions (e.g. navigation immediately following error or success display), ensuring alert messages do not unexpectedly vanish prematurely.
- **XSS Verification & DOM Audit:**
  - Audited all 10 `innerHTML` DOM sink injection points.
  - Verified that all dynamic user-provided fields (`title`, `content`, `author_username`, `email`, `role`, `reputation`, `reason`, `target_type`) pass through `escapeHtml()`.

### 1.2 Deprecation Warning Fix (`src/services/interaction/app.py`)
- Replaced direct usage of `starlette.status.HTTP_422_UNPROCESSABLE_ENTITY` with `getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)` to resolve Python runtime deprecation warnings across Starlette and FastAPI versions.

### 1.3 Test Suite Hardening (`tests/`)
- **`tests/test_frontend_e2e.py`:**
  - Expanded `test_csrf_protection_on_state_mutating_endpoints` to cover the full CSRF lifecycle:
    1. Rejection on missing header (403).
    2. Rejection on forged/corrupted token (403).
    3. Acceptance on valid token with active session (201).
    4. Token reuse within the same session (synchronizer token pattern).
    5. Rejection after session termination/logout (401).
    6. Verified zero side-effects on database state for rejected attempts.
- **`tests/test_browser_e2e.py`:**
  - Added `test_browser_stored_xss_thread_rendering_safety` verifying that injected HTML `<script>` and `<img>` tags are safely entity-escaped and do not execute script context in headless Firefox.
- **`tests/test_crud_integration.py`:**
  - Updated HTTPX client cookie setting from per-request dictionary to `client.cookies.set("forum_session", token)` to eliminate test deprecation warnings.
- **`tests/test_config.py`:**
  - Added explicit `database_url` parameter in `BaseServiceSettings` instantiation tests to ensure complete static type-checking compliance with Pyright.

---

## 2. Architectural Invariants Maintained

Throughout Task 020-R1 and Task 021, the following core constraints were strictly preserved:
1. **Six-Microservice Architecture:** User, Content, Interaction, Moderation, Notification, and Search services remained completely intact.
2. **API Gateway Ingress:** All frontend and external communication enters exclusively through the API Gateway on port 8000.
3. **Database Isolation:** PostgreSQL maintains 6 distinct logical databases. Foreign key constraints are local; no cross-database foreign keys exist.
4. **Session Authentication:** Server-side sessions stored in Redis (`forum_session` cookie). JWT was not introduced.
5. **Transactional Outbox:** At-least-once domain event propagation, worker lease claiming via `SELECT FOR UPDATE SKIP LOCKED`, and consumer idempotency tables preserved.
6. **Email Testing Invariant:** Mailpit remains the primary sink for local testing and demo validation.
