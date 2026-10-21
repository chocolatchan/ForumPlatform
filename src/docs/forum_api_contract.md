# Forum Platform API Contract

**Phiên bản:** 1.1 (Task 002 Reconciled Baseline)  
**Trạng thái:** Hợp đồng API chuẩn hóa cho MVP  
**Stack mục tiêu:** FastAPI, Pydantic, SQLAlchemy, PostgreSQL 16+  
**Giao tiếp:** HTTPS/JSON giữa client và API Gateway; HTTP nội bộ hoặc Transactional Outbox events tùy service

> Tài liệu này chuẩn hóa toàn bộ các route, schema request/response, mã lỗi, bảo mật, và event contract theo các quyết định kiến trúc đã được phê duyệt (DEC-001 đến DEC-005).

---

## 1. Phạm vi và nguyên tắc

Hệ thống gồm API Gateway và sáu service nghiệp vụ độc lập:

| Thành phần | Trách nhiệm | Dữ liệu sở hữu logic (Database) |
|---|---|---|
| **API Gateway** | Định tuyến, rate limiting, CORS, CSRF check, Correlation ID | Không sở hữu dữ liệu nghiệp vụ |
| **User Service** | Đăng ký, OTP, đăng nhập, session, profile, đổi thông tin, quyền tài khoản | `forum_users`: accounts, sessions, OTP/pending registration, password history |
| **Content Service** | Category, thread, comment, attachment metadata, trạng thái nội dung | `forum_content`: categories, threads, thread_tags, comments, attachments |
| **Interaction Service** | Vote, bookmark, thread view deduplication, reputation ledger | `forum_interactions`: votes, bookmarks, thread_views, reputation ledger |
| **Moderation Service** | Report (thread, comment, account), moderation queue, audit actions | `forum_moderation`: reports, actions |
| **Notification Service** | Thông báo trong ứng dụng, preferences, read state | `forum_notifications`: preferences, items |
| **Search Service** | Tìm kiếm toàn văn và chỉ mục tìm kiếm | `forum_search`: search documents |

### Nguyên tắc sở hữu dữ liệu (DEC-002)

1. Mỗi service là chủ sở hữu duy nhất của dữ liệu nghiệp vụ của mình trong logical database riêng.
2. Tuyệt đối không truy vấn trực tiếp database của service khác và không tạo Foreign Key vật lý xuyên database.
3. ID tham chiếu liên service là UUID logic.
4. Mọi quy trình thay đổi dữ liệu đa service phải dùng Transactional Outbox event hoặc internal REST API có cơ chế retry và idempotent.
5. API public luôn đi qua Gateway. Endpoint nội bộ được bảo vệ và không expose ra ngoài.

---

## 2. Quy ước HTTP & Bảo mật

- **Base URL:** `http://localhost:8000/api/v1`.
- **Định dạng dữ liệu:** JSON UTF-8. Thời gian định dạng ISO 8601 UTC (`2026-10-20T08:30:00Z`).
- **Identifier:** UUIDv4 dạng chuỗi.
- **Phân trang (ADR-002):** Collection lớn dùng cursor opaque (`limit` mặc định 20, tối đa 50; `cursor` dạng base64 string).
- **Session (ADR-003):** Cookie server-side opaque `forum_session`. Production: `HttpOnly; Secure; SameSite=Lax; Path=/`. Idle timeout 4 giờ, absolute timeout 24 giờ.
- **CSRF Protection (ADR-004):** Mọi request mutation (POST, PUT, PATCH, DELETE) dùng cookie bắt buộc phải gửi header `X-CSRF-Token`. Endpoint phát hành CSRF token: `GET /auth/csrf`.
- **Idempotency (ADR-014):** Các thao tác tạo thread, comment, report và moderation action retryable phải hỗ trợ header `Idempotency-Key` (chuỗi tối đa 128 ký tự). Trùng key khác payload trả `409 IDEMPOTENCY_KEY_REUSED`.
- **Anti-Enumeration (DEC-001):** Endpoint đăng ký và khôi phục tài khoản trả về `202 Accepted` với thông điệp trung tính, không tiết lộ email/username đã tồn tại.

---

## 3. Response và Envelope lỗi chuẩn

### Success Envelope (Phân trang)
```json
{
  "items": [],
  "next_cursor": null
}
```

