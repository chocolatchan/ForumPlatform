# Task 024-R1: Change Log & Remediation History

## 1. Overview
This document records the specific integration defects identified during the Task 024-R1 audit, the rationale for each fix, and the modified source files.

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
- **Files Modified:** `frontend/index.html`
- **Issue:** Select dropdown in report drawer contained non-existent reason values (`harassment`, `hate_speech`, `inappropriate_content`). `CreateReportRequest` strictly validates against `r"^(spam|abuse|harassment|wrong_category|misinformation|other)$"`.
- **Fix:** Updated dropdown options to match the backend regex enum.

### Defect 10: Search Response Field Mapping and Race Condition
- **Files Modified:** `frontend/src/app.js`
- **Issue:** Search result cards looked for `t.id` instead of `t.thread_id` from `serialize_search_item`. In addition, debounced search lacked sequence guards against out-of-order network responses.
- **Fix:** Extracted `t.thread_id || t.id`, rendered `t.snippet`, and added sequence token counter to discard stale search responses.

### Defect 11: Exported XSS Helpers & Browser Test Harness Compatibility
- **Files Modified:** `frontend/src/app.js`, `frontend/index.html`
- **Issue:** E2E Playwright tests evaluate `window.escapeHtml`, `window.navigateTo`, and look for `#verify-otp-code` element. `escapeHtml` was not bound to `window` and the OTP code input was `type="hidden"`, preventing Playwright's `page.fill` from interacting.
- **Fix:** Bound `window.escapeHtml` and `window.navigateTo` in `app.js`. Made `#verify-otp-code` an accessible input while preserving the 6-slot component.

---

## 3. Modified Files Summary
- `frontend/index.html`
- `frontend/src/api.js`
- `frontend/src/app.js`
- `docs/audit/task_024_r1_frontend_integration_audit.md`
- `docs/audit/task_024_r1_validation_report.md`
- `docs/audit/task_024_r1_change_log.md`
