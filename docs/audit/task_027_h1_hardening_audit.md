# Task 027-H1: Image Upload Lifecycle Hardening Audit

## 1. Overview and Scope

This audit evaluates the five residual risk areas identified following the Task 027 image upload lifecycle implementation:
1. **Risk 1: Orphaned files when metadata persistence fails.**
2. **Risk 2: Abandoned pending attachments.**
3. **Risk 3: Unauthorized image access and privacy-policy violations.**
4. **Risk 4: Malformed or malicious image content that passes superficial signature checks.**
5. **Risk 5: Inconsistent upload/request size limits across Gateway and Content Service.**

Operating principles:
- Repository: `chocolatchan/ForumPlatform` (default branch `master`).
- Database isolation: Content Service maintains ownership of `content.attachments` and local persistent volume `/app/uploads`.
- Preserve vanilla architecture, FastAPI conventions, CSRF protection, and session security without adding unvetted heavy external dependencies or redundant microservices.

---

## 2. Risk 1: Orphaned Files When Metadata Persistence Fails

### Current Implementation & Source Analysis
- **File / Function**: `src/services/content/app.py::upload_image`
- **Current Behavior**:
  1. The handler reads chunks, validates magic bytes, and checks size up to 2MB.
  2. The file is written to local filesystem storage via `with open(target_path, "wb") as f: f.write(...)`.
  3. The handler then initiates `async with content_db.transaction() as session:` to insert the `Attachment` record into PostgreSQL.
  4. If the database transaction fails (e.g. unique constraint violation, DB disconnect, disk full on DB, or rollback), the written file on the filesystem remains orphaned in `/app/uploads/images/{id}.{ext}`.
- **Evidence**:
  Lines 1097–1113 in `src/services/content/app.py`:
  ```python
  with open(target_path, "wb") as f:
      for chunk in chunks:
          f.write(chunk)

  # Persist metadata to database
  async with content_db.transaction() as session:
      attachment = Attachment(...)
      session.add(attachment)
  ```
  No `try...except` wraps the database insertion to delete `target_path` on DB exception.
- **Status**: **CONFIRMED DEFECT / RISK**.
- **Required Changes**:
  - Wrap the database transaction in a `try...except` block.
  - If database insertion raises an exception, catch the error and execute synchronous unlink on `target_path` using `contextlib.suppress(FileNotFoundError)`.
  - If unlink fails, log a sanitized operational error with the target `object_key` (without logging server secrets or absolute system paths).
  - Re-raise or return a canonical 500 error response.
- **Regression Risks**: Low; cleanup is bounded strictly to the newly generated `target_path` (`uuid.uuid4().hex`).
- **Testing Approach**:
  - Add test simulating database failure during metadata insert (`patch.object(content_db, "transaction", side_effect=RuntimeError("DB crash"))`) and verify the physical file on disk is immediately cleaned up.

---

## 3. Risk 2: Abandoned Pending Attachments

### Current Implementation & Source Analysis
- **File / Function**: `src/services/content/models.py`, `src/services/content/app.py`
- **Current Behavior**:
  - Uploaded images start with `status = "pending"` and `thread_id = None`.
  - When a user drafts a thread and uploads images but closes the browser or aborts submission, the attachments stay in `status = "pending"` indefinitely in PostgreSQL and on disk.
  - The schema already includes an index: `CREATE INDEX ix_attachments_orphan ON content.attachments(status, created_at) WHERE status = 'pending';` (from `migrations/content/versions/20261020_0001_initial_content.py`).
  - However, no periodic worker task cleans up expired pending attachments.
- **Status**: **CONFIRMED DEFECT / RISK**.
- **Required Changes**:
  - Define a pending TTL (default: 24 hours / 86400 seconds).
  - Implement a bounded cleanup function `cleanup_expired_pending_attachments(db, storage_dir, max_age_seconds=86400, batch_size=100)`.
  - Concurrency & Safety guard:
    - Only select attachments where `status == 'pending'` AND `thread_id IS NULL` AND `created_at < now() - interval`.
    - Atomically update their status to `deleted` or mark for deletion in a transaction (e.g. `WHERE status = 'pending' RETURNING id, object_key`).
    - Remove corresponding disk files using `target_path.unlink(missing_ok=True)`.
    - Never touch `linked` attachments.
  - Integrate periodic background cleanup into the Content Service lifespan / worker loop.
- **Regression Risks**: Must prevent race condition where an attachment uploaded 23h59m ago is being linked concurrently with cleanup. Using a 24h TTL ensures active thread drafting sessions are never purged.
- **Testing Approach**:
  - Seed expired pending attachments (>24h old), recent pending attachments (<1h old), and linked attachments.
  - Run cleanup and verify expired pending are purged from DB and disk, while recent pending and linked attachments are untouched.

---

## 4. Risk 3: Unauthorized Image Access and Privacy Policy Violations

### Current Implementation & Source Analysis
- **File / Function**: `src/services/content/app.py::serve_image`
- **Current Behavior**:
  - `GET /uploads/images/{image_id}` only checks `if not attachment or attachment.status == "deleted": return 404`.
  - If `status == "pending"`, ANY user or unauthenticated client can retrieve the image if they know the UUID.
  - If the attachment is linked to a thread whose status is `hidden` or `deleted`, or if the user is unauthenticated or not an author/moderator, the image file is still unconditionally served.
