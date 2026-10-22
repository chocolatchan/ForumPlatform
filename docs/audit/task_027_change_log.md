# Task 027 Change Log: Image Upload Lifecycle

## 1. Summary of Changes

Implemented end-to-end image upload lifecycle across Content Service backend, storage volume configuration, frontend UI, and automated validation tests.

---

## 2. Detailed File Modifications

### 2.1 Backend: Content Service
- `src/services/content/storage.py` (New):
  - Created storage utility with `sniff_image_type(header: bytes)` checking magic bytes for JPEG, PNG, and WebP.
  - Added path resolution helper `ensure_storage_dir()` and safe file key generator `generate_object_key()`.
- `src/services/content/schemas.py`:
  - Added `AttachmentResponse` schema matching OpenAPI specification (`id`, `object_key`, `file_size`, `mime_type`, `status`, `created_at`).
- `src/services/content/app.py`:
  - Implemented `POST /uploads/images`:
    - Validates caller authentication via `forum_session` and CSRF via `X-CSRF-Token`.
    - Reads initial chunk to sniff image magic bytes; returns `415 Unsupported Media Type` for non-raster formats or SVG.
    - Streams payload enforcing max 2MB (`2097152` bytes); returns `413 Content Too Large` if exceeded.
    - Saves file to disk under `uploads/images/{id}.{ext}`.
    - Creates database record in `content.attachments` with `status="pending"`.
  - Implemented `GET /uploads/images/{image_id}`:
    - Queries `content.attachments`, validates status is not deleted, and returns `FileResponse` with `X-Content-Type-Options: nosniff` and `Cache-Control: public, max-age=86400`.
  - Updated `create_thread` (`POST /threads`):
    - Validates any passed `image_ids`: confirms attachment exists, is owned by `current_user.id`, and has `status="pending"`.
    - Atomically updates status to `linked`, associates `thread_id`, sets `linked_at = now()`, and publishes `image_ids` in `ThreadCreated` outbox payload.

### 2.2 Infrastructure: Docker Compose
- `docker-compose.yml`:
  - Attached persistent named volume `content_uploads:/app/uploads` to `content-service`.
  - Declared `content_uploads:` in top-level `volumes`.

### 2.3 Frontend Client
- `frontend/src/api.js`:
  - Updated `createThread(title, body, categoryId, imageIds)` to pass `image_ids`.
  - Added `uploadImage(file)` sending `multipart/form-data` with `X-CSRF-Token`.
- `frontend/index.html`:
  - Added attachment upload dropzone and file input `#thread-image-input` and `#btn-upload-image` in `#view-create-thread`.
  - Added `#thread-attachments-list` container for visual preview chips.
- `frontend/src/app.js`:
  - Added attachment state management (`pendingAttachmentIds`).
  - Added client-side validation (max 2MB, allowed image MIME types).
  - Automatically appends markdown `![attachment](/api/v1/uploads/images/{id})` to the body textarea upon successful upload.
  - Attached `image_ids` array when submitting `formCreateThread`.

### 2.4 Testing Suite
- `tests/test_content_service.py`:
  - Added `test_image_upload_lifecycle_and_validation` verifying unauthenticated 401, missing CSRF 403, fake SVG 415, oversized 413, valid PNG 201, image retrieval 200, thread linking, and re-linking conflict 400.
- `tests/test_browser_e2e.py`:
  - Added `test_browser_image_upload_and_thread_attachment_journey` verifying live browser user journey: registration, login, thread creator, image upload, attachment chip display, markdown insertion, and thread publication.

### 2.5 Repository & Environment
- `.gitignore`:
  - Added `uploads/` to ignore local temporary media artifacts.

### 2.6 Audit Documentation
- `docs/audit/task_027_image_upload_design.md`
- `docs/audit/task_027_validation_report.md`
- `docs/audit/task_027_change_log.md`
