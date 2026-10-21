# Task 024-R1 / 024-R1-F: Frontend Integration Audit & Evidence Matrix
## Independent Integration Verification & Contract Remediation

### 1. Requirements-to-API Matrix

| Req ID | Screen / Component | Frontend Function / File | Backend API Endpoint & Method | Auth / Role / CSRF | Request / Response Schema | Status | Observed Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **REQ-01** | Screen 01: Registration | `api.register` in `api.js`, `formRegister` in `app.js` | `POST /api/v1/auth/register` | None / Guest / No CSRF required | In: `RegisterRequest` (`username`, `email`, `password`)<br/>Out: 202 `{"status":"pending_verification","message":...}` | `PASS` | Verified neutral anti-enumeration response (DEC-001) in browser and integration tests. |
| **REQ-02** | Screen 01: OTP Verification | `api.verifyOtp` in `api.js`, `formVerifyOtp` in `app.js` | `POST /api/v1/auth/register/verify` | None / Guest / No CSRF required | In: `VerifyOtpRequest` (`email`, `otp`)<br/>Out: 201 `{"status":"activated","message":...}` | `PASS` | 6-slot OTP component + unified `#verify-otp-code` input verified in Playwright browser with Mailpit. |
| **REQ-03** | Screen 01: OTP Resend & Cooldown | `api.resendOtp` in `api.js`, `btnResendOtp` in `app.js` | `POST /api/v1/auth/register/resend-otp` | None / Guest / No CSRF required | In: `ResendOtpRequest` (`email`)<br/>Out: 202 `{"status":"pending_verification",...}` | `PASS` | Authoritative 600s (10 min) TTL and 60s cooldown correctly displayed and tested. |
| **REQ-04** | Screen 02: Login & Session | `api.login` in `api.js`, `formLogin` in `app.js` | `POST /api/v1/auth/login` | None / Guest / No CSRF required | In: `LoginRequest` (`login`, `password`)<br/>Out: 200 `LoginResponse` (`user`, `session`), sets `forum_session` cookie | `PASS` | Cookie verified `HttpOnly`, `SameSite=Lax`, `Path=/`. Zero tokens in `localStorage`/`sessionStorage`. |
| **REQ-05** | Screen 02: Logout & Revocation | `api.logout` in `api.js`, `auth.logout` in `auth.js` | `POST /api/v1/auth/logout` | Session / Authenticated / Requires `X-CSRF-Token` | In: None<br/>Out: 200 `{"status":"logged_out"}` | `PASS` | Revokes session on backend and resets in-memory `AuthManager` state. |
| **REQ-06** | Screen 03: Channel / Category Listing | `api.getCategories` in `api.js`, `loadCategories` in `app.js` | `GET /api/v1/categories` | None / Public / No CSRF | In: None<br/>Out: 200 `CategoryListResponse` (`items`) | `PASS` | Populates category sidebar channel filters dynamically from Content Service. |
| **REQ-07** | Screen 03: Discussion Feed & Pagination | `api.getThreads` in `api.js`, `loadThreads` in `app.js` | `GET /api/v1/threads` | None / Public / No CSRF | In: query `limit`, `cursor`, `category_id`<br/>Out: 200 `ThreadPage` (`items`, `next_cursor`) | `PASS` | Opaque cursor navigation tested without item skipping or corruption. |
| **REQ-08** | Screen 03: Search & Suggestions | `api.searchThreads` in `api.js`, `feedSearchInput` in `app.js` | `GET /api/v1/search` | None / Public / No CSRF | In: query `q`, `category_id`, `tag`<br/>Out: 200 `SearchPage` (`items` with `thread_id`, `title`, `snippet`) | `PASS` | Debounced search (~300ms) with sequence token to drop stale out-of-order responses. |
| **REQ-09** | Screen 04: Thread Detail & View Count | `api.getThread`, `api.recordThreadView` in `api.js` | `GET /api/v1/threads/{id}`, `POST /api/v1/threads/{id}/view` | Public GET / Anonymous or Session View | Out: 200 `ThreadResponse`<br/>View Out: 200 `{"status":"view_recorded"}` | `PASS` | Asynchronous view recording adhering to 30-min session/IP deduplication (ADR-016). |
| **REQ-10** | Screen 04: Thread & Comment Voting | `api.voteThread`, `api.voteComment` in `api.js` | `PUT /api/v1/threads/{id}/vote`, `PUT /api/v1/comments/{id}/vote` | Session / Authenticated / Requires `X-CSRF-Token` | In: `VoteRequest` (`value`: 1 or -1)<br/>Out: 200 `VoteResponse` (`score`, `my_vote`) | `PASS` | Fixed remediation: method PUT; parsed `score` correctly; live counter reconciliation verified. |
| **REQ-11** | Screen 04: Mark Best Answer | `api.setBestAnswer` in `api.js` | `PUT /api/v1/threads/{id}/best-answer` | Session / Owner only / Requires `X-CSRF-Token` | In: `BestAnswerRequest` (`comment_id`)<br/>Out: 200 `ThreadResponse` | `PASS` | Visual `BEST_ANSWER` token rendered and discussion resolved. |
| **REQ-12** | Screen 04: Pin & Lock Mutations | `api.pinThread`, `api.lockThread` in `api.js` | `PUT /api/v1/threads/{id}/pin`, `PUT /api/v1/threads/{id}/lock` | Session / Moderator or Admin / Requires `X-CSRF-Token` | In: `PinThreadRequest` (`is_pinned`), `LockThreadRequest` (`is_locked`)<br/>Out: 200 `ThreadResponse` | `PASS` | Fixed remediation: method PUT; payload keys `is_locked`/`is_pinned`. Status mutation verified. |
| **REQ-13** | Screen 04: Deletion & Conflict Modal | `api.deleteThread` in `api.js` | `DELETE /api/v1/threads/{id}` | Session / Owner or Moderator / Requires `X-CSRF-Token` | Out: 204 No Content, or 409 Conflict (`THREAD_REQUIRES_MODERATION`) | `PASS` | 409 conflict correctly triggers Moderation Conflict Modal with direct flag drawer opening. |
| **REQ-14** | Screen 04: Comment Creation & Hierarchy | `api.createComment` in `api.js` | `POST /api/v1/threads/{id}/comments` | Session / Authenticated / Requires `X-CSRF-Token` | In: `CreateCommentRequest` (`body`, `parent_comment_id`)<br/>Out: 201 `CommentResponse` | `PASS` | Up to 2 hierarchy levels supported, rendered with safe Markdown formatting. |
| **REQ-15** | Screen 05: Thread Editor / Creation | `api.createThread` in `api.js`, `formCreateThread` in `app.js` | `POST /api/v1/threads` | Session / Authenticated / Requires `X-CSRF-Token` | In: `CreateThreadRequest` (`title`, `body`, `category_id`)<br/>Out: 201 `ThreadResponse` | `PASS` | Min length validation (title 10, body 20) and character counter verified in browser. |
| **REQ-16** | Screen 06: Moderation Queue Listing | `api.getModerationReports` in `api.js` | `GET /api/v1/moderation/reports` | Session / Moderator or Admin / No CSRF | In: query `status` (`pending`, `reviewing`, `actioned`, `dismissed`)<br/>Out: 200 `ReportPage` | `PASS` | Fixed remediation: mapped UI tabs to valid backend enum values (`reviewing`, `actioned`). |
| **REQ-17** | Screen 06: Claim Report | `api.claimReport` in `api.js` | `PATCH /api/v1/moderation/reports/{id}/review` | Session / Moderator or Admin / Requires `X-CSRF-Token` | Out: 200 `ReportResponse` (`status="reviewing"`) | `PASS` | Fixed remediation: corrected endpoint path and method from POST `/claim` to PATCH `/review`. |
| **REQ-18** | Screen 06: Resolve Report Actions | `api.resolveReport` in `api.js` | `POST /api/v1/moderation/reports/{id}/actions` | Session / Moderator or Admin / Requires `X-CSRF-Token` | In: `ModerationActionRequest` (`action`, `reason`)<br/>Out: 200 `ModerationAction` | `PASS` | Fixed remediation: updated action vocabulary to `dismiss_report` and `soft_delete`. |
| **REQ-19** | Screen 06: Admin Role Mutation | `api.changeUserRole` in `api.js` | `PATCH /api/v1/admin/users/{id}/role` | Session / Admin only / Requires `X-CSRF-Token` | In: `ChangeUserRoleRequest` (`role`)<br/>Out: 200 `AccountResponse` | `PASS` | RBAC gating: Non-admin gets 403 Forbidden; Admin mutates role successfully. |
| **REQ-20** | Screen 07: User Profile Dossier | `api.getProfile`, `api.getUserReputation` in `api.js` | `GET /api/v1/profile`, `GET /api/v1/users/{id}/reputation` | Session / Authenticated / No CSRF | Out: 200 `ProfileResponse`, 200 `{"reputation_score": int}` | `PASS` | Displays authoritative user information and aggregate score with internal ledger disclaimer. |
| **REQ-21** | Screen 07: User Bookmarks Dossier | `api.getBookmarks`, `api.bookmarkThread`, `api.unbookmarkThread` in `api.js` | `GET /api/v1/profile/bookmarks`, `PUT /api/v1/threads/{id}/bookmark`, `DELETE /api/v1/threads/{id}/bookmark` | Session / Authenticated / Requires `X-CSRF-Token` on mutations | In: None<br/>Out: 200 `BookmarkPage`, 204 on delete | `PASS` | Fixed remediation: corrected GET path to `/profile/bookmarks` and PUT method on bookmark creation. |
| **REQ-22** | Global: Abuse Reporting Drawer | `api.createReport` in `api.js` | `POST /api/v1/reports` | Session / Authenticated / Requires `X-CSRF-Token` | In: `CreateReportRequest` (`target_type`, `target_id`, `reason`, `details`)<br/>Out: 201 `ReportResponse` | `PASS` | Fixed remediation: aligned path to `/reports` and enum reasons (`spam`, `abuse`, etc.). |

