# Task 024: Frontend Requirements-to-API Matrix & Reconnaissance

## 1. Overview & Objective

This document establishes the authoritative mapping between the seven required screens from the **Modern Technical Brutalism × Swiss Minimalism** frontend UI specification and the actual ForumPlatform backend API Gateway endpoints.

All public API requests MUST traverse the API Gateway (`http://localhost:8000/api/v1/`), preserving:
- Opaque cookie-based server sessions (`forum_session`, HttpOnly, SameSite=Strict).
- Session-bound HMAC synchronizer CSRF protection on mutating endpoints (`X-CSRF-Token`).
- Server-authoritative RBAC (`guest`, `member`, `moderator`, `admin`).
- Neutral anti-enumeration patterns (DEC-001: 202 Accepted on registration).

---

## 2. Requirements-to-API Mapping Matrix

| Screen / Flow | Required UI Component / User Action | Backend Endpoint & Method | Contract & Payload Schema | Status / Backend Support | Notes & Constraints |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Global Shell** | Fetch CSRF Token | `GET /api/v1/auth/csrf` | Returns `{ csrf_token: str }` | **CONFIRMED** | Stored in-memory only, bound to session cookie |
| | Get Active Session / Identity | `GET /api/v1/auth/session` | Returns `{ user: UserPublic, session: SessionPublic }` | **CONFIRMED** | Server-authoritative role & identity |
| | Liveness & Readiness | `GET /health/live`, `GET /health/ready` | Returns `{ status: "ok" }` | **CONFIRMED** | Gateway health |
| **Screen 01: Registration & OTP** | Submit Registration Form | `POST /api/v1/auth/register` | `{ username, email, password }` | **CONFIRMED** | Returns 202 Accepted (anti-enumeration, DEC-001) |
| | Verify 6-digit numeric OTP | `POST /api/v1/auth/register/verify` | `{ email, otp: "^[0-9]{6}$" }` | **CONFIRMED** | Activates account; returns 201 Created |
| | Resend Registration OTP | `POST /api/v1/auth/register/resend-otp`| `{ email }` | **CONFIRMED** | Max 3 resends, 60s cooldown enforced |
| | *OTP Expiry Display* | UI timer | **Discrepancy: Spec 5m vs Backend 10m (600s)** | **RESOLVED** | Display **10 minutes (600s)** as backed by server |
| **Screen 02: Login & Session** | Login (username or email) | `POST /api/v1/auth/login` | `{ login, password }` | **CONFIRMED** | Returns 200 OK + `Set-Cookie: forum_session` |
| | Logout | `POST /api/v1/auth/logout` | Headers: `X-CSRF-Token` | **CONFIRMED** | Revokes session in Redis; clears cookie |
| | Invalid Credentials Error | `POST /api/v1/auth/login` | Returns 401 `INVALID_CREDENTIALS` | **CONFIRMED** | Neutral message, never reveals which field failed |
| | Rate limit error | Any | Returns 429 `RATE_LIMITED` | **CONFIRMED** | Handles retry-after if provided |
| **Screen 03: Feed & Categories** | List Categories | `GET /api/v1/categories` | Returns `{ items: Category[] }` | **CONFIRMED** | Populates sidebar and thread editor |
| | List Threads (Paginated) | `GET /api/v1/threads?limit=20&cursor=...` | Query params: `category_id`, `tag`, `sort`, `cursor` | **CONFIRMED** | Opaque cursor pagination, returns `next_cursor` |
| | Keyword Search | `GET /api/v1/search?q=...` | Query: `q`, `category_id`, `tag`, `sort` | **CONFIRMED** | Full-text search via Search Service |
| | Search Suggestions | `GET /api/v1/search/suggestions?q=...` | Query: `q` (min 2 chars) | **CONFIRMED** | Instant typeahead suggestions |
| **Screen 04: Thread Detail & Comments** | View Thread Details | `GET /api/v1/threads/{id}` | Returns Thread schema | **CONFIRMED** | Full thread body, tags, status, author |
| | Track View Count | `POST /api/v1/threads/{id}/view` | Emits view increment | **CONFIRMED** | Anti-inflation deduplication (DEC-005) |
| | List Thread Comments | `GET /api/v1/threads/{id}/comments` | Returns `{ items: Comment[], next_cursor }` | **CONFIRMED** | Nested up to max 2 levels (parent_comment_id) |
| | Add Comment / Reply | `POST /api/v1/threads/{id}/comments` | `{ body, parent_comment_id }` | **CONFIRMED** | Requires auth & CSRF; checks thread lock |
| | Vote on Thread | `PUT /api/v1/threads/{id}/vote` | `{ value: 1 \| -1 }` | **CONFIRMED** | Disallows self-vote; returns updated score |
| | Vote on Comment | `PUT /api/v1/comments/{id}/vote` | `{ value: 1 \| -1 }` | **CONFIRMED** | Returns updated comment score |
| | Bookmark Thread | `PUT /api/v1/threads/{id}/bookmark` | None (id in path) | **CONFIRMED** | Toggles/sets bookmark |
| | Remove Bookmark | `DELETE /api/v1/threads/{id}/bookmark` | None | **CONFIRMED** | Removes bookmark |
| | Mark Best Answer | `PUT /api/v1/threads/{id}/best-answer` | `{ comment_id }` | **CONFIRMED** | Thread author only |
| | Lock/Unlock Thread | `PUT /api/v1/threads/{id}/lock` | `{ is_locked: bool, reason }` | **CONFIRMED** | Moderator/Admin only |
| | Pin/Unpin Thread | `PUT /api/v1/threads/{id}/pin` | `{ is_pinned: bool }` | **CONFIRMED** | Moderator/Admin only |
| | Delete Thread | `DELETE /api/v1/threads/{id}` | Headers: `X-CSRF-Token` | **CONFIRMED** | Conflict 409 if active comments or best answer |
| | Delete Comment | `DELETE /api/v1/comments/{id}` | Headers: `X-CSRF-Token` | **CONFIRMED** | Soft delete |
| **Screen 05: Thread Editor** | Create Thread | `POST /api/v1/threads` | `{ category_id, title, body, tags }` | **CONFIRMED** | Validates lengths (title 5-200, body 10-50000) |
| | Update Thread | `PUT /api/v1/threads/{id}` | `{ title, body, tags, category_id }` | **CONFIRMED** | Thread author only |
| | *Draft & Publish split* | UI Draft State | Backend stores `status="published"` only | **UNSUPPORTED IN API** | Local drafting supported in browser state |
| | *Image Uploads* | `POST /api/v1/uploads/images` | `multipart/form-data` | **UNSUPPORTED (501)** | Backend returns 501 Not Implemented; UI indicates state |
| **Screen 06: Moderation Queue** | List Reports | `GET /api/v1/moderation/reports` | Query: `status`, `target_type`, `limit` | **CONFIRMED** | Moderator/Admin only |
| | Get Report Dossier | `GET /api/v1/moderation/reports/{id}` | Detailed report + target info | **CONFIRMED** | Moderator/Admin only |
| | Claim/Review Report | `PATCH /api/v1/moderation/reports/{id}/review` | Sets status to reviewing | **CONFIRMED** | Moderator/Admin only |
| | Resolve Report with Action | `POST /api/v1/moderation/reports/{id}/actions` | `{ action: "pin"\|"lock"\|"soft_delete"\|"ban"\|"dismiss_report", reason }` | **CONFIRMED** | Maps UI action to backend enum |
| | List Audit Actions | `GET /api/v1/moderation/actions` | Audit log entries | **CONFIRMED** | Moderator/Admin only |
| | List Admin Users | `GET /api/v1/admin/users` | List accounts | **CONFIRMED** | Admin only |
| | Change User Role | `PATCH /api/v1/admin/users/{id}/role` | `{ role: "member"\|"moderator"\|"admin" }` | **CONFIRMED** | Admin only |
| | Ban / Unban User | `PUT /api/v1/admin/users/{id}/ban` | `{ is_banned: bool, reason }` | **CONFIRMED** | Admin/Moderator |
| **Screen 07: User Profile** | Current User Profile | `GET /api/v1/profile` | Detailed account info | **CONFIRMED** | Authenticated user |
| | Public User Profile | `GET /api/v1/users/{id}` | Public profile info | **CONFIRMED** | Any user / guest |
| | User Reputation Score | `GET /api/v1/users/{id}/reputation` | Returns `{ reputation_score: int }` | **CONFIRMED** | Aggregate score from ledger |
| | *Reputation Ledger (History)*| UI Ledger Table | No public ledger history endpoint | **UNSUPPORTED IN API** | Documented gap; displays aggregate score and state notice |
| | Submit Abuse Report | `POST /api/v1/reports` | `{ target_type: "thread"\|"comment"\|"account", target_id, reason, details }` | **CONFIRMED** | Handles 409 `DUPLICATE_REPORT` |
| | List My Reports | `GET /api/v1/reports/mine` | List user's submitted reports | **CONFIRMED** | Member/Author |
| | User Bookmarks | `GET /api/v1/profile/bookmarks` | Returns user's bookmarked threads | **CONFIRMED** | Authenticated user |
| | Notifications List | `GET /api/v1/notifications` | Unread / all notifications | **CONFIRMED** | Authenticated user |