### Error Envelope chuẩn
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": [
      {"field": "title", "reason": "Length must be between 10 and 150 characters."}
    ],
    "request_id": "f7e2c5a2-..."
  }
}
```

### Mã lỗi ứng dụng chuẩn hóa
`VALIDATION_ERROR`, `INVALID_CREDENTIALS`, `ACCOUNT_RESTRICTED`, `SESSION_EXPIRED`, `OTP_INVALID`, `OTP_EXPIRED`, `OTP_ATTEMPTS_EXCEEDED`, `OTP_RESEND_COOLDOWN`, `OTP_RESEND_LIMIT`, `CSRF_INVALID`, `RATE_LIMITED`, `THREAD_LOCKED`, `CONTENT_NOT_FOUND`, `SELF_VOTE_NOT_ALLOWED`, `DUPLICATE_REPORT`, `BEST_ANSWER_NOT_ALLOWED`, `IDEMPOTENCY_KEY_REUSED`, `FILE_TOO_LARGE`, `UNSUPPORTED_MEDIA_TYPE`.

---

## 4. User Service Endpoints

Prefix: `/auth`, `/users`, `/profile`, `/admin/users`.

| Method | Endpoint | Auth | CSRF | Mô tả | Thành công |
|---|---|---|---|---|---|
| `GET` | `/auth/csrf` | Public | Không | Lấy CSRF token cho client | `200 OK` |
| `POST` | `/auth/register` | Public | Không | Khởi tạo đăng ký, gửi OTP (DEC-001) | `202 Accepted` |
| `POST` | `/auth/register/verify` | Public | Không | Xác minh OTP và tạo tài khoản | `201 Created` |
| `POST` | `/auth/register/resend-otp` | Public | Không | Gửi lại OTP theo cooldown/quota (DEC-001) | `202 Accepted` |
| `POST` | `/auth/login` | Public | Không | Đăng nhập, thiết lập cookie `forum_session` | `200 OK` + Set-Cookie |
| `POST` | `/auth/logout` | Session | Có | Thu hồi session hiện tại, xóa cookie | `204 No Content` |
| `GET` | `/auth/session` | Session | Không | Lấy thông tin session và người dùng hiện tại | `200 OK` |
| `POST` | `/auth/recovery/request` | Public | Không | Yêu cầu khôi phục mật khẩu (DEC-001) | `202 Accepted` |
| `POST` | `/auth/recovery/verify` | Public | Không | Xác minh OTP recovery và cấp reset token | `200 OK` |
| `POST` | `/auth/recovery/reset` | Reset Token | Không | Đặt lại mật khẩu mới, thu hồi session cũ | `204 No Content` |
| `GET` | `/users/{user_id}` | Public | Không | Xem hồ sơ công khai của người dùng | `200 OK` |
| `GET` | `/profile` | Session | Không | Xem hồ sơ chi tiết của chính mình | `200 OK` |
| `PATCH` | `/profile` | Session | Có | Cập nhật hồ sơ (`display_name`, `bio`) | `200 OK` |
| `POST` | `/profile/change-email/request` | Session | Có | Yêu cầu đổi email, gửi OTP tới email mới | `202 Accepted` |
| `POST` | `/profile/change-email/verify` | Session | Có | Xác minh OTP và hoàn tất đổi email | `200 OK` |
| `POST` | `/profile/change-password` | Session | Có | Đổi mật khẩu, thu hồi các session khác | `204 No Content` |
| `GET` | `/admin/users` | Admin | Không | Danh sách tài khoản quản trị (phân trang) | `200 OK` |
| `PATCH` | `/admin/users/{user_id}/role` | Admin | Có | Cập nhật role (`member`, `moderator`, `admin`) | `200 OK` |

### Schema trọng yếu User Service

**`POST /auth/register` (DEC-001 Neutral Response)**
- Request:
  ```json
  {"username": "forum_user", "email": "user@example.com", "password": "StrongPassword123!"}
  ```
- Response `202 Accepted`:
  ```json
  {
    "status": "pending_verification",
    "message": "If the registration details are eligible, a verification code has been sent."
  }
  ```

**`POST /auth/register/verify`**
- Request:
  ```json
  {"email": "user@example.com", "otp": "123456"}
  ```
- Response `201 Created`:
  ```json
  {
    "user": {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "username": "forum_user",
      "display_name": null,
      "email": "user@example.com",
      "role": "member"
    }
  }
  ```

---

## 5. Content Service Endpoints

Prefix: `/categories`, `/threads`, `/comments`, `/uploads`, `/admin/categories`.

| Method | Endpoint | Auth | CSRF | Mô tả | Thành công |
|---|---|---|---|---|---|
| `GET` | `/categories` | Public | Không | Danh sách danh mục công khai | `200 OK` |
| `GET` | `/categories/{category_id}` | Public | Không | Chi tiết một danh mục | `200 OK` |
| `POST` | `/admin/categories` | Admin | Có | Tạo danh mục mới | `201 Created` |
| `PATCH` | `/admin/categories/{category_id}` | Admin | Có | Cập nhật danh mục | `200 OK` |
| `GET` | `/threads` | Public | Không | Danh sách bài viết theo filter/sort/cursor | `200 OK` |
| `POST` | `/threads` | Member | Có | Tạo bài viết mới kèm ảnh đính kèm (Idempotency) | `201 Created` |
| `GET` | `/threads/{thread_id}` | Public | Không | Chi tiết bài viết và comments | `200 OK` |
| `PATCH` | `/threads/{thread_id}` | Owner | Có | Chỉnh sửa tiêu đề, nội dung, tags bài viết | `200 OK` |
| `DELETE` | `/threads/{thread_id}` | Owner/Mod | Có | Xóa bài viết (soft delete sang `status: deleted`) | `204 No Content` |
| `PUT` | `/threads/{thread_id}/lock` | Moderator | Có | Khóa/mở khóa bình luận (`is_locked`) | `200 OK` |
| `PUT` | `/threads/{thread_id}/pin` | Moderator | Có | Ghim/bỏ ghim bài viết (`is_pinned`) | `200 OK` |
| `PUT` | `/threads/{thread_id}/best-answer` | Thread Owner | Có | Chọn câu trả lời hay nhất trong Q&A | `200 OK` |
| `POST` | `/threads/{thread_id}/comments` | Member | Có | Tạo bình luận hoặc reply (Idempotency) | `201 Created` |
| `PATCH` | `/comments/{comment_id}` | Owner | Có | Chỉnh sửa nội dung bình luận | `200 OK` |
| `DELETE` | `/comments/{comment_id}` | Owner/Mod | Có | Xóa bình luận (đánh dấu `is_deleted: true`) | `204 No Content` |
| `POST` | `/uploads/images` | Member | Có | Upload ảnh đính kèm (<2MB, multipart) | `201 Created` |

### Schema trọng yếu Content Service (DEC-003)

**`Thread` Object Representation:**
```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "author_id": "8bb98bf7-e160-4b2a-bf34-a15d90bfb834",
  "category_id": "c6a1e3b7-7e61-4c12-87f5-2319fbb87d81",
  "title": "Discussion title between 10 and 150 characters",
  "body": "Thread content with at least twenty characters of detail.",
  "tags": ["fastapi", "postgres"],
  "status": "published",
  "is_pinned": false,
  "is_locked": false,
  "best_answer_comment_id": null,
  "view_count": 42,
  "created_at": "2026-10-20T08:30:00Z",
  "updated_at": "2026-10-20T08:30:00Z"
}
```
*Ghi chú DEC-003:* `status` chỉ nhận `draft`, `published`, `hidden`, `deleted`. Thuộc tính khóa và ghim là boolean độc lập (`is_locked`, `is_pinned`).

---

## 6. Interaction Service Endpoints

Prefix: `/threads`, `/comments`, `/users`, `/profile`.

| Method | Endpoint | Auth | CSRF | Mô tả | Thành công |
|---|---|---|---|---|---|
| `PUT` | `/threads/{thread_id}/vote` | Member | Có | Vote/đổi vote/hủy vote cho thread (`1` hoặc `-1`) | `200 OK` |
| `PUT` | `/comments/{comment_id}/vote` | Member | Có | Vote/đổi vote/hủy vote cho comment (`1` hoặc `-1`) | `200 OK` |
| `PUT` | `/threads/{thread_id}/bookmark` | Member | Có | Bookmark bài viết | `200 OK` |
| `DELETE` | `/threads/{thread_id}/bookmark` | Member | Có | Hủy bookmark bài viết | `204 No Content` |
| `GET` | `/profile/bookmarks` | Session | Không | Danh sách bài viết đã bookmark của mình | `200 OK` |
| `POST` | `/threads/{thread_id}/view` | Public | Không | Ghi nhận view (khử trùng 30m, phát sinh DEC-005) | `204 No Content` |
| `GET` | `/users/{user_id}/reputation` | Public | Không | Xem điểm uy tín công khai của người dùng | `200 OK` |

**Vote Response:**
```json
{
  "target_type": "thread",
  "target_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "my_vote": 1,
  "score": 15,
  "reputation_delta": 5
}
```

---

## 7. Moderation Service Endpoints (DEC-004)

Prefix: `/reports`, `/moderation`.

| Method | Endpoint | Auth | CSRF | Mô tả | Thành công |
|---|---|---|---|---|---|
| `POST` | `/reports` | Member | Có | Báo cáo vi phạm `thread`, `comment`, hoặc `account` | `201 Created` |
| `GET` | `/reports/mine` | Member | Không | Xem danh sách báo cáo do mình gửi | `200 OK` |
| `GET` | `/moderation/reports` | Moderator | Không | Queue báo cáo vi phạm có bộ lọc trạng thái | `200 OK` |
| `GET` | `/moderation/reports/{report_id}` | Moderator | Không | Xem chi tiết báo cáo vi phạm | `200 OK` |
| `POST` | `/moderation/reports/{report_id}/actions` | Moderator | Có | Xử lý báo cáo và áp dụng hành động kiểm duyệt | `200 OK` |
| `POST` | `/moderation/actions` | Moderator | Có | Áp dụng hành động kiểm duyệt chủ động (proactive) | `200 OK` |
| `GET` | `/moderation/actions` | Moderator | Không | Lịch sử audit log các hành động kiểm duyệt | `200 OK` |

### Schema Report & Moderation (DEC-004)
- **`CreateReportRequest`:**
  ```json
  {
    "target_type": "account",
    "target_id": "8bb98bf7-e160-4b2a-bf34-a15d90bfb834",
    "reason": "spam",
    "details": "User is posting repeated automated advertising."
  }
  ```
  - `target_type`: enum `thread`, `comment`, `account`.
  - `reason`: enum `spam`, `abuse`, `harassment`, `wrong_category`, `misinformation`, `other`.
- **`Report` Status:** enum `pending`, `reviewing`, `actioned`, `dismissed`.
- **`ModerationAction` Types:** enum `pin`, `lock`, `move_category`, `soft_delete`, `ban`, `unban`, `role_change`, `dismiss_report`.

---

## 8. Notification Service Endpoints

Prefix: `/notifications`.

| Method | Endpoint | Auth | CSRF | Mô tả | Thành công |
|---|---|---|---|---|---|
| `GET` | `/notifications` | Session | Không | Danh sách thông báo in-app có phân trang | `200 OK` |
| `GET` | `/notifications/unread-count` | Session | Không | Số lượng thông báo chưa đọc | `200 OK` |
| `PATCH` | `/notifications/{notification_id}/read` | Session | Có | Đánh dấu một thông báo là đã đọc | `200 OK` |
| `POST` | `/notifications/read-all` | Session | Có | Đánh dấu toàn bộ thông báo là đã đọc | `204 No Content` |
| `GET` | `/notifications/preferences` | Session | Không | Xem cài đặt thông báo của người dùng | `200 OK` |
| `PATCH` | `/notifications/preferences` | Session | Có | Cập nhật cài đặt nhận thông báo | `200 OK` |

---

## 9. Search Service Endpoints

Prefix: `/search`.

| Method | Endpoint | Auth | CSRF | Mô tả | Thành công |
|---|---|---|---|---|---|
| `GET` | `/search` | Public | Không | Tìm kiếm bài viết theo từ khóa, danh mục, tags | `200 OK` |
| `GET` | `/search/suggestions` | Public | Không | Gợi ý từ khóa tìm kiếm nhanh | `200 OK` |

---

## 10. Event Contract Catalog (Transactional Outbox)

Mọi domain event được ghi vào bảng `outbox_events` trong cùng một database transaction với thay đổi dữ liệu nghiệp vụ:

```json
{
  "event_id": "78202951-df4b-4b2e-a579-245bf7280cf2",
  "event_type": "ThreadCreated",
  "occurred_at": "2026-10-20T08:30:00Z",
  "schema_version": 1,
  "producer": "content-service",
  "correlation_id": "f7e2c5a2-9b21-4f1e-8a1a-3a1e2f3b4c5d",
  "data": {}
}
```

### Danh mục Event chuẩn hóa (DEC-005 Baseline)

| Event Type | Producer | Consumers dự kiến | Mô tả nghiệp vụ |
|---|---|---|---|
| `UserRegistered` | User | Notification | Tài khoản đăng ký và kích hoạt OTP thành công |
| `ThreadCreated` | Content | Search, Notification | Bài viết mới được xuất bản; cập nhật index |
| `ThreadUpdated` | Content | Search | Bài viết được chỉnh sửa; cập nhật index |
| `ThreadDeleted` | Content | Search, Notification | Bài viết bị soft delete; loại bỏ khỏi index search |
| `CommentCreated` | Content | Notification | Bình luận mới được thêm; gửi thông báo tới tác giả/replier |
| `BestAnswerChanged` | Content | Notification, Interaction | Tác giả chọn/đổi best answer; ghi nhận reputation thưởng |
| `VoteChanged` | Interaction | Notification | Lượt vote thay đổi trên bài viết/bình luận |
| `ReputationChanged` | Interaction | User | Điểm uy tín người dùng thay đổi ròng |
| `ThreadViewCountIncremented` | Interaction | Content | **DEC-005:** Gửi batch/increment view count đã khử trùng sang Content Service |
| `ModerationActionApplied` | Moderation | Content, User, Notification, Search | Hành động kiểm duyệt (khóa, xóa, ban) được thực thi |

---

## 11. Definition of Done cho API Contract

Hợp đồng API này là tài liệu chuẩn mực duy nhất để đối chiếu khi hoàn thiện `forum_public_api.openapi.yaml` và triển khai mã nguồn FastAPI. Mọi thay đổi route hoặc schema request/response phải được phản ánh trước tiên vào file này.