---

### 2. Workflow Coverage Table & Test Verification Modes

As required by Task 024-R1-F, each critical user workflow is explicitly mapped to its test implementation, classification mode, and observed assertions:

| Workflow | Test Function | Test File | Verification Mode | Actual Assertions Checked | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Public Feed Loading** | `test_browser_public_home_feed` | `tests/test_browser_e2e.py` | Live browser + real Gateway/backend | `#brand-logo` contains "Forum" and "Platform", heading "Community Discussions" visible, `#btn-create-thread-view` hidden for guest. | `PASS` |
| **Registration & Mailpit OTP** | `test_browser_registration_mailpit_otp_and_login` | `tests/test_browser_e2e.py` | Live browser + real Gateway/backend | Anti-enumeration alert displayed, OTP retrieved from live Mailpit API, `#verify-otp-code` filled, session cookie set, profile visible, logout terminates session. | `PASS` |
| **Invalid Credentials Error** | `test_browser_invalid_credentials_error_handling` | `tests/test_browser_e2e.py` | Live browser + real Gateway/backend | `#global-alert` displays "invalid username/email or password" without username leak. | `PASS` |
| **Incorrect OTP Failure** | `test_browser_incorrect_otp_failure_state` | `tests/test_browser_e2e.py` | Live browser + real Gateway/backend | Input 000000 rejected, `#global-alert` displays "invalid or expired otp". | `PASS` |
| **Thread Creation Lifecycle** | `test_browser_thread_creation_lifecycle` | `tests/test_browser_e2e.py` | Live browser + real Gateway/backend | Category options populated from ContentService, thread form submitted with valid length, "thread published" alert observed, redirected to home. | `PASS` |
| **Client RBAC & Navigation Guard** | `test_browser_rbac_and_restricted_navigation` | `tests/test_browser_e2e.py` | Live browser + real Gateway/backend | Unauthenticated attempt to navigate to `view-admin` blocked by client guard, "access denied" alert displayed, returned to home. | `PASS` |
| **Thread & Comment Voting** | `test_browser_thread_and_comment_voting_reconciliation` | `tests/test_browser_e2e.py` | Live browser + real Gateway/backend | Upvote on thread updates `.vote-counter`; upvote on comment sends `PUT /comments/{id}/vote` with `X-CSRF-Token`, response reconciles in DOM. | `PASS` |
| **Best Answer Selection** | `test_browser_pin_lock_and_best_answer_operations` | `tests/test_browser_e2e.py` | Live browser + real Gateway/backend | Owner marks comment as best answer via `PUT /threads/{id}/best-answer`, alert displays "verified resolution". | `PASS` |
| **Pin & Lock Mutations** | `test_browser_pin_lock_and_best_answer_operations` | `tests/test_browser_e2e.py` | Live browser + real Gateway/backend | Operator toggles pin (`PUT /threads/{id}/pin`) and lock (`PUT /threads/{id}/lock`), global alerts confirm status modification, state restored cleanly. | `PASS` |
| **Bookmark Full Lifecycle** | `test_browser_bookmark_lifecycle` | `tests/test_browser_e2e.py` | Live browser + real Gateway/backend | `PUT /threads/{id}/bookmark` succeeds, thread appears in `#user-bookmarks-list` on `view-profile`, `DELETE /threads/{id}/bookmark` removes item. | `PASS` |
| **Abuse Report Lifecycle** | `test_browser_moderation_report_lifecycle` | `tests/test_browser_e2e.py` | Live browser + real Gateway/backend | Drawer submits `POST /reports`, operator claims via `PATCH /moderation/reports/{id}/review`, operator resolves via `POST /moderation/reports/{id}/actions`. | `PASS` |
| **Admin Role Mutation & RBAC Rejection** | `test_browser_admin_role_mutation_and_rejection` | `tests/test_browser_e2e.py` | Live browser + real Gateway/backend | Member blocked from `view-admin` (client guard) and receives 403 on role mutation; Admin mutates role via `PATCH /admin/users/{id}/role` and reverts successfully. | `PASS` |
| **Thread Deletion 409 Conflict Modal** | `test_browser_thread_deletion_conflict_modal` | `tests/test_browser_e2e.py` | Live browser + real Gateway/backend | Owner attempting to delete thread with comments triggers HTTP 409 `THREAD_REQUIRES_MODERATION`; `#conflict-modal` displays reason; `#btn-conflict-open-flag` launches flag drawer. | `PASS` |
| **Debounced Search & Reset** | `test_browser_search_debounced_query_and_reset` | `tests/test_browser_e2e.py` | Live browser + real Gateway/backend | Debounced search queries `GET /api/v1/search`, renders matching cards with snippet; clearing input restores feed without out-of-order race corruption. | `PASS` |
| **Basic XSS Injection Safety** | `test_browser_xss_injection_payload_safety` | `tests/test_browser_e2e.py` | Browser with mocked/intercepted API responses | `<script>` and `<img>` tags neutralized to `&lt;script&gt;` and `&lt;img`, `window.pwned` remains `false`. | `PASS` |
| **Stored XSS DOM Sinks** | `test_browser_stored_xss_thread_rendering_safety` | `tests/test_browser_e2e.py` | Browser with mocked/intercepted API responses | Payloads across thread title, author, bio, category, admin table, and mod table render inert HTML without script execution (`window.xss_executed` false). | `PASS` |
| **Advanced XSS Sanitizer Matrix** | `test_browser_advanced_xss_sanitization_matrix` | `tests/test_browser_e2e.py` | Browser with mocked/intercepted API responses | `safeMarkdownToHtml` tested against `<svg onload=...>`, `<iframe src="javascript:...">`, `[link](data:...)`, `[link](vbscript:...)`, autofocus; zero script execution. | `PASS` |
| **Static Client Gateway Delivery** | `test_frontend_static_client_delivered_via_gateway` | `tests/test_frontend_e2e.py` | ASGI/in-process integration | `GET /` serves `index.html` with Technical Brutalism layout and charset UTF-8; `GET /static/src/app.js` serves ES module client. | `PASS` |
| **End-to-End Registration & Activation** | `test_end_to_end_user_registration_and_activation` | `tests/test_frontend_e2e.py` | ASGI/in-process integration | 202 Accepted anti-enumeration, OTP delivery to Mailpit sink, 201 Created activation, rejection of invalid/reused OTPs (`OTP_INVALID_OR_EXPIRED`). | `PASS` |
| **Session Cookie & Profile Access** | `test_login_session_cookie_and_profile_access` | `tests/test_frontend_e2e.py` | ASGI/in-process integration | 200 OK login, `forum_session` issued with `HttpOnly`, `Path=/`, `SameSite=lax`; authenticated profile accessible; invalid credentials return 401. | `PASS` |
| **CSRF Protection & Token Validation** | `test_csrf_protection_and_synchronizer_token` | `tests/test_frontend_e2e.py` | ASGI/in-process integration | State-modifying requests rejected with 403 without `X-CSRF-Token`; accepted with valid synchronizer token; GET requests do not require CSRF. | `PASS` |
| **Logout & Session Revocation** | `test_logout_and_session_revocation` | `tests/test_frontend_e2e.py` | ASGI/in-process integration | `POST /auth/logout` revokes session; subsequent requests using same cookie return 401 Unauthorized. | `PASS` |
| **Resend OTP Cooldown & Ineligible Recipient** | `test_resend_otp_cooldown_and_delivery` | `tests/test_frontend_e2e.py` | ASGI/in-process integration | 202 Accepted, new OTP delivered; consecutive request within cooldown window returns 429 `OTP_COOLDOWN_ACTIVE`; ineligible recipient returns neutral 202. | `PASS` |
| **RBAC Security Boundaries** | `test_role_based_access_control_guards` | `tests/test_frontend_e2e.py` | ASGI/in-process integration | Member denied `/admin/users` (403 FORBIDDEN); Admin allowed `/admin/users` (200 OK); role elevation attempts by member rejected. | `PASS` |
| **Gateway 503 Resilient Error Handling** | `test_gateway_returns_canonical_503_when_service_unavailable` | `tests/test_frontend_e2e.py` | ASGI/in-process integration | Upstream disconnection returns canonical JSON error envelope with HTTP 503 and `X-Request-ID`. | `PASS` |

