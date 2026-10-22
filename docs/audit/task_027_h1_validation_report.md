# Task 027-H1: Image Upload Lifecycle Hardening Validation Report

## 1. Executive Summary and Verdict

**Verdict: PASS**

All five residual risk areas identified following Task 027 have been audited, systematically mitigated, and thoroughly verified through automated tests, static typing, code linting, and full browser integration against the live Docker stack.

- **Current Git Branch**: `master` (tracking `origin/master`)
- **Current HEAD Commit**: `7f3942832809934eb2814d82ae04b76dd00a9954` (`027`)
- **Working Tree Status**: Uncommitted modifications confined strictly to the 5 risk mitigations and test/audit suites (zero untracked runtime artifacts).

---

## 2. Hardening Status Matrix Across All Five Risks

| Risk Area | Description | Root Cause / Pre-Condition | Mitigation Applied | Final Status |
| :--- | :--- | :--- | :--- | :--- |
| **Risk 1** | Orphaned files when metadata persistence fails | File was written to disk before DB transaction; an uncaught DB failure left the file on disk indefinitely. | Wrapped DB transaction in `try...except`; on DB failure, immediately unlinks newly created file and logs sanitized `object_key`. Returns 500 `DATABASE_ERROR`. | **RESOLVED & VERIFIED** |
| **Risk 2** | Abandoned pending attachments | Unlinked uploads remained in `pending` status indefinitely with no expiration worker. | Implemented `cleanup_expired_pending_attachments()` with a 24-hour TTL, running periodically in Content Service lifespan. Atomically marks expired pending attachments as `deleted` and unlinks files. | **RESOLVED & VERIFIED** |
| **Risk 3** | Unauthorized image access & privacy violations | `GET /uploads/images/{id}` served images without checking thread visibility or pending status. | Added `get_optional_current_user` dependency. Pending images are restricted strictly to author (404 for others). Images linked to hidden threads restricted to author/moderator/admin (404 for strangers). Public threads served publicly. | **RESOLVED & VERIFIED** |
| **Risk 4** | Malformed/malicious images passing magic bytes | Only initial 12 bytes were checked. Decompression bombs or corrupt chunks passed through. | Added `validate_image_content()` pure Python parser enforcing maximum dimensions (8192x8192), valid IHDR/IEND for PNG, SOI/EOI and SOF frame parsing for JPEG, and RIFF/VP8 canvas bounds for WebP. | **RESOLVED & VERIFIED** |
| **Risk 5** | Inconsistent upload/request size limits | Gateway buffered entire payload into RAM via `request.body()` without a limit before proxying. | In Gateway `forwarder.py`, enforced 5MB ceiling on `Content-Length` and streaming reader. Payloads exceeding limit are aborted with 413 `PAYLOAD_TOO_LARGE` without buffering into memory. Content Service strictly enforces 2MB limit on image file payload. | **RESOLVED & VERIFIED** |

---

## 3. Test Execution and Evidence Summary

### 3.1 Content Service Hardening & Lifecycle Suite (`tests/test_content_service.py`)
- **Command**: `./venv/bin/pytest tests/test_content_service.py -v`
- **Result**: **6 passed in 3.09s**
- **Specific Scenarios Verified**:
  - `test_image_upload_hardening_suite`:
    - Phase E: PNG missing IEND rejected with 415.
    - Phase E: Decompression bomb (10000x10000 dimensions) rejected with 415.
    - Phase B: Simulated DB insertion failure triggers immediate physical file cleanup on disk and returns 500 `DATABASE_ERROR`.
    - Phase D: Pending image viewable by author (200), hidden from unauthenticated (404), hidden from stranger (404).
    - Phase D: Image attached to Hidden thread viewable by author and moderator (200), hidden from stranger and unauthenticated (404).
    - Phase D: Image attached to Published thread publicly viewable (200) with `Cache-Control: public, max-age=86400`.
    - Phase C: `cleanup_expired_pending_attachments` purges expired pending attachment (>24h) and unlinks disk file, while leaving linked attachments intact.

### 3.2 Frontend Integration Suite (`tests/test_frontend_e2e.py`)
- **Command**: `./venv/bin/pytest tests/test_frontend_e2e.py -v`
- **Result**: **8 passed in 3.41s**

### 3.3 Full Browser Playwright E2E Suite (`tests/test_browser_e2e.py`)
- **Command**: `./venv/bin/pytest tests/test_browser_e2e.py -v`
- **Result**: **18 passed in 74.03s**
- Covers full live browser interaction through the API Gateway, including registration, login, thread creation, image upload dropzone, preview chips, markdown snippet insertion, thread publishing, and feed rendering.

### 3.4 Static Analysis and Type Safety
- **Ruff**: `ruff check src/ tests/` -> **All checks passed!** (0 errors)
- **Pyright**: `pyright src/ tests/test_content_service.py tests/test_browser_e2e.py` -> **0 errors, 0 warnings, 0 informations**.

---

## 4. Docker and Migration Status

- **Database Migrations**: No schema migration required. The existing `content.attachments` table and indexes (`ix_attachments_orphan` on `(status, created_at) WHERE status = 'pending'`) already perfectly support the atomic `cleanup_expired_pending_attachments` workflow.
- **Docker Volumes**: `content_uploads:/app/uploads` confirmed persistent across container restarts. Containers `forum_content_service` and `forum_api_gateway` running healthy.

---

## 5. Remaining Risks and Operational Limitations

1. **Local Filesystem Storage Ceiling**:
   - The current storage uses Docker named volume `content_uploads`. If the host disk fills up, uploads will fail safely (triggering DB rollback and cleanup). For large-scale production, an S3-compatible backend (MinIO / AWS S3) can be configured via `storage_backend = "s3"`.
2. **Pending Cleanup Interval**:
   - Background cleanup runs every 1 hour with a 24-hour cutoff and a batch size of 100. This is optimal for preventing DB contention and resource spikes.
