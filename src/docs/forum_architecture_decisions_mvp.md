# Forum Platform: Architecture Decision Record (MVP)

**Phiên bản:** 1.1 (Task 002 Reconciled Baseline)  
**Trạng thái:** Approved Architecture Baseline  
**Stack:** FastAPI, Pydantic, SQLAlchemy, PostgreSQL 16+

Tài liệu này xác lập baseline kiến trúc chính thức cho Forum Platform sau khi rà soát và phê duyệt các quyết định cốt lõi (DEC-001 đến DEC-005) từ Task 001/Task 002. Mọi thay đổi tiếp theo phải được cập nhật đồng bộ trên ERD, OpenAPI, API Contract, diagrams, code và test suites.

---

## 1. Approved Baseline Decisions (Task 002)

| Quyết định | Nội dung phê duyệt | Hệ quả kiến trúc & triển khai |
|---|---|---|
| **DEC-001: Account Enumeration** | Dùng response trung tính `202 Accepted` cho đăng ký và khôi phục tài khoản nhằm triệt tiêu nguy cơ user enumeration. | API public không phản hồi `409 USERNAME_TAKEN` hay `409 EMAIL_TAKEN` để lộ sự tồn tại của tài khoản. Lỗi cú pháp/schema vẫn trả `422`, lỗi rate limit trả `429`. Email thông báo được gửi nếu tài khoản hợp lệ hoặc hướng dẫn khôi phục/đăng nhập nếu email đã tồn tại. |
| **DEC-002: Database Isolation** | Bảo đảm quyền sở hữu dữ liệu cấp service. Tuyệt đối không tạo Foreign Key vật lý xuyên ranh giới service/database. | Mỗi service sở hữu database logic riêng trong cùng cluster: `forum_users`, `forum_content`, `forum_interactions`, `forum_moderation`, `forum_notifications`, `forum_search`. ID xuyên service là UUID thuần logic; giao tiếp qua REST nội bộ hoặc outbox events. |
| **DEC-003: Thread Lifecycle** | Chuẩn hóa vòng đời thread thành trạng thái vòng đời (lifecycle status) kết hợp các cờ độc lập (independent flags). | **Lifecycle status:** `draft`, `published`, `hidden`, `deleted`.<br>**Independent flags:** `is_locked` (chặn bình luận mới), `is_pinned` (ghim đầu danh mục).<br>Loại bỏ trạng thái `removed` và `locked` khỏi enum status. Đồng bộ tuyệt đối giữa OpenAPI và SQL check constraints. |
| **DEC-004: User Account Reporting** | Hỗ trợ báo cáo tài khoản người dùng (`account`) trong MVP bên cạnh `thread` và `comment`. | Target types hỗ trợ: `thread`, `comment`, `account`. Ràng buộc chống trùng: 1 report hoạt động (`pending`/`reviewing`) cho mỗi cặp (reporter, target). Trạng thái chuẩn: `pending`, `reviewing`, `actioned`, `dismissed`. Bảng audit `actions` liên kết tùy chọn với `report_id`. |
| **DEC-005: View Count Propagation** | Lan truyền lượt xem bài viết bất đồng bộ qua Transactional Outbox event. | Interaction Service sở hữu khử trùng lặp lượt xem (cửa sổ 30 phút theo session/IP hash). Khi ghi nhận view hợp lệ, Interaction Service phát sinh outbox event `ThreadViewCountIncremented` (kèm batch/delta). Content Service tiêu thụ event idempotent để cập nhật `content.threads.view_count`. |

---

## 2. Bảng tổng hợp Architecture Decision Records (ADRs)

