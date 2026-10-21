# Task 024-R1 / 024-R1-F: Change Log & Remediation History

## 1. Overview
This document records the specific integration defects identified during the Task 024-R1 audit and 024-R1-F verification closure, the rationale for each fix, and the modified source files.

---

## 2. Remediated Integration Defects

### Defect 1: HTTP Method Mismatch on Voting Endpoints
- **Files Modified:** `frontend/src/api.js`
- **Issue:** Thread and comment votes were using `POST /threads/{id}/vote` and `POST /comments/{id}/vote`. The backend and canonical OpenAPI contract specify `PUT`.
- **Fix:** Updated `voteThread` and `voteComment` in `ApiClient` to use `PUT`.

### Defect 2: Vote Score Response Parsing
- **Files Modified:** `frontend/src/app.js`
- **Issue:** `app.js` attempted to read `vRes.data.new_score`. The OpenAPI contract and backend `VoteResponse` return `score`.
- **Fix:** Changed vote score reading to `vRes.data.score ?? 0`.

### Defect 3: HTTP Method and Payload Mismatch on Pin and Lock
- **Files Modified:** `frontend/src/api.js`
- **Issue:** `lockThread` and `pinThread` used `PATCH` with bodies `{ locked }` and `{ pinned }`. The Content Service specifies `PUT` with schemas `LockThreadRequest` (`is_locked`) and `PinThreadRequest` (`is_pinned`).
- **Fix:** Updated HTTP method to `PUT` and payload keys to `is_locked` and `is_pinned`.

### Defect 4: Moderation Claim Endpoint Path and Method
- **Files Modified:** `frontend/src/api.js`
- **Issue:** `claimReport` sent `POST /moderation/reports/{id}/claim`. The Moderation Service defines `PATCH /moderation/reports/{id}/review`.
- **Fix:** Changed `claimReport` to invoke `PATCH /moderation/reports/{id}/review`.

### Defect 5: Moderation Resolve Action Vocabulary
- **Files Modified:** `frontend/src/app.js`
- **Issue:** `app.js` dispatched action names `"dismiss"` and `"delete_content"`. The Moderation Service enum restricts actions to `"dismiss_report"`, `"soft_delete"`, `"pin"`, `"lock"`, etc.
- **Fix:** Updated resolution actions to `"dismiss_report"` and `"soft_delete"`.

### Defect 6: Moderation Queue Status Tab Mapping
- **Files Modified:** `frontend/src/app.js`
- **Issue:** UI moderation tabs queried `claimed` and `resolved`. Backend `Report` status enum defines `pending`, `reviewing`, `actioned`, `dismissed`.
- **Fix:** Mapped UI tabs to query `pending`, `reviewing`, and `actioned`.

### Defect 7: Bookmarks Listing Route
- **Files Modified:** `frontend/src/api.js`
- **Issue:** `getBookmarks` queried `/users/me/bookmarks`. The Gateway and Interaction Service route is `GET /profile/bookmarks`.
- **Fix:** Updated endpoint path in `api.js` to `/profile/bookmarks`.

### Defect 8: Bookmark Creation Method
- **Files Modified:** `frontend/src/api.js`
- **Issue:** `bookmarkThread` invoked `POST /threads/{id}/bookmark`. Interaction Service and OpenAPI specification define `PUT /threads/{id}/bookmark`.
- **Fix:** Changed method in `bookmarkThread` to `PUT`.

### Defect 9: Abuse Report Violation Reason Enum Alignment
- **Files Modified:** `frontend/src/app.js`
- **Issue:** Report drawer dropdown options (`spam`, `harassment`, `inappropriate`) did not align with backend enum.
- **Fix:** Updated options to `spam`, `abuse`, `harassment`, `wrong_category`, `misinformation`, `other`.

### Defect 10: Abuse Report Creation Gateway Route Mismatch
- **Files Modified:** `frontend/src/api.js`
- **Issue:** `createReport` invoked `POST /moderation/reports`. Upstream router maps report creation to `POST /reports`.
- **Fix:** Corrected path in `ApiClient.createReport` to `POST /reports`.

### Defect 11: Gateway Cross-Request Session Leakage on Shared Client
- **Files Modified:** `src/gateway/app.py`
- **Issue:** The Gateway's singleton `httpx.AsyncClient` kept an internal cookie jar by default. When an upstream login returned `Set-Cookie: forum_session=...`, the jar stored the session and leaked it to subsequent unauthenticated requests made through the gateway.
- **Fix:** Injected `StatelessCookieJar()` into `httpx.AsyncClient(..., cookies=StatelessCookieJar())` so the gateway strictly streams cookies to/from downstream clients without persisting them in its shared pool.

---

## 3. Test Harness Compatibility Changes

### Change H1: OTP Slot Playwright Autofill Compatibility
- **Files Modified:** `frontend/index.html`
- **Description:** Added a single continuous `#verify-otp-code` text input while keeping the visual 6-slot technical brutalist layout to ensure compatibility with automated Playwright browser tests.

### Change H2: Navigation Transition Alert Preservation
- **Files Modified:** `frontend/src/app.js`
- **Description:** Updated `navigateTo(viewId, preserveAlert = false)` to prevent clearing the `#global-alert` element immediately upon view transition when displaying post-action confirmation banners.

### Change H3: Exposed Sanitizer Global for Test Isolation
- **Files Modified:** `frontend/src/app.js`
- **Description:** Exposed `window.safeMarkdownToHtml = safeMarkdownToHtml` to allow direct in-browser injection matrix testing without DOM dependency coupling.

### Change H4: User Service In-Memory Rate Limiter Reset Fixture
- **Files Modified:** `tests/test_browser_e2e.py`
- **Description:** Added `per_test_environment_cleanup` autouse fixture to reset the live user service's in-memory sliding window rate limits between browser tests, preventing 429 errors from high-frequency automated execution.

---

## 4. Verification Tests Added in 024-R1-F

- `tests/test_browser_e2e.py::test_browser_thread_and_comment_voting_reconciliation`
- `tests/test_browser_e2e.py::test_browser_pin_lock_and_best_answer_operations`
- `tests/test_browser_e2e.py::test_browser_bookmark_lifecycle`
- `tests/test_browser_e2e.py::test_browser_moderation_report_lifecycle`
- `tests/test_browser_e2e.py::test_browser_admin_role_mutation_and_rejection`
- `tests/test_browser_e2e.py::test_browser_thread_deletion_conflict_modal`
- `tests/test_browser_e2e.py::test_browser_search_debounced_query_and_reset`
- `tests/test_browser_e2e.py::test_browser_advanced_xss_sanitization_matrix`
