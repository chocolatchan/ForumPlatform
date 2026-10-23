# Forum Platform — API Endpoint Reference

**Document type:** source-grounded endpoint reference

**Baseline:** repository worktree at commit `bf3384c` plus the uncommitted changes present on 2026-10-23

**Canonical public contract:** `src/docs/forum_public_api.openapi.yaml` (60 method/path operations)

**Runtime status:** not runtime-verified for this document. Statements below are derived from source code, canonical contracts, and repository tests; no container, database, or endpoint was started, rebuilt, migrated, or invoked.

---

## 1. Scope and reading guide

This reference covers every public API Gateway route, every additional public service route that is reachable through the Gateway but absent from the canonical OpenAPI document, every internal service-to-service route, and the Gateway's operational routes.

Each endpoint receives exactly one status:

| Status | Meaning in this document |
|---|---|
| `IMPLEMENTED` | A concrete handler exists, is reachable through the documented surface, and its observed source behavior matches the documented contract for the primary success path and principal failure paths. |
| `PARTIALLY_IMPLEMENTED` | A handler exists but one or more documented behaviors, filters, validations, idempotency guarantees, or cross-service effects are missing or degraded. |
| `STUB_OR_NOT_IMPLEMENTED` | No usable handler exists for the documented operation, or the operation is intentionally unimplemented. |
| `CONTRACT_SOURCE_MISMATCH` | The canonical OpenAPI/contract and the source disagree on externally observable behavior (status, authentication requirement, or response semantics). |
| `NOT_VERIFIED` | Behavior could not be established from source and tests with sufficient confidence; this document does not use it as a substitute for runtime testing. |

### Mandatory fields A–F

Every endpoint row records:

- **A — Identity and routing:** HTTP method, public path, owning service, OpenAPI presence.
- **B — Authentication, RBAC, CSRF:** whether a session is required, which roles are accepted, and whether `X-CSRF-Token` is enforced.
- **C — Request:** path/query parameters, headers, body schema, and validation constraints.
- **D — Response:** success status/body schema and the principal error statuses/codes.
- **E — Processing and data effects:** validation order, database transaction scope, tables read/written, and concurrency controls.
- **F — Events, async and failures:** outbox events, synchronous cross-service calls, retries, compensation, and known failure modes.

### Evidence notes

- A handler existing is not proof that its OpenAPI description is complete. Where the OpenAPI operation and the handler differ, the row is marked `CONTRACT_SOURCE_MISMATCH` or `PARTIALLY_IMPLEMENTED`.
- Tests under `tests/` are cited as repository evidence only. They were read, not executed for this task.
- Audit reports under `docs/audit/` were not modified and are not treated as the primary source of truth.

---

## 2. Gateway surface and routing

### 2.1 Gateway request pipeline