---

## 3. Discrepancies and Gap Audit

1. **OTP Challenge Expiry: 5-minute vs. 10-minute:**
   - The UI specification illustrated a 5-minute timer.
   - The authoritative backend (`PendingRegistration.otp_expires_at` in `src/services/user/app.py:205`) configures `ttl_seconds=600` (10 minutes) and 60-second resend cooldown.
   - **Resolution:** The frontend implements the authoritative **10-minute countdown timer** with real 60s resend cooldown.
2. **Reputation History Ledger:**
   - The UI specification requested a ledger showing individual reputation transactions.
   - The backend exposes `GET /api/v1/users/{id}/reputation` returning the aggregate `reputation_score` (summed from `interactions.reputation_ledger`), but does not expose a public itemized ledger endpoint.
   - **Resolution:** The frontend displays the verified aggregate score with a status badge indicating individual transaction history is private/internal.
3. **Image Uploads Endpoint:**
   - `POST /api/v1/uploads/images` is present in Gateway routes and OpenAPI spec, but Content Service returns `501 Not Implemented`.
   - **Resolution:** The editor component provides image staging validation with clear UI messaging that cloud image storage is currently disabled on this node.
4. **Thread Deletion Conflict (ADR-008):**
   - Attempting to delete a thread that has active comments or a best answer returns `409 Conflict` with code `THREAD_REQUIRES_MODERATION`.
   - **Resolution:** The frontend catches this 409 specifically and triggers the Moderation Conflict Modal guiding the user to flag the thread for moderator assistance.