---

### 3. Screen-by-Screen Findings & Remediation

1. **Screen 01 (Registration and OTP):**
   - Verified 202 Accepted anti-enumeration behavior (DEC-001).
   - Unified 6-slot interactive OTP slots with `#verify-otp-code` text input to support both keyboard typing, paste, and automated Playwright fill operations.
   - Authoritative 600s timer with 60s cooldown verified.

2. **Screen 02 (Login and Session):**
   - Opaque HttpOnly cookie session `forum_session` verified with strict browser security attributes (`HttpOnly=True`, `SameSite=Lax`, `Path=/`).
   - Generic credential error messaging prevents username enumeration.
   - Fixed Gateway shared `http_client` session leakage using `StatelessCookieJar()`.
   - Logout invokes `/api/v1/auth/logout` with valid CSRF header and clears in-memory state.

3. **Screen 03 (Feed, Categories and Search):**
   - Category filtering functions against live `Content Service` data.
   - Search result parsing updated to use `thread_id` and `snippet` fields matching `SearchDocument` serialization.
   - Out-of-order search response race condition prevented via monotonic sequence token counter.

4. **Screen 04 (Thread Detail, Comments and Voting):**
   - Vote operations changed from `POST` to `PUT` according to canonical OpenAPI specification.
   - Vote score display updated to read `score` from `VoteResponse` instead of non-existent `new_score`.
   - Moderation pin/lock mutations changed from `PATCH` to `PUT` with body `{ is_pinned }` and `{ is_locked }`.
   - Markdown rendering rigorously audited against stored XSS, script injection, and event handlers.