1. The Gateway accepts `/api/v1/{path}` for `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `OPTIONS`, and `HEAD`.
2. `GatewayRouter.resolve()` strips leading slashes and applies the first matching prefix rule in the order below.
3. If no rule matches, the Gateway returns `404 ROUTE_NOT_FOUND` in the canonical error envelope.
4. If the shared HTTP client is not initialized, it returns `503 GATEWAY_UNAVAILABLE`.
5. The forwarder removes hop-by-hop headers and all client-supplied identity/internal headers:
   `X-Authenticated-User-ID`, `X-Authenticated-Role`, `X-User-ID`, `X-Role`, `X-Forwarded-User-ID`, `X-Forwarded-Role`, `X-Forwarded-Username`, `X-Internal-Service-Key`.
6. The forwarder streams the request body with a cumulative 5 MB limit. A declared oversized `Content-Length` is rejected before reading; a lying small `Content-Length` does not bypass the streamed limit.
7. Upstream responses are streamed back. `Set-Cookie` is preserved explicitly; responses carry `X-Request-ID`.
8. Gateway failure mapping: `413 PAYLOAD_TOO_LARGE`, `499 CLIENT_DISCONNECTED`, `503 UPSTREAM_UNAVAILABLE`, `504 UPSTREAM_TIMEOUT`, `502` for unexpected forwarding errors.

### 2.2 Route precedence

| Priority | Path pattern | Target service | Notes |
|---|---|---|---|
| 1 | `profile/bookmarks` | Interaction | Must precede `/profile` and `/users` user rules. |
| 1 | `users/{id}/reputation` | Interaction | Must precede `/users`. |
| 1 | `threads/{id}/vote` | Interaction | Must precede `/threads`. |
| 1 | `threads/{id}/bookmark` | Interaction | Must precede `/threads`. |
| 1 | `threads/{id}/view` | Interaction | Must precede `/threads`. |
| 1 | `comments/{id}/vote` | Interaction | Must precede `/comments`. |
| 2 | `auth`, `profile`, `admin/users`, `users` | User | `/users/{id}` public profile lives here. |
| 3 | `categories`, `admin/categories`, `threads`, `comments`, `uploads` | Content | Content owns thread/comment/upload CRUD. |
| 4 | `reports`, `moderation` | Moderation | Report creation and moderation queue. |
| 5 | `notifications` | Notification | Inbox and preferences. |
| 6 | `search` | Search | Search and suggestions. |
| — | anything else | none | `404 ROUTE_NOT_FOUND`. |

### 2.3 Gateway operational routes

| ID | Method & path | Status | B Auth | D Response | E/F Notes |
|---|---|---|---|---|---|
| GW-01 | `GET /health/live` | `IMPLEMENTED` | Public | `200 {"status":"ok"}` | Process liveness only. |
| GW-02 | `GET /health/ready` | `IMPLEMENTED` | Public | `200 {"status":"ready"}` or `503 {"status":"degraded","client":"uninitialized"}` | Checks Gateway HTTP client initialization, not downstream health. |
| GW-03 | `GET /docs` | `IMPLEMENTED` | Public | Swagger UI HTML | Served by FastAPI. |
| GW-04 | `GET /redoc` | `IMPLEMENTED` | Public | ReDoc HTML | Served by FastAPI. |
| GW-05 | `GET /openapi.json` | `IMPLEMENTED` | Public | Canonical OpenAPI JSON | Replaced by `load_canonical_openapi()`; falls back to a minimal document if the YAML is missing. |
| GW-06 | `GET /metrics` | `IMPLEMENTED` | Public | Prometheus text | Added by `setup_metrics_endpoint`; excluded from OpenAPI schema. |
| GW-07 | `GET /` and `/static/*` | `IMPLEMENTED` | Public | Frontend index or static file | Mounted only when the `frontend/` directory exists. |
| GW-08 | `/api/v1/{path}` catch-all | `IMPLEMENTED` | Depends on target | Proxied upstream response | See pipeline above; unknown prefixes return `404 ROUTE_NOT_FOUND`. |

---

## 3. Common conventions

### 3.1 Session authentication

- Public session scheme: opaque cookie `forum_session` (OpenAPI `sessionCookie`, `apiKey` in `cookie`).
- Sessions are stored server-side in `forum_users.sessions`; the cookie value is hashed before lookup.
- Idle timeout: 4 hours, extended on activity at most once per 60 seconds. Absolute timeout: 24 hours.
- Revoked, expired, idle-expired, or banned-account sessions yield `401`/`403` from User Service.
- Content, Interaction, Moderation, Notification, and Search validate the cookie by calling User Service `GET /auth/session` (synchronous internal HTTP). A User Service outage produces `503` on the protected downstream endpoint.
- Trusted `X-Forwarded-User-ID` / `X-Forwarded-Role` headers are honored by downstream services in code, but the Gateway strips them from client requests, so they are not a public bypass. Direct service access outside the Gateway is an infrastructure concern.

### 3.2 CSRF

- Synchronizer token is returned by `GET /api/v1/auth/csrf` as `{"csrf_token": "..."}`.
- Mutation endpoints protected by cookie session expect `X-CSRF-Token`.
- User, Content, Interaction, Moderation, and Notification `verify_csrf_header` skip verification when no `forum_session` cookie is present. This is intentional for unauthenticated callers that later fail authentication, but it means CSRF alone is not an authentication control.
- Search `verify_csrf_header` always requires a valid token, including on `POST /admin/reindex`.
- Internal `X-Internal-Service-Key` endpoints do not use CSRF.

### 3.3 Roles

`member`, `moderator`, `admin`. Ownership checks are separate from role checks. Notable role behavior:

- Admin: user role changes, category creation/update, search reindex.
- Admin or moderator: thread lock/pin, moderation queue/actions, account ban/unban, proactive moderation.
- Member: create threads/comments, vote, bookmark, report.
- Resource owner: edit/delete own thread/comment, select best answer, edit own profile.

### 3.4 Error envelope

All services register canonical handlers:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request schema validation failed",
    "details": [{"field": "title", "reason": "..."}],
    "request_id": "client-supplied-or-generated"
  }
}
```

`HTTPException` is mapped to generic codes such as `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`, `RATE_LIMITED`. Handlers that call `error_response()` supply domain-specific codes such as `THREAD_LOCKED`, `DUPLICATE_REPORT`, or `OTP_EXPIRED`.

### 3.5 Pagination and cursors

Most collections use opaque base64 cursors and `limit` (default 20, max 50). Cursor encoding differs by service:

- Content, Interaction, Moderation, Search: base64 of an ISO timestamp; ordering generally `created_at DESC` (comments ascending).
- Notification: base64 of `(created_at, id)` for deterministic `(created_at DESC, id DESC)` ordering.
- `GET /admin/users` accepts `limit` and `cursor` but always returns `next_cursor: null` and ignores the cursor.
- Search `most_viewed` and `newest` cursor on `source_created_at`; relevance cursor on `indexed_at`.

### 3.6 Idempotency (contract vs source)

The canonical OpenAPI declares `Idempotency-Key` for:

- `POST /threads`
- `POST /threads/{thread_id}/comments`
- `POST /reports`

Source reality:

- Content thread/comment handlers do not read or persist the header. Repeating a request creates another row/event.
- Moderation `POST /reports` declares `idempotency_key` as a parameter but never uses it.
- No handler implements `409 IDEMPOTENCY_KEY_REUSED`.
- Duplicate-report protection exists independently: one active (`pending`/`reviewing`) report per reporter/target pair.
- Duplicate bookmark creation is made idempotent by a uniqueness check/constraint, not by `Idempotency-Key`.

This is a contract/source gap documented per endpoint and in the workflow guide.

### 3.7 Internal service authentication

- Header: `X-Internal-Service-Key`.
- `verify_internal_service_request` compares with `hmac.compare_digest`.
- Missing header is accepted only when `internal_service_auth_bypass` is explicitly enabled in development/test. Staging/production configuration rejects bypass.
- A supplied wrong key is always rejected, even when bypass is enabled.
- Outbox workers require a non-empty internal key at construction; there is no default.

---

## 4. Canonical schemas

### 4.1 Shared envelopes

| Schema | Fields |
|---|---|
| `Error` | `error.code`, `error.message`, `error.details[]` (`field`, `reason`), `error.request_id` |
| `CategoryListResponse` | `items[]` of `CategoryResponse` |
| `ThreadPageResponse` | `items[]` of `ThreadResponse`, `next_cursor` |
| `CommentPageResponse` | `items[]` of `CommentResponse`, `next_cursor` |
| `NotificationPageResponse` | `items[]` of `NotificationItemResponse`, `next_cursor` |
| `SearchPage` | `items[]` of `SearchItem`, `next_cursor` |

### 4.2 User schemas

| Schema | Fields and constraints |
|---|---|
| `RegisterRequest` | `username` `^[A-Za-z0-9_]{3,32}$`; `email` max 254; `password` min 8. Username/email are trimmed and lowercased for uniqueness. |
| `VerifyOtpRequest` | `email`; `otp` exactly 6 digits. |
| `ResendOtpRequest` | `email`. |
| `LoginRequest` | `login` (username or email); `password`. |
| `RecoveryRequest` | `email`. |
| `RecoveryVerifyRequest` | `email`; `otp` 6 digits. |
| `ResetPasswordRequest` | `reset_token`; `new_password` min 8. |
| `UserPublic` | `id`, `username`, `display_name`, `bio`, `role`. |
| `UserProfileOwn` | adds `email`, `created_at`, `avatar_url`. |
| `UserProfilePublic` | `id`, `username`, `display_name`, `bio`, `role`, `avatar_url`. |
| `UserAdminView` | `id`, `username`, `display_name`, `email`, `role`, `is_banned`, `ban_reason`, `created_at`. |
| `SessionMetadata` | `idle_expires_at`, `absolute_expires_at`. |
| `LoginResponse` | `user` (`UserPublic`), `session` (`SessionMetadata`). |
| `UpdateProfileRequest` | `display_name` max 50; `bio` max 200. |
| `SetAvatarRequest` | `image_id` UUID. |
| `ChangeEmailRequest` | `new_email`; `password` (re-authentication). |
| `ChangeEmailVerifyRequest` | `otp` 6 digits. |
| `ChangePasswordRequest` | `current_password` (alias `old_password`); `new_password` min 8. |
| `ChangeUserRoleRequest` | `role` in `member|moderator|admin`. |
| `BanUserRequest` | `is_banned` bool; `ban_reason` optional. |
| `RegistrationPendingResponse` | `status`, `message`. |
| `NeutralAcceptedResponse` | `status`, `message`. |
| `VerifyOtpResponse` | `user` (`UserPublic`). |

### 4.3 Content schemas

| Schema | Fields and constraints |
|---|---|
| `CreateCategoryRequest` | `name` 1–80; `slug` 1–100; `description`; `sort_order` default 0. |
| `UpdateCategoryRequest` | optional `name`, `slug`, `description`, `sort_order`, `is_hidden`. |
| `CategoryResponse` | `id`, `name`, `slug`, `description`. |
| `CreateThreadRequest` | `category_id` UUID; `title` 10–150; `body` min 20; `tags` max 5; `image_ids[]`. |
| `EditThreadRequest` | optional `title` 10–150, `body` min 20, `tags` max 5. |
| `ThreadResponse` | `id`, `author_id`, `category_id`, `title`, `body`, `tags[]`, `status` (`draft|published|hidden|deleted`), `is_pinned`, `is_locked`, `best_answer_comment_id`, `view_count`, `created_at`, `updated_at`. |
| `LockThreadRequest` | `is_locked` bool. |
| `PinThreadRequest` | `is_pinned` bool. |
| `BestAnswerRequest` | `comment_id` UUID. |
| `CreateCommentRequest` | `body` min 1; `parent_comment_id` optional UUID. |
| `EditCommentRequest` | `body` min 1. |
| `CommentResponse` | `id`, `thread_id`, `author_id`, `parent_comment_id`, `body`, `is_deleted`, `created_at`. Deleted comments serialize body as `[deleted]`. |
| `AttachmentResponse` | `id`, `object_key`, `file_size`, `mime_type`, `status`, `created_at`. |

### 4.4 Interaction, moderation, notification, search schemas

| Schema | Fields and constraints |
|---|---|
| `VoteRequest` | `value` must be `1` or `-1`. |
| `VoteResponse` | `target_type` (`thread|comment`), `target_id`, `my_vote` (`1|-1|null`), `score`, `reputation_delta`. |
| `BookmarkResponse` | `bookmarked`. |
| `ReputationResponse` | `user_id`, `reputation_score`. |
| `CreateReportRequest` | `target_type` (`thread|comment|account`), `target_id`, `reason` (`spam|abuse|harassment|wrong_category|misinformation|other`), `details` max 1000. |
| `ReportResponse` | `id`, `target_type`, `target_id`, `reason`, `details`, `status`, `resolution_note`, `created_at`. |
| `ModerationActionRequest` | `action` (`pin|lock|move_category|soft_delete|ban|unban|role_change|dismiss_report`), `reason` min 1, `metadata` object. |
| `ProactiveModerationActionRequest` | same actions except `dismiss_report`, plus `target_type`, `target_id`. |
| `ModerationActionResponse` | `id`, `report_id`, `moderator_id`, `action`, `target_type`, `target_id`, `reason`, `created_at`. |
| `NotificationPreferencesRequest` | optional `in_app_enabled`, `email_enabled`. |
| `NotificationPreferencesResponse` | `in_app_enabled`, `email_enabled`. |
| `NotificationItemResponse` | `id`, `type`, `title`, `body`, `target_type`, `target_id`, `is_read`, `created_at`. |
| `UnreadCountResponse` | `unread_count`. |
| `SearchItem` | `thread_id`, `title`, `snippet`, `category_id`, `author_id`, `created_at`. |
| `SearchSuggestionsResponse` | `suggestions[]`. |

---

## 5. Coverage summary

| Surface | Count | Status distribution |
|---|---:|---|
| Canonical OpenAPI operations | 60 | 48 `IMPLEMENTED`, 9 `PARTIALLY_IMPLEMENTED`, 3 `CONTRACT_SOURCE_MISMATCH` |
| Additional public Gateway-reachable routes | 5 | 5 `IMPLEMENTED` |
| Internal service routes | 6 | 6 `IMPLEMENTED` |
| Gateway operational routes | 8 | 8 `IMPLEMENTED` |
| **Total documented** | **79** | 67 `IMPLEMENTED`, 9 `PARTIALLY_IMPLEMENTED`, 3 `CONTRACT_SOURCE_MISMATCH` |

The exact per-endpoint status is authoritative in the tables below. The summary counts are not a substitute for the rows.

---

## 6. User Service endpoints

Owning service: `src/services/user/app.py`

Database: `forum_users` — `accounts`, `pending_registrations`, `one_time_tokens`, `sessions`, `password_history`, `email_change_requests`, `outbox_events`.

Base public prefix: `/auth`, `/profile`, `/users`, `/admin/users`.

### 6.1 User Service inventory

| ID | A — Method & path | Status | B — Auth / RBAC / CSRF | C — Request | D — Response | E — Processing & DB | F — Events / async / failures |
|---|---|---|---|---|---|---|---|
| U-01 | `GET /auth/csrf` | `IMPLEMENTED` | Public; no CSRF | None | `200 {"csrf_token": "..."}` | Signs a CSRF token with the configured secret. No DB write. | Token is not session-bound; verification is HMAC-based. |
| U-02 | `POST /auth/register` | `IMPLEMENTED` | Public; no CSRF | `RegisterRequest` | `202` neutral `{status:"pending_verification", message}` | 1 rate-limit by client IP; 2 normalize username/email; 3 existing account returns neutral 202; 4 lock/select pending row; 5 hash password/OTP, upsert `pending_registrations`; 6 commit; 7 deliver OTP outside transaction. | OTP delivery failure marks pending `delivery_failed`; response stays neutral 202. No outbox event until verification. |
| U-03 | `POST /auth/register/verify` | `IMPLEMENTED` | Public; no CSRF | `VerifyOtpRequest` | `201 {"user": UserPublic}`; `400 INVALID_OTP`, `OTP_EXPIRED`, `OTP_ATTEMPTS_EXCEEDED`; `409 ACCOUNT_ALREADY_EXISTS`; `429 RATE_LIMITED` | 1 rate-limit; 2 lock pending row `FOR UPDATE`; 3 check attempts ≤5 and expiry; 4 verify OTP hash; 5 reject duplicate account; 6 insert `accounts`, mark pending `consumed`, insert `UserRegistered` outbox in same transaction; 7 flush. | `UserRegistered` outbox consumed asynchronously by Notification. `409` is not listed in OpenAPI for this operation. |
| U-04 | `POST /auth/register/resend-otp` | `IMPLEMENTED` | Public; no CSRF | `{email}` | `202` neutral `{status:"accepted", message}` | 1 rate-limit; 2 lock pending row; 3 require resend_count<3, 60 s cooldown, not expired; 4 rotate OTP, increment count; 5 deliver outside transaction. | Delivery failure marks `delivery_failed`; always neutral 202. |
| U-05 | `POST /auth/login` | `IMPLEMENTED` | Public; no CSRF | `LoginRequest` | `200 LoginResponse` + `Set-Cookie forum_session`; `401 INVALID_CREDENTIALS`; `403 ACCOUNT_BANNED`; `429 RATE_LIMITED` | 1 rate-limit by IP; 2 normalize login; 3 select account by username/email; 4 verify Argon2id password; 5 reject banned; 6 insert `sessions` with token hash and idle/absolute expiry; 7 set HttpOnly/Lax cookie. | Error code in source is `ACCOUNT_BANNED`, while the contract catalog lists `ACCOUNT_RESTRICTED`; OpenAPI does not enumerate codes. No event. |
| U-06 | `POST /auth/logout` | `IMPLEMENTED` | Session required; CSRF required when cookie present | None | `204` + cookie deletion | 1 authenticate; 2 set `revoked_at`, `revocation_reason="user_logout"`; 3 delete cookie. | Idempotent from the caller's perspective; second call fails authentication before revocation. |
| U-07 | `GET /auth/session` | `IMPLEMENTED` | Session required; no CSRF | None | `200 {user, session}` | Validates token hash, revocation, absolute/idle expiry, ban status; optionally refreshes `last_seen_at`/`idle_expires_at` if older than 60 s. | Used synchronously by all downstream services for authentication. |
| U-08 | `GET /profile` | `IMPLEMENTED` | Session required; no CSRF | None | `200 UserProfileOwn` | Reads `accounts`; derives `avatar_url` as `/uploads/images/{avatar_object_key}`. | Avatar URL points at Content Service; no synchronous call here. |
| U-09 | `PATCH /profile` | `IMPLEMENTED` | Session; CSRF | `UpdateProfileRequest` | `200 UserProfileOwn` | 1 authenticate; 2 lock account `FOR UPDATE`; 3 update only provided `display_name`/`bio`; 4 commit. | Empty body is accepted and changes nothing. No event. |
| U-10 | `PUT /profile/avatar` | `IMPLEMENTED` | Session; CSRF | `SetAvatarRequest` | `200 UserProfileOwn`; `404 ATTACHMENT_NOT_FOUND`, `403 FORBIDDEN_ATTACHMENT_CLAIM`, `409 INVALID_ATTACHMENT_STATUS`, `502/500` on Content failure | 1 authenticate; 2 synchronous HTTP `POST {content}/internal/attachments/{id}/claim-avatar` with internal key and `user_id`; 3 on success lock account and set `avatar_object_key`; 4 best-effort release old avatar in Content. | Cross-service operation is not atomic: Content may claim before the User update fails, or the User update may commit before old-avatar release fails. Old-avatar release failure is only logged. |
| U-11 | `DELETE /profile/avatar` | `IMPLEMENTED` | Session; CSRF | None | `204` | 1 authenticate; 2 lock account, clear `avatar_object_key`; 3 commit; 4 best-effort synchronous `release-avatar`. | Release failure is logged and not retried; Content cleanup/reconciliation is the fallback. |
| U-12 | `POST /profile/change-email/request` | `IMPLEMENTED` | Session; CSRF | `ChangeEmailRequest` | `202` neutral; `400 INVALID_PASSWORD/EMAIL_UNCHANGED`; `422 VALIDATION_ERROR`; `429 RATE_LIMIT_EXCEEDED` | 1 rate-limit; 2 verify current password; 3 format-check new email; 4 reject unchanged; 5 check existing account and return neutral 202 if taken; 6 lock account; 7 expire previous requests; 8 insert `email_change_requests` with OTP; 9 deliver OTP outside transaction. | Delivery failure marks request completed. Anti-enumeration hides taken-email outcome. |
| U-13 | `POST /profile/change-email/resend-otp` | `IMPLEMENTED` | Session; CSRF | Empty body | `200 {status:"sent"}`; `400 NO_ACTIVE_EMAIL_CHANGE`, `RESEND_LIMIT_EXCEEDED`, `REQUEST_EXPIRED`; `429 RESEND_COOLDOWN_ACTIVE` | 1 rate-limit; 2 lock account and active request; 3 enforce 60 s cooldown, max 3 resends, expiry; 4 rotate OTP; 5 deliver. | Delivery failure is logged; OTP state remains rotated. |
| U-14 | `POST /profile/change-email/verify` | `IMPLEMENTED` | Session; CSRF | `ChangeEmailVerifyRequest` | `200 UserProfileOwn`; `400 NO_ACTIVE_EMAIL_CHANGE/OTP_EXPIRED/OTP_ATTEMPTS_EXCEEDED/INVALID_OTP`; `409 EMAIL_ALREADY_IN_USE`; `429 RATE_LIMIT_EXCEEDED` | 1 rate-limit; 2 lock account then active request; 3 check expiry/attempts/OTP; 4 re-check email uniqueness; 5 update `accounts.email/email_normalized/email_verified_at`; 6 mark request verified/completed; 7 rely on unique constraint on concurrent conflict. | No outbox event. IntegrityError maps to 409. |
| U-15 | `POST /auth/recovery/request` | `IMPLEMENTED` | Public; no CSRF | `RecoveryRequest` (`email`) | `202 NeutralAccepted`; `429 RATE_LIMIT_EXCEEDED` | 1 IP rate-limit; 2 normalize email; 3 look up non-banned account; 4 for an eligible account lock account `FOR UPDATE`, find the newest active `password_recovery` token, enforce 60 s cooldown and max 3 resends, expire older OTP tokens, insert a 15-minute OTP; 5 deliver OTP outside transaction; 6 on delivery failure mark the token consumed. | Always neutral `202`, including unknown, banned, cooldown, or resend-limit cases. No outbox event; delivery failure is swallowed and the token invalidated. `429` is not listed in OpenAPI. |
| U-16 | `POST /auth/recovery/verify` | `IMPLEMENTED` | Public; no CSRF | `RecoveryVerifyRequest` (`email`, `otp` 6 digits) | `200 {reset_token, expires_at}`; `400 INVALID_OTP/OTP_EXPIRED/OTP_ATTEMPTS_EXCEEDED`; `429 RATE_LIMIT_EXCEEDED`; `422 VALIDATION_ERROR` | 1 IP rate-limit; 2 lock account `FOR UPDATE`; 3 lock the newest active `password_recovery` token; 4 reject missing/expired/max-attempts challenges; 5 verify OTP constant-time and increment attempts; 6 consume OTP, expire prior `password_reset_ticket` rows, insert a 10-minute single-use ticket storing only its hash. | Reset token is returned once; only its hash is stored. No outbox event. `429` is not listed in OpenAPI. |
| U-17 | `POST /auth/recovery/reset` | `IMPLEMENTED` | Public; no CSRF | `ResetPasswordRequest` (`reset_token`, `new_password` min 8) | `204`; `400 INVALID_RESET_TOKEN/RESET_TOKEN_EXPIRED/PASSWORD_REUSE_FORBIDDEN`; `429 RATE_LIMIT_EXCEEDED`; `422 VALIDATION_ERROR` | 1 IP rate-limit; 2 hash reset token and pre-read ticket owner; 3 lock account then ticket; 4 reject missing/expired tickets; 5 reject the current password and the last 5 `password_history` hashes; 6 consume ticket, append old hash to `password_history`, update password/`password_changed_at`; 7 revoke all active sessions with reason `password_reset`. | No outbox event. Ticket is single-use; all sessions are revoked. `429` is not listed in OpenAPI. |
| U-18 | `POST /profile/change-password` | `IMPLEMENTED` | Session; CSRF | `ChangePasswordRequest` | `204`; `400 INVALID_PASSWORD/PASSWORD_REUSE_FORBIDDEN` | 1 verify current password; 2 reject same password; 3 lock account; 4 append old hash to `password_history`; 5 update password + `password_changed_at`; 6 revoke all other sessions with reason `password_changed`. | Current session remains valid. No event. |
| U-19 | `GET /users/{user_id}` | `IMPLEMENTED` | Public; no CSRF | Path UUID | `200 UserProfilePublic`; `404 USER_NOT_FOUND` | Reads `accounts`; excludes email and ban details. | Used by Moderation to verify account reports and by UI for public profiles. |
| U-20 | `GET /admin/users` | `PARTIALLY_IMPLEMENTED` | Session; role `admin`; no CSRF | Query: `search`, `role`, `limit` 1–50, `cursor` per OpenAPI. Source only reads `limit` and `cursor`. | `200 {items: UserAdminView[], next_cursor}` | Reads `accounts`, orders by `created_at DESC`, limits `limit`; always returns `next_cursor: null`. | OpenAPI `search` and `role` filters are ignored; cursor pagination is not implemented. |
| U-21 | `PATCH /admin/users/{user_id}/role` | `IMPLEMENTED` | Session; role `admin`; CSRF | `ChangeUserRoleRequest` | `200 UserAdminView`; `404 USER_NOT_FOUND` | 1 authenticate/admin; 2 select account; 3 update role; 4 revoke all active sessions with reason `role_change`; 5 commit. | Immediate role invalidation via session revocation. No outbox event. |
| U-22 | `PUT /admin/users/{user_id}/ban` | `IMPLEMENTED` | Session; roles `admin` or `moderator`; CSRF | `BanUserRequest` | `200 UserAdminView`; `404 USER_NOT_FOUND` | 1 authenticate/role; 2 select account; 3 set `is_banned` and `ban_reason`; 4 if banning, revoke active sessions with reason `account_banned`; 5 commit. | Extra public route absent from OpenAPI. Used by Moderation `ban`/`unban` remote enforcement. |
| U-23 | `GET /internal/users/{user_id}` | `IMPLEMENTED` | Internal key required; no session/CSRF | Path UUID | `200 {id, username, display_name, email, role, is_banned}`; `404 USER_NOT_FOUND` | Reads `accounts`. | Used synchronously by Notification to resolve recipient email. |
| U-24 | `GET /test/otp` | `IMPLEMENTED` | Public only in development/test when test endpoints are enabled | Query `recipient` | `200 {"code": "..."}`; `404 Not available` | Reads the OTP delivery sink. | Test-only; not reachable through the Gateway (`/test` has no route). Never usable outside dev/test. |
| U-25 | `POST /test/reset-rate-limiters` | `IMPLEMENTED` | Public only in development/test when test endpoints are enabled | None | `200 {"status":"reset"}` | Resets in-memory rate limiters. | Test-only; not reachable through the Gateway. |

> **User Service status note:** U-20 is the only OpenAPI operation in this service that is materially incomplete. U-05, U-10/U-11, and U-02/U-04 have documented behavioral caveats but fully implement their primary path. U-15–U-17 implement the documented recovery flow; their `429` responses are not enumerated in OpenAPI.

---

## 7. Content Service endpoints

Owning service: `src/services/content/app.py`

Database: `forum_content` — `categories`, `threads`, `thread_tags`, `comments`, `attachments`, `outbox_events`, `consumed_events`.

Local storage: image bytes under `storage_local_dir`; metadata in `attachments`.

### 7.1 Content Service inventory

| ID | A — Method & path | Status | B — Auth / RBAC / CSRF | C — Request | D — Response | E — Processing & DB | F — Events / async / failures |
|---|---|---|---|---|---|---|---|
| C-01 | `GET /categories` | `IMPLEMENTED` | Public; no CSRF | None | `200 CategoryListResponse` | Selects non-hidden categories ordered by `sort_order`, then `name`. | No event. Hidden categories are omitted. |
| C-02 | `GET /categories/{category_id}` | `IMPLEMENTED` | Public; no CSRF | Path UUID | `200 CategoryResponse`; `404 CATEGORY_NOT_FOUND` | Selects by ID, including hidden categories. | Hidden-category detail remains readable if the ID is known. |
| C-03 | `POST /admin/categories` | `IMPLEMENTED` | Session; role `admin`; CSRF | `CreateCategoryRequest` | `201 CategoryResponse`; `409 CATEGORY_SLUG_CONFLICT` | 1 authenticate/admin; 2 normalize slug to lowercase/trim; 3 check slug uniqueness; 4 insert category with `created_by`. | No outbox event. Unique constraint is the final concurrency guard. |
| C-04 | `PATCH /admin/categories/{category_id}` | `IMPLEMENTED` | Session; role `admin`; CSRF | `UpdateCategoryRequest` | `200 CategoryResponse`; `404 CATEGORY_NOT_FOUND`; `409 CATEGORY_SLUG_CONFLICT` | 1 authenticate/admin; 2 select category; 3 if slug changes, check conflict; 4 apply provided fields including `is_hidden`; 5 commit. | No event; search documents are not updated for category renames. |
| C-05 | `GET /threads` | `PARTIALLY_IMPLEMENTED` | Public; no CSRF | Query: `category_id`, `tag`, `sort` (`newest|most_viewed|most_voted`), `limit` 1–50, `cursor` | `200 ThreadPageResponse` | Selects `status="published"`; applies category/tag filters; supports `newest` and `most_viewed` ordering; uses `created_at` cursor. | `sort=most_voted` is accepted by the schema but falls through to the default newest ordering; there is no vote-count join. Cursor is timestamp-only, so equal timestamps can be skipped/duplicated. |
| C-06 | `POST /threads` | `PARTIALLY_IMPLEMENTED` | Session (any role); CSRF | `CreateThreadRequest` + optional `Idempotency-Key` | `201 ThreadResponse`; `404 CATEGORY_NOT_FOUND`; `400 INVALID_ATTACHMENT`; `422` | 1 authenticate; 2 verify visible category; 3 sort and lock pending owned attachments `FOR UPDATE`; 4 insert `threads`; 5 insert normalized/deduplicated `thread_tags`; 6 mark attachments `linked` + `linked_at`; 7 insert `ThreadCreated` outbox; 8 commit. | `Idempotency-Key` is accepted by OpenAPI but never read/persisted; retries create duplicate threads. `ThreadCreated` payload has no `body`, so Search's `ThreadCreated` handler indexes an empty body. |
| C-07 | `GET /threads/{thread_id}` | `IMPLEMENTED` | Public; no CSRF | Path UUID | `200 ThreadResponse`; `404 THREAD_NOT_FOUND` | Selects thread + tags; returns 404 for `deleted` or `hidden`. | Used synchronously by Interaction and Moderation to verify targets. |
| C-08 | `PATCH /threads/{thread_id}` | `IMPLEMENTED` | Session; owner, `admin`, or `moderator`; CSRF | `EditThreadRequest` | `200 ThreadResponse`; `404 THREAD_NOT_FOUND`; `403 FORBIDDEN_THREAD_EDIT` | 1 authenticate; 2 select thread; 3 404 if deleted; 4 owner or moderator check; 5 update title/body; 6 replace tags if provided; 7 set `edited_at`; 8 insert `ThreadUpdated` outbox. | Tags are deleted/recreated. Event lacks `body`, `category_id`, and `tags`, so Search's update handler only updates title/status. `category_id` is not part of the schema and is ignored. |
| C-09 | `DELETE /threads/{thread_id}` | `IMPLEMENTED` | Session; owner or moderator/admin; CSRF | Path UUID | `204`; `404 THREAD_NOT_FOUND`; `403 FORBIDDEN_THREAD_DELETE`; `409 THREAD_REQUIRES_MODERATION` | 1 authenticate; 2 select thread; 3 owner/mod check; 4 if owner and not mod: reject if best answer exists or non-deleted comments exist; 5 set `status="deleted"`; 6 insert `ThreadDeleted` outbox. | Soft delete. Moderator bypasses ADR-008 restrictions. Search consumes `ThreadDeleted` by setting `is_visible=False`. |
| C-10 | `PUT /threads/{thread_id}/lock` | `IMPLEMENTED` | Session; roles `admin|moderator`; CSRF | `LockThreadRequest` | `200 ThreadResponse`; `404 THREAD_NOT_FOUND` | 1 authenticate/role; 2 select thread (404 if deleted); 3 set `is_locked`; 4 insert `ThreadLockToggled` outbox. | Search consumes the event but only bumps version/source time; visibility is unchanged. Locking blocks new comments via C-14. |
| C-11 | `PUT /threads/{thread_id}/pin` | `IMPLEMENTED` | Session; roles `admin|moderator`; CSRF | `PinThreadRequest` | `200 ThreadResponse`; `404 THREAD_NOT_FOUND` | 1 authenticate/role; 2 select thread (404 if deleted); 3 set `is_pinned`; 4 insert `ThreadPinToggled` outbox. | Search consumes the event and bumps version; pin status is not stored in search documents. |
| C-12 | `PUT /threads/{thread_id}/best-answer` | `PARTIALLY_IMPLEMENTED` | Session; thread owner; CSRF | `BestAnswerRequest` | `200 ThreadResponse`; `404 THREAD_NOT_FOUND`; `403 FORBIDDEN_BEST_ANSWER`; `400 INVALID_COMMENT` | 1 authenticate; 2 select thread; 3 owner check; 4 verify comment belongs to thread and is not deleted; 5 set `best_answer_comment_id`; 6 insert `BestAnswerChanged` outbox. | Does not enforce the ADR rule that the thread's category must be a Q&A category or that the thread is unlocked. Emits `BestAnswerChanged`; Notification consumes it, Interaction does not (despite the contract listing Interaction as a consumer). |
| C-13 | `GET /threads/{thread_id}/comments` | `IMPLEMENTED` | Public; no CSRF | Query `limit` 1–50, `cursor` | `200 CommentPageResponse`; `404 THREAD_NOT_FOUND` | Verifies thread exists and is not deleted/hidden; selects comments ascending by `created_at`; includes deleted comments with `[deleted]` body. | Timestamp-only cursor; equal timestamps can be skipped/duplicated. No event. |
| C-14 | `POST /threads/{thread_id}/comments` | `PARTIALLY_IMPLEMENTED` | Session; CSRF | `CreateCommentRequest` + optional `Idempotency-Key` | `201 CommentResponse`; `404 THREAD_NOT_FOUND/PARENT_COMMENT_NOT_FOUND`; `409 THREAD_LOCKED`; `400 CROSS_THREAD_PARENT/COMMENT_DEPTH_EXCEEDED` | 1 authenticate; 2 verify thread not deleted/hidden; 3 reject locked; 4 validate parent exists, same thread, depth ≤2; 5 insert `comments`; 6 insert `CommentCreated` outbox. | `Idempotency-Key` ignored. `CommentCreated` payload lacks `recipient_id`/`thread_author_id`, so Notification's handler returns no notification (workflow gap). |
| C-15 | `PATCH /comments/{comment_id}` | `IMPLEMENTED` | Session; owner, `admin`, or `moderator`; CSRF | `EditCommentRequest` | `200 CommentResponse`; `404 COMMENT_NOT_FOUND`; `403 FORBIDDEN_COMMENT_EDIT` | 1 authenticate; 2 select non-deleted comment; 3 owner/mod check; 4 update body + `edited_at`; 5 insert `CommentUpdated` outbox. | `CommentUpdated` is not in the outbox routing table, so it is marked delivered with no consumer. |
| C-16 | `DELETE /comments/{comment_id}` | `IMPLEMENTED` | Session; owner or `admin`/`moderator`; CSRF | Path UUID | `204`; `404 COMMENT_NOT_FOUND`; `403 FORBIDDEN_COMMENT_DELETE` | 1 authenticate; 2 select non-deleted comment; 3 owner/mod check; 4 set `is_deleted=True`; 5 insert `CommentDeleted` outbox. | `CommentDeleted` is not in the routing table; no Search cleanup. Thread `best_answer_comment_id` is not cleared if the deleted comment was the best answer. |
| C-17 | `POST /uploads/images` | `IMPLEMENTED` | Session; CSRF | `multipart/form-data` field `file` | `201 AttachmentResponse`; `415 UNSUPPORTED_MEDIA_TYPE`; `413 PAYLOAD_TOO_LARGE`; `500 STORAGE_ERROR/DATABASE_ERROR` | 1 authenticate; 2 read 512-byte header and sniff magic bytes; 3 stream remainder with 2 MB cap; 4 decode/normalize/strip metadata; 5 fsync file to local storage; 6 insert `attachments` row with `status="pending"`; 7 on DB failure unlink file. | No outbox event. Orphaned-file reconciliation and pending-attachment cleanup run as background loops in the Content service. |
| C-18 | `GET /uploads/images/{image_id}` | `IMPLEMENTED` | Optional session; no CSRF | Path UUID | `200` image bytes with `X-Content-Type-Options: nosniff`; `404 ATTACHMENT_NOT_FOUND/ATTACHMENT_FILE_MISSING` | 1 optional authenticate; 2 select attachment + thread; 3 visibility policy: `avatar` public; `pending` author-only; `linked` requires thread not deleted, hidden only author/mod/admin; other statuses hidden; 4 stream file with public or private cache headers. | Extra public route absent from OpenAPI. Used for avatars and thread images. |
| C-19 | `POST /internal/attachments/{attachment_id}/claim-avatar` | `IMPLEMENTED` | Internal key; no session/CSRF | `{user_id}` | `200 {id, object_key, status:"avatar"}`; `400 MISSING_USER_ID/INVALID_USER_ID`; `404 ATTACHMENT_NOT_FOUND`; `403 FORBIDDEN_ATTACHMENT_CLAIM`; `409 INVALID_ATTACHMENT_STATUS` | 1 verify internal key; 2 parse/validate user_id; 3 lock attachment `FOR UPDATE`; 4 owner must match; 5 allow only `pending` or `avatar`; 6 set `status="avatar"`, `linked_at`. | Called synchronously by User Service avatar flow. No outbox event. |
| C-20 | `POST /internal/attachments/{attachment_id}/release-avatar` | `IMPLEMENTED` | Internal key; no session/CSRF | Path UUID | `200 {status:"deleted"}` or `{status:"not_found"}` | 1 verify internal key; 2 lock attachment; 3 if absent return 200 not_found; 4 set `status="deleted"`; 5 commit; 6 unlink file best-effort post-commit. | Unlink failure is logged; reconciliation handles remaining bytes. |
| C-21 | `POST /internal/events` | `IMPLEMENTED` | Internal key; no session/CSRF | Event envelope `{event_id, event_type, occurred_at, producer, data}` | `200 {status, thread_id, increment, duplicate}` or `200 {status:"ignored"}` | Handles `ThreadViewCountIncremented`: 1 parse IDs; 2 check `consumed_events` by `event_id`; 3 if duplicate return `duplicate:true`; 4 increment `threads.view_count`; 5 insert `consumed_events`; 6 commit. | Other event types are ignored. Consumed events are keyed only by `event_id`, so re-delivery is idempotent. |

> **Content Service status note:** C-05, C-06, C-12, and C-14 are `PARTIALLY_IMPLEMENTED` because documented sort behavior, idempotency, ADR best-answer constraints, and notification-producing comment payloads are incomplete. C-08's contract permits `category_id` in moderation workflows, but the schema ignores it; the moderation endpoint rows carry the resulting gap.

---

## 8. Interaction Service endpoints

Owning service: `src/services/interaction/app.py`

Database: `forum_interactions` — `votes`, `bookmarks`, `thread_views`, `reputation_ledger`, `outbox_events`.

### 8.1 Interaction Service inventory

| ID | A — Method & path | Status | B — Auth / RBAC / CSRF | C — Request | D — Response | E — Processing & DB | F — Events / async / failures |
|---|---|---|---|---|---|---|---|
| I-01 | `GET /users/{user_id}/reputation` | `IMPLEMENTED` | Public; no CSRF | Path UUID | `200 ReputationResponse` | Sums `reputation_ledger.delta` for the user; unknown users return score 0. | No event. No existence check against User Service, so a random UUID returns 0 rather than 404. |
| I-02 | `PUT /threads/{thread_id}/vote` | `IMPLEMENTED` | Session; CSRF | `VoteRequest` | `200 VoteResponse`; `422 VALIDATION_ERROR`; `404 THREAD_NOT_FOUND`; `400 SELF_VOTE_NOT_ALLOWED/INVALID_TARGET`; `503 SERVICE_UNAVAILABLE`; `409 CONFLICT` | 1 validate value; 2 synchronous `GET {content}/threads/{id}`; 3 reject self-vote; 4 transaction: select existing vote, toggle/change/insert; 5 recompute score; 6 upsert `reputation_ledger` delta (±5 new, ±10 flip, revert on cancel); 7 insert `VoteChanged` outbox; 8 commit. | New vote ±5, flip ±10, cancel reverses prior grant. Unique-constraint races map to 409. Notification consumes positive `VoteChanged` for author notifications. |
| I-03 | `PUT /comments/{comment_id}/vote` | `PARTIALLY_IMPLEMENTED` | Session; CSRF | `VoteRequest` | `200 VoteResponse`; `422 VALIDATION_ERROR` | 1 validate value; 2 transaction: toggle/change/insert comment vote; 3 recompute comment score; 4 insert `VoteChanged` outbox; 5 commit. | No Content Service verification and no self-vote check, unlike thread voting and the ADR/contract. Invalid/nonexistent comment IDs can create vote rows. Reputation delta is emitted but no `reputation_ledger` row is written for comment votes. |
| I-04 | `PUT /threads/{thread_id}/bookmark` | `IMPLEMENTED` | Session; CSRF | None | `200 {"bookmarked": true}`; `404 THREAD_NOT_FOUND`; `400 INVALID_TARGET`; `503 SERVICE_UNAVAILABLE` | 1 authenticate; 2 synchronous `GET {content}/threads/{id}`; 3 transaction: select bookmark, insert if absent; 4 catch `IntegrityError` as success. | Idempotent by natural uniqueness, not `Idempotency-Key`. No outbox event. |
| I-05 | `DELETE /threads/{thread_id}/bookmark` | `IMPLEMENTED` | Session; CSRF | None | `204` | 1 authenticate; 2 transaction: select bookmark for current user/thread and delete if present. | Idempotent; no Content existence check. No event. |
| I-06 | `GET /profile/bookmarks` | `IMPLEMENTED` | Session; no CSRF | Query `limit` 1–50, `cursor` | `200 ThreadPageResponse` | 1 authenticate; 2 page bookmarks by `created_at DESC`; 3 for each, synchronous `GET {content}/threads/{id}`; 4 include returned thread or a synthetic fallback for deleted/unreachable threads. | N+1 synchronous HTTP calls. Fallback representation contains placeholder body/category/author. Cursor is bookmark `created_at`, not thread data. |
| I-07 | `POST /threads/{thread_id}/view` | `IMPLEMENTED` | Public; no CSRF | None | `204`; `404 THREAD_NOT_FOUND`; `400 INVALID_TARGET`; `503 SERVICE_UNAVAILABLE` | 1 synchronous `GET {content}/threads/{id}`; 2 compute session key hash from session cookie or client IP with HMAC; 3 transaction: select `thread_views`; if viewed within 30 min return 204; else refresh timestamp or insert; 4 insert `ThreadViewCountIncremented` outbox; 5 commit. | Event is consumed asynchronously by Content (view_count increment, idempotent) and Search (view_count mirror). Concurrent duplicate insert maps to 204. |

> **Interaction Service status note:** I-03 is the only incomplete operation. I-06 is implemented but performs sequential synchronous enrichment and can return placeholder threads, which callers must treat as degraded data.

---

## 9. Moderation Service endpoints

Owning service: `src/services/moderation/app.py`

Database: `forum_moderation` — `reports`, `actions`, `outbox_events`.

### 9.1 Moderation Service inventory

| ID | A — Method & path | Status | B — Auth / RBAC / CSRF | C — Request | D — Response | E — Processing & DB | F — Events / async / failures |
|---|---|---|---|---|---|---|---|
| M-01 | `POST /reports` | `PARTIALLY_IMPLEMENTED` | Session; CSRF | `CreateReportRequest`; optional `Idempotency-Key` | `201 ReportResponse`; `400 SELF_REPORT_NOT_ALLOWED/INVALID_TARGET`; `404 TARGET_NOT_FOUND`; `409 DUPLICATE_REPORT`; `503 SERVICE_UNAVAILABLE` | 1 reject self-report for accounts; 2 verify target: thread via Content `GET /threads/{id}`; comment via Content `GET /comments/{id}`; account via User `GET /users/{id}`; 3 check active duplicate for reporter/target; 4 insert `reports` with `status="pending"`; 5 commit. | Comment verification target does not exist: Content exposes no `GET /comments/{id}` (only PATCH/DELETE), so comment reports fail with 400 `INVALID_TARGET`. `Idempotency-Key` is accepted but unused. No outbox event. |
| M-02 | `GET /reports/mine` | `IMPLEMENTED` | Session; no CSRF | Query `limit` 1–50, `cursor` | `200 {items: ReportResponse[], next_cursor}` | Selects reports by `reporter_id`, orders `created_at DESC`, timestamp cursor. | No cross-service enrichment. |
| M-03 | `GET /moderation/reports` | `IMPLEMENTED` | Session; roles `admin|moderator`; no CSRF | Query `status`, `target_type`, `limit` 1–50, `cursor` | `200 {items, next_cursor}` | Selects reports, optional status/target filters, orders `created_at DESC`, timestamp cursor. | No HTTP enrichment of targets; moderator sees stored report fields only. |
| M-04 | `GET /moderation/reports/{report_id}` | `IMPLEMENTED` | Session; roles `admin|moderator`; no CSRF | Path UUID | `200 ReportResponse`; `404 REPORT_NOT_FOUND` | Selects report by ID. | No target enrichment. |
| M-05 | `PATCH /moderation/reports/{report_id}/review` | `IMPLEMENTED` | Session; roles `admin|moderator`; CSRF | None | `200 ReportResponse`; `404 REPORT_NOT_FOUND`; `409 INVALID_STATE_TRANSITION` | 1 lock report `FOR UPDATE`; 2 require status `pending`; 3 set `status="reviewing"`, `assigned_to=current_user`; 4 commit. | Extra public route absent from OpenAPI. No outbox event. |
| M-06 | `POST /moderation/reports/{report_id}/actions` | `PARTIALLY_IMPLEMENTED` | Session; roles `admin|moderator`; CSRF | `ModerationActionRequest` | `200 ModerationActionResponse`; `404 REPORT_NOT_FOUND`; `409 REPORT_ALREADY_FINALIZED`; `400 ACTION_FAILED` | 1 authenticate/role; 2 lock report; 3 reject finalized; 4 dismiss path: mark dismissed, insert `actions`, insert outbox; 5 enforcement path: synchronous remote action, then mark `actioned` + insert `actions`/outbox on success, or insert failed `actions` audit row and return 400 on failure. | Remote enforcement is synchronous and outside the local transaction (remote call happens while local report row is locked). A failure leaves the report unactioned but records a failed audit row. `move_category` sends `category_id` to `PATCH /threads/{id}`, whose schema ignores it and returns 200, so the report is marked actioned while no move occurs. |
| M-07 | `POST /moderation/actions` | `PARTIALLY_IMPLEMENTED` | Session; roles `admin|moderator`; CSRF | `ProactiveModerationActionRequest` | `200 ModerationActionResponse`; `400 ACTION_FAILED` | 1 authenticate/role; 2 synchronous remote action; 3 transaction: insert `actions` row (`report_id=null`), insert `ModerationActionApplied` outbox on success; on failure insert failed audit row and return 400. | Same `move_category` no-op as M-06. Remote action happens before the local transaction, so a local DB failure can leave the remote effect applied without an audit row. |
| M-08 | `GET /moderation/actions` | `IMPLEMENTED` | Session; roles `admin|moderator`; no CSRF | Query `target_type`, `target_id`, `moderator_id`, `limit` 1–50, `cursor` | `200 {items: ModerationActionResponse[], next_cursor}` | Selects `actions` with filters, orders `created_at DESC`, timestamp cursor. | Read-only audit log. Includes failed attempts in `metadata_` (`outcome`, `error`). |

> **Moderation Service status note:** M-01 and M-06/M-07 are incomplete because of the missing comment lookup and the ineffective category-move call. M-05 extends the public surface; it is implemented but not documented in OpenAPI.

### 9.2 Remote enforcement matrix (`execute_remote_action`)

| Action | Valid target | Remote call | Local effect | Known issue |
|---|---|---|---|---|
| `pin` | thread | `PUT {content}/threads/{id}/pin` with `is_pinned` from metadata or true | Report `actioned`, audit row, outbox | None found. |
| `lock` | thread | `PUT {content}/threads/{id}/lock` with `is_locked` from metadata or true | Report `actioned`, audit row, outbox | None found. |
| `soft_delete` | thread or comment | `DELETE {content}/threads/{id}` or `DELETE {content}/comments/{id}` | Report `actioned`, audit row, outbox | Content returns 204; accepted. |
| `ban` / `unban` | account | `PUT {user}/admin/users/{id}/ban` with `is_banned` and `ban_reason` | Report `actioned`, audit row, outbox | Caller must be moderator/admin; forwarded cookie + CSRF are used. |
| `role_change` | account | `PATCH {user}/admin/users/{id}/role` with `metadata.role` | Report `actioned`, audit row, outbox | Requires `metadata.role`; admin-only endpoint, so a moderator caller receives 403 and the action fails. |
| `move_category` | thread | `PATCH {content}/threads/{id}` with `{"category_id": ...}` | Report `actioned`, audit row, outbox | **No-op:** `EditThreadRequest` has no `category_id`; Content ignores it and returns 200. |
| `dismiss_report` | report | None | Report `dismissed`, audit row, outbox | Only valid on report action path; excluded from proactive schema. |

---

## 10. Notification Service endpoints

Owning service: `src/services/notification/app.py`, `events.py`, `email_worker.py`

Database: `forum_notifications` — `preferences`, `items`, `consumed_events`, `email_deliveries`.

### 10.1 Notification Service inventory

| ID | A — Method & path | Status | B — Auth / RBAC / CSRF | C — Request | D — Response | E — Processing & DB | F — Events / async / failures |
|---|---|---|---|---|---|---|---|
| N-01 | `GET /notifications` | `IMPLEMENTED` | Session; no CSRF | Query `limit` 1–50, `cursor`; source extension `unread_only` | `200 NotificationPageResponse` | Selects items for `recipient_id`; optional `unread_only`; deterministic `(created_at DESC, id DESC)` cursor. | `unread_only` is not described in OpenAPI. No cross-service calls. |
| N-02 | `GET /notifications/unread-count` | `CONTRACT_SOURCE_MISMATCH` | OpenAPI: session required. Source: optional session; anonymous returns 0. | None | `200 {"unread_count": n}` | Anonymous returns 0 without DB access; authenticated counts unread items. | OpenAPI documents `401`; source intentionally returns `200` for anonymous callers. |
| N-03 | `GET /notifications/preferences` | `CONTRACT_SOURCE_MISMATCH` | OpenAPI: session required. Source: optional session. | None | `200 NotificationPreferencesResponse` | Anonymous uses `DEFAULT_USER_ID` and returns that row or defaults (`in_app=true`, `email=false`). | OpenAPI documents `401`; source returns defaults anonymously. |
| N-04 | `PATCH /notifications/preferences` | `CONTRACT_SOURCE_MISMATCH` | OpenAPI: session + CSRF. Source: optional session; CSRF only if cookie present. | `NotificationPreferencesRequest` | `200 NotificationPreferencesResponse` | Anonymous updates `DEFAULT_USER_ID` preferences; authenticated updates own row, creating defaults if absent. | Anonymous mutation is possible. This is a security-relevant contract/source mismatch, not merely a documentation mismatch. |
| N-05 | `GET /notifications/{notification_id}` | `IMPLEMENTED` | Session; no CSRF | Path UUID | `200 NotificationItemResponse`; `404 NOTIFICATION_NOT_FOUND` | Selects by ID **and** `recipient_id=current_user`, preventing cross-user reads. | Extra public route absent from OpenAPI. |
| N-06 | `PATCH /notifications/{notification_id}/read` | `IMPLEMENTED` | Session; CSRF | Path UUID | `200 NotificationItemResponse`; `404 NOTIFICATION_NOT_FOUND` | Selects own item; if unread, sets `is_read=True` and `read_at`; repeated calls are idempotent. | No event. |
| N-07 | `POST /notifications/read-all` | `IMPLEMENTED` | Session; CSRF | None | `204` (source returns `JSONResponse(content=None)`) | Selects all unread own items and marks read. | Body serialization of `content=None` may emit `null`; clients should ignore the body for 204. |
| N-08 | `POST /internal/events` | `IMPLEMENTED` | Internal key; no session/CSRF | `ConsumedEventPayload` | `200 {status, event_id, event_type, notifications_created}` | 1 registry supports `UserRegistered`, `CommentCreated`, `BestAnswerChanged`, `VoteChanged`, `ModerationActionApplied`; 2 handler computes recipients; 3 per recipient, idempotency check on `(event_id, recipient_id)`; 4 preference lookup; 5 create `items` if in-app enabled; 6 queue `email_deliveries` if mandatory or email enabled and address resolvable; 7 insert `consumed_events`; 8 commit. | Unknown event types return success with 0 notifications. `CommentCreated` handler requires `recipient_id`/`thread_author_id`, which Content does not emit, so no comment notifications are created. Email address resolution is a synchronous call to User Service's internal endpoint; failure skips email but still records consumption. |

### 10.2 Notification event behavior

| Event | Recipient derivation | In-app item | Email | Notes |
|---|---|---|---|---|
| `UserRegistered` | `data.user_id` | `welcome` | Mandatory welcome template | Email queued even if email preference is disabled because category is `mandatory_security`. |
| `CommentCreated` | `data.recipient_id` or `data.thread_author_id` | `comment` | `render_comment_notification_email` | Content emits neither field, so no notification is produced. |
| `BestAnswerChanged` | `data.comment_author_id` | `best_answer` | Best-answer template | Works with Content's payload. |
| `VoteChanged` | `data.author_id` | `upvote` | None | Only creates for `my_vote == 1`; excludes self-votes and cancellations. Comment vote payload lacks `author_id`, so comment upvotes do not notify. |
| `ModerationActionApplied` | `data.affected_user_id`/`author_id`, or target account ID | `moderation` | Mandatory moderation template | Moderation payload does not include affected user/author, so only account-target actions resolve a recipient (target ID). Thread/comment moderation notifications are generally skipped. |

### 10.3 Email delivery worker

- Claims `email_deliveries` rows in `pending`/`failed` with `next_attempt_at <= now` and `attempts < max_attempts`, using `SELECT ... FOR UPDATE SKIP LOCKED`.
- Marks rows `sending` with a lease, dispatches outside the transaction, then records `delivered` or `failed`/`dead_letter`.
- Exponential backoff with jitter (base 2 s, cap 60 s), max 5 attempts by default.
- Recipient allowlist is enforced in development/test; rejected recipients move directly to `dead_letter`.
- Stale `sending` leases are recovered on startup and periodically. Crash-after-SMTP-accept can duplicate an email: delivery is at-least-once, not exactly-once.
- Backend is `memory` or `smtp`; memory deliveries are observable through the test sink only.

---

## 11. Search Service endpoints

Owning service: `src/services/search/app.py`, `events.py`

Database: `forum_search` — `documents`, `consumed_events`.

### 11.1 Search Service inventory

| ID | A — Method & path | Status | B — Auth / RBAC / CSRF | C — Request | D — Response | E — Processing & DB | F — Events / async / failures |
|---|---|---|---|---|---|---|---|
| S-01 | `GET /search/suggestions` | `IMPLEMENTED` | Public; no CSRF | Query `q` min length 2, `limit` 1–20 default 5 | `200 {"suggestions": []}` | Selects distinct visible document titles matching `ILIKE %q%`, limited. | No full-text ranking; substring matching. No event. |
| S-02 | `GET /search` | `IMPLEMENTED` | Public; no CSRF | Query `q`, `category_id`, `tag`, `sort` (`relevance|newest|most_viewed`), `limit` 1–50, `cursor` | `200 SearchPage` | Selects `is_visible=true`; filters category/tag/substring title/body; orders by `indexed_at`, `source_created_at`, or `view_count`; uses cursor by corresponding timestamp. | Substring `ILIKE`, not PostgreSQL full-text. `most_viewed` cursor uses `source_created_at`, which is not the sort key, so pagination can be unstable. |
| S-03 | `POST /internal/events` | `IMPLEMENTED` | Internal key; no session/CSRF | `ConsumedEventPayload` | `200 {status, event_id, event_type, document_updated}` | 1 idempotency by `event_id`; 2 dispatch supported events; 3 upsert/update `documents`; 4 insert `consumed_events`; 5 commit. | Supports `ThreadCreated`, `ThreadUpdated`, `ThreadDeleted`, `ThreadLockToggled`, `ThreadPinToggled`, `ThreadViewCountIncremented`, `ModerationActionApplied`. Does not consume `CommentCreated`, `BestAnswerChanged`, or `VoteChanged`. No stale-event/version-ordering rejection. |
| S-04 | `POST /admin/reindex` | `IMPLEMENTED` | Session; role `admin`; CSRF always required | None | `200 {"status":"reindexed","count":n}`; `503` Content unreachable; `502` Content error | 1 authenticate/admin; 2 synchronous `GET {content}/threads?limit=50`; 3 transaction: upsert each returned thread into `documents`, bump version, refresh timestamps. | Extra public route absent from OpenAPI. Only fetches the first 50 threads; no pagination loop. Requires CSRF even without a cookie, unlike other services. |

### 11.2 Search event semantics

| Event | Effect | Gap |
|---|---|---|
| `ThreadCreated` | Upsert document; `is_visible = status == "published"` | Content payload omits `body`, so body indexes empty; tags are present. |
| `ThreadUpdated` | Update provided title/body/tags/category/status; bump version | Content emits only `thread_id`, `author_id`, `title`, `status`, so body/tags/category stay stale. |
| `ThreadDeleted` | Set `is_visible=False`; bump version | Document is retained, not deleted. |
| `ThreadLockToggled` | Bump version/source time | No searchable field changes. |
| `ThreadPinToggled` | Bump version/source time | Pin state is not stored, so search cannot order by pin. |
| `ThreadViewCountIncremented` | Increment `view_count` | No version bump. |
| `ModerationActionApplied` | For thread targets, `soft_delete`/`delete`/`hide` hides; restore/unhide shows; bump version | Thread/comment actions where Moderation cannot resolve affected user still update search; comment targets are ignored. |
| `CommentCreated`, `BestAnswerChanged`, `VoteChanged` | Not subscribed | Threads are not re-indexed for comment/best-answer/vote changes. |

---

## 12. Additional public routes absent from OpenAPI

| ID | A — Method & path | Status | B — Auth / RBAC / CSRF | C — Request | D — Response | E/F Notes |
|---|---|---|---|---|---|---|
| X-01 | `PUT /admin/users/{user_id}/ban` | `IMPLEMENTED` | Session; `admin|moderator`; CSRF | `BanUserRequest` | `200 UserAdminView` | Added to support moderation `ban`/`unban`. Revokes sessions when banning. |
| X-02 | `PATCH /moderation/reports/{report_id}/review` | `IMPLEMENTED` | Session; `admin|moderator`; CSRF | None | `200 ReportResponse` | `pending` → `reviewing` claim transition. |
| X-03 | `GET /notifications/{notification_id}` | `IMPLEMENTED` | Session; no CSRF | Path UUID | `200 NotificationItemResponse`; `404` | Owner-scoped read. |
| X-04 | `POST /admin/reindex` | `IMPLEMENTED` | Session; `admin`; CSRF always | None | `200 {status,count}` | Search index reconciliation from Content's first 50 threads. |
| X-05 | `GET /uploads/images/{image_id}` | `IMPLEMENTED` | Optional session; no CSRF | Path UUID | Image bytes or `404` | Visibility-aware attachment serving; no OpenAPI operation. |

These five routes are reachable through the Gateway because their prefixes match existing router rules. They should either be added to the canonical OpenAPI document or removed if not intended as public API.

---

## 13. Internal service routes

| ID | Method & path | Service | Status | B Auth | C Request | D Response | E/F Notes |
|---|---|---|---|---|---|---|---|
| INT-01 | `GET /internal/users/{user_id}` | User | `IMPLEMENTED` | `X-Internal-Service-Key` | Path UUID | `200` contact record or `404` | Synchronous lookup used by Notification for email resolution. |
| INT-02 | `POST /internal/attachments/{attachment_id}/claim-avatar` | Content | `IMPLEMENTED` | Internal key | `{user_id}` | `200`/`400`/`403`/`404`/`409` | Called by User avatar set flow. |
| INT-03 | `POST /internal/attachments/{attachment_id}/release-avatar` | Content | `IMPLEMENTED` | Internal key | Path UUID | `200 {status}` | Called by User avatar delete/replace flow. |
| INT-04 | `POST /internal/events` | Content | `IMPLEMENTED` | Internal key | Event envelope | `200` | Consumes `ThreadViewCountIncremented` idempotently. |
| INT-05 | `POST /internal/events` | Notification | `IMPLEMENTED` | Internal key | Event envelope | `200 {notifications_created}` | Consumes five event types; per-recipient idempotency. |
| INT-06 | `POST /internal/events` | Search | `IMPLEMENTED` | Internal key | Event envelope | `200 {document_updated}` | Consumes seven event types; event-level idempotency. |

No internal routes exist in Interaction or Moderation; those services communicate outward only through outbox events or public Content/User APIs with the caller's cookie/CSRF.

---

## 14. Synchronous cross-service call inventory

| Caller | Callee | Route | Purpose | Failure mapping |
|---|---|---|---|---|
| Gateway | all services | `/api/v1/{path}` | Public proxy | `404`, `413`, `499`, `502`, `503`, `504` |
| Content / Interaction / Moderation / Notification / Search | User | `GET /auth/session` | Session validation | `401`/`403`, `503` if unreachable |
| Interaction | Content | `GET /threads/{id}` | Verify vote/bookmark/view target | `404`, `400 INVALID_TARGET`, `503` |
| Interaction | Content | `GET /threads/{id}` per bookmark | Enrich bookmark list | Placeholder fallback on error/non-200 |
| Moderation | Content | `GET /threads/{id}` | Verify thread report target | `404`, `400`, `503` |
| Moderation | Content | `GET /comments/{id}` | Verify comment report target | **Route does not exist**; non-200 becomes `400 INVALID_TARGET` |
| Moderation | User | `GET /users/{id}` | Verify account report target | `404`, `400`, `503` |
| Moderation | Content | lock/pin/delete thread, delete comment, patch thread | Enforcement | `400 ACTION_FAILED` on non-success |
| Moderation | User | ban/unban, role change | Enforcement | `400 ACTION_FAILED` on non-success |
| User | Content | claim/release avatar | Avatar lifecycle | `502`, `404`, `403`, `409`, `500` |
| Notification | User | `GET /internal/users/{id}` | Resolve email | Email skipped, event still consumed |
| Search | Content | `GET /threads?limit=50` | Reindex | `503`/`502` |

---

## 15. Endpoint status roll-up

| Service | `IMPLEMENTED` | `PARTIALLY_IMPLEMENTED` | `CONTRACT_SOURCE_MISMATCH` | `STUB_OR_NOT_IMPLEMENTED` | `NOT_VERIFIED` |
|---|---:|---:|---:|---:|---:|
| User (U-01–U-23 public/internal; excludes test-only) | 22 | 1 | 0 | 0 | 0 |
| Content (C-01–C-21; includes internal) | 17 | 4 | 0 | 0 | 0 |
| Interaction (I-01–I-07) | 6 | 1 | 0 | 0 | 0 |
| Moderation (M-01–M-08) | 5 | 3 | 0 | 0 | 0 |
| Notification (N-01–N-08; includes internal) | 5 | 0 | 3 | 0 | 0 |
| Search (S-01–S-04; includes internal) | 4 | 0 | 0 | 0 | 0 |
| Gateway operational (GW-01–GW-08) | 8 | 0 | 0 | 0 | 0 |
| **Total (79 routes)** | **67** | **9** | **3** | **0** | **0** |

> Counts include internal and additional public routes where listed and exclude test-only User routes (U-24–U-25). Gateway operational routes are in Section 2.3 and are included in the total above. No endpoint in this repository is a pure stub: handlers exist for every OpenAPI operation. The gaps are behavioral and contract-level.

### 15.1 Highest-impact endpoint findings

1. `POST /reports` cannot verify comment targets because `GET /comments/{id}` does not exist in Content Service.
2. `PATCH /threads/{id}` ignores `category_id`, so Moderation `move_category` reports success without moving anything.
3. `CommentCreated` notifications are never produced because Content does not emit a recipient/thread-author field.
4. `Idempotency-Key` is documented for thread/comment/report creation but not implemented.
5. `GET /admin/users` ignores documented `search` and `role` filters and never paginates.
6. Notification preferences and unread count are anonymous in source but session-protected in OpenAPI; anonymous preference updates mutate a default user row.
7. `PUT /comments/{id}/vote` skips target verification and self-vote prevention.
8. `GET /threads?sort=most_voted` and `PUT /threads/{id}/best-answer` do not enforce all documented/ADR rules.
9. Search indexes are event-driven but several producers emit payload fields that consumers do not receive/use; comment/vote/best-answer changes never re-index.
10. Search `/admin/reindex` only reconciles the first 50 threads.

---

## 16. How to use this reference with the workflow guide

- Use this document for per-endpoint contracts, statuses, and failure/DB facts.
- Use `docs/api/forum_api_workflow_guide.md` for end-to-end sequences, sync/async boundaries, Mermaid diagrams, workflow index, and the consolidated gap matrix.
- When a status here is `PARTIALLY_IMPLEMENTED` or `CONTRACT_SOURCE_MISMATCH`, the workflow guide explains the user-visible consequence and the source locations involved.