| ID | Quyết định | Trạng thái | Hệ quả cần triển khai |
|---|---|---|---|
| **ADR-001** | Dùng UUIDv4 cho ID public và ID liên service. PostgreSQL dùng kiểu `UUID`. | Approved | Không dùng ID tự tăng làm ID public; không tạo FK xuyên database/service. |
| **ADR-002** | Collection lớn dùng cursor pagination opaque. `limit` mặc định 20, tối đa 50. | Approved | Sort ổn định, có tie-breaker như `(created_at DESC, id DESC)`. |
| **ADR-003** | Dùng opaque server-side session qua cookie `forum_session`. Idle timeout 4 giờ, absolute timeout 24 giờ. | Approved | Cookie production `HttpOnly; Secure; SameSite=Lax; Path=/`; không lưu token đăng nhập trong localStorage. |
| **ADR-004** | Dùng synchronizer CSRF token gắn với session, truyền qua `X-CSRF-Token`. Cung cấp endpoint `GET /api/v1/auth/csrf`. | Approved | Mọi mutation dùng session cookie phải kiểm tra CSRF (kể cả logout). |
| **ADR-005** | Triệt tiêu Account Enumeration qua response trung tính (DEC-001). | Superseded by DEC-001 | Đăng ký và phục hồi tài khoản trả `202 Accepted` trung tính. |
| **ADR-006** | Upload ảnh qua Content Service bằng `multipart/form-data`; lưu tại Object Storage. Quản lý attachment metadata. | Approved | File < 2 MB, định dạng JPG/PNG/WebP, magic bytes check, strip EXIF. Metadata lưu tại bảng `content.attachments` để theo dõi và dọn object mồ côi. |
| **ADR-007** | Chuẩn hóa Thread Lifecycle và Soft Delete (DEC-003). | Superseded by DEC-003 | Thread dùng status `draft`, `published`, `hidden`, `deleted` cùng cờ `is_locked`, `is_pinned`. Comment dùng cờ `is_deleted`. Purge vật lý là batch job riêng. |
| **ADR-008** | Owner không tự xóa thread nếu đã có comment hoặc best answer. | Approved | Trả `409 THREAD_REQUIRES_MODERATION`; moderator xử lý theo policy và ghi audit action. |
| **ADR-009** | Vote score là tổng vote hiện hành (`+1`/`-1`). Reputation tính theo ledger thay đổi ròng. | Approved | API và Database dùng giá trị số `1` / `-1`. Vote và ledger ghi nhận transactional trong Interaction Service. Không cho tự vote. |
| **ADR-010** | Quản lý báo cáo vi phạm nội dung và tài khoản (DEC-004). | Superseded by DEC-004 | Target types: `thread`, `comment`, `account`. Trạng thái: `pending`, `reviewing`, `actioned`, `dismissed`. Mỗi user báo cáo tối đa 1 lần/target. |
| **ADR-011** | Phân quyền sở hữu dữ liệu và điều phối đa service retry-safe. | Approved | Moderation sở hữu report/audit; User sở hữu account/ban/session; Content sở hữu thread/comment; Interaction sở hữu vote/view/reputation. Không cross-DB update. |
| **ADR-012** | Search eventual-consistent; mục tiêu MVP cập nhật index trong vòng 60 giây. | Approved | Index đồng bộ bất đồng bộ qua event outbox; revalidate visibility để tránh trả bài viết đã ẩn/xóa. |
| **ADR-013** | Database per Service: Mỗi microservice có logical database riêng (DEC-002). | Superseded by DEC-002 | 6 logical database: `forum_users`, `forum_content`, `forum_interactions`, `forum_moderation`, `forum_notifications`, `forum_search`. Tuyệt đối không cross-service FK. |
| **ADR-014** | Dùng Transactional Outbox cho các domain events quan trọng. | Approved | Producer ghi business data và outbox trong cùng 1 transaction; consumer deduplicate theo `event_id`, có retry và dead-letter queue. |
| **ADR-015** | Chuẩn hóa username/email trước khi unique check. Mật khẩu băm bằng Argon2id. | Approved | Normalization thống nhất ở validation, DB uniqueness và login. Không trim mật khẩu âm thầm. |
| **ADR-016** | Khử trùng lặp lượt xem và lan truyền view count bất đồng bộ (DEC-005). | Superseded by DEC-005 | Cửa sổ 30 phút theo session/IP HMAC hash tại Interaction Service; outbox event `ThreadViewCountIncremented` cập nhật `content.threads.view_count`. |

---

## 3. Quyền sở hữu dữ liệu & Ranh giới Database

| Service | Logical Database | Dữ liệu và bảng thuộc quyền sở hữu |
|---|---|---|
| **User Service** | `forum_users` | `accounts`, `pending_registrations`, `one_time_tokens`, `sessions`, `password_history`, `email_change_requests` |
| **Content Service** | `forum_content` | `categories`, `threads`, `thread_tags`, `comments`, `attachments` |
| **Interaction Service** | `forum_interactions` | `votes`, `bookmarks`, `thread_views`, `reputation_ledger` |
| **Moderation Service** | `forum_moderation` | `reports`, `actions` |
| **Notification Service** | `forum_notifications` | `preferences`, `items` |
| **Search Service** | `forum_search` | `documents` |
| **API Gateway** | Không có DB | Định tuyến, rate limiting, xác thực cookie sớm, CORS, request ID |

**Nguyên tắc cốt lõi:**
1. Mỗi service chỉ đọc/ghi trực tiếp vào database của chính mình.
2. Không tồn tại Foreign Key vật lý xuyên ranh giới database giữa các service.
3. Tham chiếu giữa các service sử dụng UUID logic (ví dụ `author_id`, `thread_id`, `reporter_id`).
4. Tính toàn vẹn và quy trình nghiệp vụ đa service được điều phối qua REST API nội bộ hoặc Transactional Outbox events.

---

## 4. Các quy tắc nghiệp vụ đã thống nhất

1. **Best Answer:** Chỉ chủ sở hữu thread mới có quyền chọn best answer; thread phải thuộc category dạng Q&A, comment phải thuộc đúng thread đó và thread chưa bị khóa (`is_locked = false`).
2. **Khóa / Ghim bài viết:** Quyền thuộc về Moderator/Admin; `is_locked` ngăn chặn tạo comment/reply mới nhưng không làm ẩn bài viết; `is_pinned` đưa bài viết lên đầu danh mục.
3. **Soft-delete & Ẩn bài viết:**
   - Tác giả được sửa/xóa bài viết của mình nếu bài viết chưa có comment và chưa có best answer. Nếu đã có comment/best answer, trả `409 THREAD_REQUIRES_MODERATION`.
   - Bài viết bị xóa đánh dấu `status = 'deleted'`; bài viết vi phạm kiểm duyệt chuyển `status = 'hidden'`.
4. **Báo cáo vi phạm:** Hỗ trợ báo cáo thread, comment và user account. Lý do chuẩn hóa: `spam`, `abuse`, `harassment`, `wrong_category`, `misinformation`, `other`.
5. **Khử trùng lặp lượt xem (Anti-inflation View):** Ghi nhận tối đa 1 view trong 30 phút cho cùng một session token hash hoặc IP HMAC hash. Interaction Service phát sinh event `ThreadViewCountIncremented` để Content Service cập nhật bộ đếm hiển thị.
6. **Bình luận phân cấp:** Giới hạn tối đa 2 cấp (top-level comment và direct replies). Bình luận con phải thuộc cùng một `thread_id` với bình luận cha.
7. **Idempotency:** Các thao tác `POST /threads`, `POST /threads/{id}/comments`, `POST /reports` và các thao tác moderation retryable phải hỗ trợ header `Idempotency-Key` (tối đa 128 ký tự).