5. **Screen 05 (Thread Editor):**
   - Client-side validation aligned with backend schemas (Title: 10–150 chars, Body: min 20 chars).
   - Selection-preserving Markdown helper buttons (`BOLD`, `CODE`, `QUOTE`) fixed to preserve cursor position.
   - Local attachment preview clearly marked with note that remote cloud upload is not implemented on node.

6. **Screen 06 (Moderation and RBAC):**
   - Tab status values mapped from UI labels to backend enums (`pending`, `reviewing`, `actioned`, `dismissed`).
   - Claim report endpoint corrected from `POST /moderation/reports/{id}/claim` to canonical `PATCH /moderation/reports/{id}/review`.
   - Action resolution vocabulary corrected from `dismiss`/`delete_content` to `dismiss_report`/`soft_delete`.
   - Admin account role mutation endpoint verified against backend 403 authorization rules.

7. **Screen 07 (Profile and Reputation):**
   - Bookmarks listing endpoint corrected from `/users/me/bookmarks` to `/profile/bookmarks`.
   - Bookmark addition changed from `POST` to `PUT /threads/{id}/bookmark`.
   - Itemized reputation ledger disclaimer verified, preventing fabricated transaction histories.

---

### 4. Security Findings & Invariants

1. **CSRF Protection:**
   - Synchronizer token fetched via `GET /api/v1/auth/csrf`.
   - Sent automatically on all state-modifying HTTP methods (`POST`, `PUT`, `PATCH`, `DELETE`) via `X-CSRF-Token` header.
   - State-changing requests without CSRF token are verified to be rejected with HTTP 403.

2. **XSS & Content Sanitization:**
   - Custom `safeMarkdownToHtml` parser escapes all untrusted characters (`&`, `<`, `>`, `"`, `'`) before parsing formatting tags.
   - Protocol allowlist restricted to `http:`, `https:`, and `mailto:`—blocking `javascript:` and data URIs.
   - Stored XSS browser testing confirms zero script execution in DOM sinks.

3. **Storage Security:**
   - Zero session tokens, passwords, or CSRF tokens persisted in `localStorage` or `sessionStorage`.
   - In-memory `AuthManager` state cleared upon session termination.
