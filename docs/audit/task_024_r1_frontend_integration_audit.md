# Task 024-R1: Frontend Integration Audit & Evidence Matrix
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
| **REQ-10** | Screen 04: Thread & Comment Voting | `api.voteThread`, `api.voteComment` in `api.js` | `PUT /api/v1/threads/{id}/vote`, `PUT /api/v1/comments/{id}/vote` | Session / Authenticated / Requires `X-CSRF-Token` | In: `VoteRequest` (`value`: 1 or -1)<br/>Out: 200 `VoteResponse` (`score`, `my_vote`) | `PASS` | Fixed remediation: changed method from POST to PUT; parsed `score` correctly; self-voting rejected. |
| **REQ-11** | Screen 04: Mark Best Answer | `api.setBestAnswer` in `api.js` | `PUT /api/v1/threads/{id}/best-answer` | Session / Owner only / Requires `X-CSRF-Token` | In: `BestAnswerRequest` (`comment_id`)<br/>Out: 200 `ThreadResponse` | `PASS` | Visual `BEST_ANSWER` token rendered and discussion resolved. |
| **REQ-12** | Screen 04: Pin & Lock Mutations | `api.pinThread`, `api.lockThread` in `api.js` | `PUT /api/v1/threads/{id}/pin`, `PUT /api/v1/threads/{id}/lock` | Session / Moderator or Admin / Requires `X-CSRF-Token` | In: `PinThreadRequest` (`is_pinned`), `LockThreadRequest` (`is_locked`)<br/>Out: 200 `ThreadResponse` | `PASS` | Fixed remediation: changed method from PATCH to PUT; updated payload schema to `is_locked`/`is_pinned`. |
| **REQ-13** | Screen 04: Deletion & Conflict Modal | `api.deleteThread` in `api.js` | `DELETE /api/v1/threads/{id}` | Session / Owner or Moderator / Requires `X-CSRF-Token` | Out: 204 No Content, or 409 Conflict (`THREAD_REQUIRES_MODERATION`) | `PASS` | 409 conflict correctly triggers Moderation Conflict Modal with direct flag drawer opening. |
| **REQ-14** | Screen 04: Comment Creation & Hierarchy | `api.createComment` in `api.js` | `POST /api/v1/threads/{id}/comments` | Session / Authenticated / Requires `X-CSRF-Token` | In: `CreateCommentRequest` (`body`, `parent_comment_id`)<br/>Out: 201 `CommentResponse` | `PASS` | Up to 2 hierarchy levels supported, rendered with safe Markdown formatting. |
| **REQ-15** | Screen 05: Thread Editor / Creation | `api.createThread` in `api.js`, `formCreateThread` in `app.js` | `POST /api/v1/threads` | Session / Authenticated / Requires `X-CSRF-Token` | In: `CreateThreadRequest` (`title`, `body`, `category_id`)<br/>Out: 201 `ThreadResponse` | `PASS` | Min length validation (title 10, body 20) and character counter verified in browser. |
| **REQ-16** | Screen 06: Moderation Queue Listing | `api.getModerationReports` in `api.js` | `GET /api/v1/moderation/reports` | Session / Moderator or Admin / No CSRF | In: query `status` (`pending`, `reviewing`, `actioned`, `dismissed`)<br/>Out: 200 `ReportPage` | `PASS` | Fixed remediation: mapped UI tabs to valid backend enum values (`reviewing`, `actioned`). |
| **REQ-17** | Screen 06: Claim Report | `api.claimReport` in `api.js` | `PATCH /api/v1/moderation/reports/{id}/review` | Session / Moderator or Admin / Requires `X-CSRF-Token` | Out: 200 `ReportResponse` (`status="reviewing"`) | `PASS` | Fixed remediation: corrected endpoint path and method from POST `/claim` to PATCH `/review`. |
| **REQ-18** | Screen 06: Resolve Report Actions | `api.resolveReport` in `api.js` | `POST /api/v1/moderation/reports/{id}/actions` | Session / Moderator or Admin / Requires `X-CSRF-Token` | In: `ModerationActionRequest` (`action`, `reason`)<br/>Out: 200 `ModerationAction` | `PASS` | Fixed remediation: updated action vocabulary to `dismiss_report` and `soft_delete`. |
| **REQ-19** | Screen 06: Admin Role Mutation | `api.changeUserRole` in `api.js` | `PATCH /api/v1/admin/users/{id}/role` | Session / Admin only / Requires `X-CSRF-Token` | In: `ChangeUserRoleRequest` (`role`)<br/>Out: 200 `AccountResponse` | `PASS` | RBAC gating: Non-admin gets 403 Forbidden; Admin mutates role successfully. |
| **REQ-20** | Screen 07: User Profile Dossier | `api.getProfile`, `api.getUserReputation` in `api.js` | `GET /api/v1/profile`, `GET /api/v1/users/{id}/reputation` | Session / Authenticated / No CSRF | Out: 200 `ProfileResponse`, 200 `{"reputation_score": int}` | `PASS` | Displays authoritative user information and aggregate score with internal ledger disclaimer. |
| **REQ-21** | Screen 07: User Bookmarks Dossier | `api.getBookmarks`, `api.bookmarkThread`, `api.unbookmarkThread` in `api.js` | `GET /api/v1/profile/bookmarks`, `PUT /api/v1/threads/{id}/bookmark`, `DELETE /api/v1/threads/{id}/bookmark` | Session / Authenticated / Requires `X-CSRF-Token` on mutations | In: None<br/>Out: 200 `BookmarkPage`, 204 on delete | `PASS` | Fixed remediation: corrected GET path to `/profile/bookmarks` and PUT method on bookmark creation. |
| **REQ-22** | Global: Abuse Reporting Drawer | `api.createReport` in `api.js` | `POST /api/v1/reports` | Session / Authenticated / Requires `X-CSRF-Token` | In: `CreateReportRequest` (`target_type`, `target_id`, `reason`, `details`)<br/>Out: 201 `ReportResponse` | `PASS` | Fixed remediation: aligned dropdown reasons to schema enum (`spam`, `abuse`, `harassment`, etc.). |

---

### 2. Screen-by-Screen Findings & Remediation

1. **Screen 01 (Registration and OTP):**
   - Verified 202 Accepted anti-enumeration behavior (DEC-001).
   - Unified 6-slot interactive OTP slots with `#verify-otp-code` text input to support both keyboard typing, paste, and automated Playwright fill operations.
   - Authoritative 600s timer with 60s cooldown verified.

2. **Screen 02 (Login and Session):**
   - Opaque HttpOnly cookie session `forum_session` verified with strict browser security attributes (`HttpOnly=True`, `SameSite=Lax`, `Path=/`).
   - Generic credential error messaging prevents username enumeration.
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

### 3. Security Findings & Invariants

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