- **Status**: **CONFIRMED DEFECT / RISK**.
- **Privacy Policy & Authorization Invariants**:
  1. **Public Published Threads**: Images attached to threads with `status == "published"` are publicly accessible (read-by-all).
  2. **Pending Attachments**: An attachment in `pending` status must ONLY be viewable by its `author_id`. Unauthenticated users or other members receive a 404/403 (uniform 404 to avoid enumeration).
  3. **Hidden / Deleted Threads**: Images attached to threads in `hidden` or `deleted` status must NOT be viewable by regular members or anonymous users; only moderators/admins or the original author (for non-deleted hidden content) may access them; otherwise uniform 404.
  4. **Deleted Attachments**: Attachments in `deleted` status return 404.
- **Required Changes**:
  - Update `serve_image` endpoint to accept optional authentication (via `get_current_user_optional`).
  - Fetch attachment joined/associated with its thread `select(Attachment).options(selectinload(Attachment.thread))`.
  - If `attachment.status == "pending"`:
    - If user is authenticated and `current_user.id == attachment.author_id`: allow serving.
    - Otherwise: return 404.
  - If `attachment.status == "linked"`:
    - If `attachment.thread` is missing or `attachment.thread.status == "deleted"`: return 404.
    - If `attachment.thread.status == "hidden"`:
      - If user is authenticated and (`current_user.role in ("moderator", "admin")` or `current_user.id == attachment.thread.author_id`): allow serving.
      - Otherwise: return 404.
    - If `attachment.thread.status == "published"`: allow public serving with cache headers.
- **Regression Risks**: Low. Public thread images continue to load publicly.
- **Testing Approach**:
  - Test pending image access by author (200), by stranger (404), unauthenticated (404).
  - Test image linked to published thread by anyone (200).
  - Test image linked to hidden/deleted thread by stranger (404) vs moderator/author (200).

---

## 5. Risk 4: Malformed or Malicious Image Content Passing Superficial Signature Checks

### Current Implementation & Source Analysis
- **File / Function**: `src/services/content/storage.py::sniff_image_type`
- **Current Behavior**:
  - Checks first few magic bytes (`\xff\xd8\xff` for JPEG, `\x89PNG\r\n\x1a\n` for PNG, `RIFF....WEBP` for WebP).
  - Does NOT validate image structure, chunk validity, or pixel dimensions.
  - A 2MB file with `\x89PNG\r\n\x1a\n` followed by 2 million bytes of garbage or an extreme decompression bomb (e.g. 100,000 x 100,000 pixels in PNG IHDR) will pass.
- **Status**: **CONFIRMED DEFECT / RISK**.
- **Required Changes**:
  - Standard library pure-Python parsing for JPEG, PNG, and WebP dimensions and structural integrity:
    - **PNG**: Parse `IHDR` chunk immediately following 8-byte signature. Extract `width` (4 bytes), `height` (4 bytes), bit depth, color type. Enforce `width <= 8192` and `height <= 8192`. Verify presence of `IEND` chunk.
    - **JPEG**: Parse SOF markers (`SOF0`, `SOF1`, `SOF2`: `0xFF, 0xC0..0xC2`) to extract `height` and `width`. Enforce `width <= 8192` and `height <= 8192`. Verify image starts with SOI (`0xFF 0xD8`) and ends with EOI (`0xFF 0xD9`).
    - **WebP**: Parse `VP8` or `VP8L` or `VP8X` chunk headers to extract canvas dimensions. Enforce max 8192x8192.
  - Reject malformed, truncated, or zero-dimension images with `415 Unsupported Media Type` or `422 Unprocessable Content`.
- **Regression Risks**: Valid standard JPEGs, PNGs, and WebPs have standard headers and chunks; limits of 8192x8192 allow up to 67 megapixels (far exceeding any 2MB raster photo).
- **Testing Approach**:
  - Test valid PNG, JPEG, WebP.
  - Test truncated PNG (missing chunks / missing IEND).
  - Test spoofed PNG with valid header but invalid chunks.
  - Test oversized dimension image (>8192x8192).

---

## 6. Risk 5: Inconsistent Upload/Request Size Limits Across Gateway and Content Service

### Current Implementation & Source Analysis
- **File / Function**:
  - `src/gateway/forwarder.py::forward_request`
  - `src/services/content/app.py::upload_image`
- **Current Behavior**:
  - `src/services/content/storage.py` defines `MAX_FILE_SIZE_BYTES = 2097152` (2MB).
  - Content Service streams incoming chunks up to 2MB. If the file payload exceeds 2MB, it returns `413 Content Too Large`.
  - In `src/gateway/forwarder.py`, `body = await request.body()` reads the entire incoming body into memory without a streaming size limit on the Gateway layer.
  - If a client uploads a 100MB body, the Gateway buffers all 100MB into memory before forwarding to Content Service.
  - Furthermore, multipart form-data adds MIME boundary overhead (~500 bytes to 2KB) on top of the raw file binary.
- **Status**: **PARTIALLY MITIGATED / CONFIRMED AT GATEWAY LAYER**.
- **Required Changes**:
  - In Gateway `src/gateway/forwarder.py`:
    - Check `Content-Length` header against a maximum gateway request limit (e.g. 5MB for uploads to allow 2MB file + multipart overhead).
    - If `Content-Length` > 5MB, return early `413 Payload Too Large`.
    - If `Content-Length` is missing or chunked, stream-buffer up to `MAX_GATEWAY_BODY_SIZE` (5MB). If exceeded during read, return `413 Payload Too Large` immediately without buffering into memory.
  - In Content Service:
    - Continue strict streaming limit `MAX_FILE_SIZE_BYTES = 2097152` specifically on the `file` part.
- **Regression Risks**: Low; 5MB Gateway limit comfortably accommodates 2MB image payloads while stopping memory exhaustion attacks.
- **Testing Approach**:
  - Test upload of 2MB file (succeeds).
  - Test upload of file with 2,097,153 bytes (returns 413).
  - Test upload with giant Content-Length or oversized stream to Gateway (returns 413).
