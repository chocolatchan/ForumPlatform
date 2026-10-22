# Task 027-H1: Change Log

## 1. Summary of Changes

Task 027-H1 hardened the image upload lifecycle across five residual risk dimensions without introducing new microservices, unvetted external dependencies, or breaking changes.

---

## 2. File Modifications

### 2.1 API Gateway
- `src/gateway/forwarder.py`:
  - Added `MAX_GATEWAY_BODY_SIZE_BYTES = 5242880` (5 MB).
  - Enforced Gateway-level validation on incoming `Content-Length`.
  - Replaced unconstrained `await request.body()` with streaming chunk accumulator enforcing the 5MB ceiling during read.
  - Returns canonical 413 `PAYLOAD_TOO_LARGE` if request body exceeds limit before proxying to upstream services.

### 2.2 Content Service Configuration & Authentication
- `src/services/content/config.py`:
  - Added `attachment_pending_ttl_seconds = 86400` (24h).
  - Added `attachment_cleanup_interval_seconds = 3600` (1h).
  - Added `attachment_cleanup_batch_size = 100`.
- `src/services/content/auth.py`:
  - Added `get_optional_current_user` FastAPI dependency to optionally extract `ContentUser` context from session cookie without raising 401 when anonymous.

### 2.3 Image Storage & Deep Content Validation
- `src/services/content/storage.py`:
  - Added dimension constraints `MAX_IMAGE_WIDTH = 8192` and `MAX_IMAGE_HEIGHT = 8192`.
  - Added `validate_image_content(data: bytes)` supporting standard library pure-Python parsing:
    - `_validate_png`: Checks IHDR chunk validity, dimension bounds, and presence of terminal IEND chunk.
    - `_validate_jpeg`: Checks SOI (`0xFF 0xD8`), EOI (`0xFF 0xD9`), and scans SOF markers to extract and validate frame dimensions.
    - `_validate_webp`: Validates RIFF container structure and parses VP8, VP8L, and VP8X canvas width and height bounds.
    - Rejects malformed, truncated, or excessive dimension files with descriptive error messages.

### 2.4 Content Service Application & Routes
- `src/services/content/app.py`:
  - `upload_image`:
    - Added deep content and dimension inspection via `validate_image_content()`.
    - Wrapped database metadata persistence in `try...except`. On DB failure, safely unlinks the newly created file from disk (`target_path.unlink(missing_ok=True)`) to prevent orphaned files and returns 500 `DATABASE_ERROR`.
  - `serve_image`:
    - Updated dependency to use `get_optional_current_user`.
    - Joined thread relationship via `options(selectinload(Attachment.thread))`.
    - Enforced privacy policy:
      - Pending images are accessible only to their author.
      - Images linked to `hidden` threads are accessible only to thread author or moderator/admin.
      - Images linked to `deleted` threads or nonexistent threads return uniform 404.
      - Images linked to `published` threads remain accessible to all with public caching.
  - Background Cleanup Worker:
    - Implemented `cleanup_expired_pending_attachments()`.
    - Integrated `_pending_attachment_cleanup_loop()` into FastAPI `lifespan` context manager.

### 2.5 Automated Test Suite
- `tests/test_content_service.py`:
  - Added `test_image_upload_hardening_suite` verifying:
    - Phase E: Rejection of corrupted PNG and decompression bombs.
    - Phase B: Automatic rollback and disk cleanup upon database error.
    - Phase D: Strict authorization checks for pending, hidden, deleted, and published attachments across author, stranger, moderator, and unauthenticated roles.
    - Phase C: Execution of `cleanup_expired_pending_attachments` purging expired pending records and files while leaving linked records intact.

### 2.6 Audit Documentation
- `docs/audit/task_027_h1_hardening_audit.md`
- `docs/audit/task_027_h1_validation_report.md`
- `docs/audit/task_027_h1_change_log.md`
