# Task 027-H1-R1: Residual Risk Remediation Validation Report

## 1. Executive Summary

This report documents the validation and verification of remedies implemented under **Task 027-H1-R1 (Residual Risk Remediation for Image Upload Lifecycle)**.
Following the findings of Task 027 and Task 027-H1, five targeted workstreams were remediated and verified:
1. **Filesystem-to-database storage reconciliation**: Autonomous detection and cleanup of orphaned files with no DB metadata.
2. **Row-level locking concurrency verification**: Preventing race conditions between concurrent thread creation attachment-linking and pending attachment cleanup via pessimistic locking on live PostgreSQL.
3. **Image decoder assurance & normalization**: Full raster payload decoding via Pillow, decompression bomb limits, EXIF stripping, and clean re-encoding.
4. **Gateway streaming pass-through**: True chunked streaming request forwarding with bounded 5 MB cumulative payload ceiling.
5. **Cache & privacy consistency audit**: Verification and documentation of public/private thread image caching nuances and security posture.

All verification suites (unit, integration, concurrency on live PostgreSQL, frontend E2E, and browser Playwright E2E) passed with zero regressions.

---

## 2. Workstream Verification Details

### Workstream 1: Filesystem-to-Database Reconciliation
- **Implementation**: [`reconcile_orphaned_storage_files`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/content/app.py#L149) in `src/services/content/app.py`.
- **Validation Criteria**:
  - Scans only the configured `uploads/images/` directory without following symlinks.
  - Matches filenames against `SAFE_OBJECT_KEY_PATTERN` (`^[0-9a-fA-F-]{36}\.(jpg|jpeg|png|webp)$`).
  - Ignores files newer than the configurable safety age (default 24 hours / 86400s) to safeguard legitimate in-flight uploads.
  - Batches database queries (`Attachment.storage_path.in_(...)`) in chunks of 50.
  - Immediately before unlinking, executes a secondary atomic check against the DB.
  - Bounds iterations per run (`max_scan_files=500`).
- **Test Evidence**:
  - [`test_image_upload_hardening_suite`](file:///home/chocolat/Documents/Project/ForumPlatform/tests/test_content_service.py#L960) in `tests/test_content_service.py`:
    - Seeded an unrecorded file with $mtime > 24\text{h}$ $\to$ Successfully unlinked.
    - Seeded an unrecorded file with $mtime < 24\text{h}$ $\to$ Preserved.
    - Seeded an active DB attachment file $\to$ Preserved.
    - Verified non-standard files and symlink boundaries remain unmolested.

### Workstream 2: Row-Level Locking Concurrency on Live PostgreSQL
- **Implementation**:
  - Deterministic sort of attachment IDs (`sorted_image_ids = sorted(request.image_ids)`).
  - Pessimistic locking in [`create_thread`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/content/app.py#L415): `select(Attachment)...order_by(Attachment.id).with_for_update()`.
  - Non-blocking lock skipping in [`cleanup_expired_pending_attachments`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/content/app.py#L105): `select(Attachment)...order_by(Attachment.id).with_for_update(skip_locked=True)`.
- **Validation Criteria**:
  - Verify that when thread creation locks pending attachments, cleanup workers skip or serialize without data loss or deadlocks.
- **Test Evidence**:
  - Executed [`test_concurrency_postgres.py::test_postgres_concurrent_linking_vs_cleanup`](file:///home/chocolat/Documents/Project/ForumPlatform/tests/test_concurrency_postgres.py) against live `forum_postgres` container (`postgresql+asyncpg://.../forum_content`).
  - Result: 1 passed in 0.59s. Mutual exclusion and status transition integrity verified under concurrent async transactions.

### Workstream 3: Image Decoder Assurance & Normalization
- **Implementation**:
  - Added Pillow dependency to the environment and container.
  - In [`validate_and_normalize_image`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/content/storage.py#L131):
    - Magic signature verification.
    - `img.verify()` for basic bitstream checks.
    - `img.load()` to decompress and decode every raster pixel block.
    - Dimensions check: max $8192 \times 8192$ px, max $16,000,000$ total pixels.
    - Decompression bomb defense via `Image.MAX_IMAGE_PIXELS = 16_000_000`.
    - EXIF metadata stripping and clean re-encoding into pristine byte buffers (`PNG`, `JPEG`, `WEBP`).
- **Test Evidence**:
  - `tests/test_content_service.py::test_image_upload_hardening_suite`:
    - Upload with invalid/corrupt compressed stream $\to$ rejected with `400 INVALID_IMAGE_CONTENT`.
    - Upload exceeding dimension limits ($10000 \times 10000$) $\to$ rejected with `400 INVALID_IMAGE_DIMENSIONS`.
    - Valid image $\to$ processed, EXIF stripped, sanitized raster saved to storage.

### Workstream 4: Gateway Streaming Pass-Through
- **Implementation**:
  - Replaced chunk accumulation in memory with [`streaming_request_content`](file:///home/chocolat/Documents/Project/ForumPlatform/src/gateway/forwarder.py#L106).
  - Enforces `MAX_GATEWAY_BODY_SIZE_BYTES = 5242880` (5 MB) cumulative byte counter while yielding directly to upstream HTTPX request stream.
  - Safe stream closure helper `_close_request_stream` handles partial failures without memory leaks.
- **Validation Criteria**:
  - Gateway does not buffer full multipart payloads into RAM.
  - Chunked requests exceeding 5 MB trigger immediate `413 Payload Too Large` from Gateway forwarder.
- **Test Evidence**:
  - Gateway content forwarding tests in `tests/test_content_service.py` and `tests/test_frontend_e2e.py` passed.
  - Streaming error handling verified with clean connection termination.

### Workstream 5: Cache and Privacy Consistency Audit
- **Audit Findings**:
  - In Content Service [`serve_image`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/content/app.py#L525), permissions are verified before streaming:
    - Pending images: restricted strictly to the uploader author.
    - Linked images: author or moderators/admins can access unrestricted; public users can access only if the thread is published (`is_deleted=False` and category accessible).
  - Cache Control headers:
    - Current: `Cache-Control: public, max-age=86400, immutable`.
  - **Identified Trade-off & Operational Constraint**:
    - HTTP caching proxies and browsers retain cached responses if served with `public, immutable`. If an author or moderator subsequently soft-deletes a thread, downstream browser caches that previously fetched the image will continue serving it from local cache until the 24-hour TTL expires.
    - **Resolution**: This is documented in accordance with ADR guidelines. For sensitive or private forums, cache directives should be configured to `private, no-cache, no-store`. In the current public forum configuration, 24-hour edge caching optimizes bandwidth for public media assets while authorization checks protect initial serving.

---

## 3. Test Execution Summary

| Test Suite | Scope | Result | Execution Time |
|---|---|---|---|
| `tests/test_content_service.py` | Content Service unit & integration (Uploads, Reconciliation, Decoder, Hardening) | **6 PASSED** | 4.78s |
| `tests/test_concurrency_postgres.py` | Live PostgreSQL concurrency & row locking | **1 PASSED** | 0.59s |
| `tests/test_frontend_e2e.py` | Gateway routing, CSRF, frontend integration | **8 PASSED** | 2.91s |
| `tests/test_browser_e2e.py` | Full Playwright E2E browser test suite (18 scenarios) | **18 PASSED** | 83.53s |
| `ruff check src/ tests/` | Python static analysis & style enforcement | **Clean (0 errors)** | 0.05s |
| `pyright src/gateway/forwarder.py src/services/content/` | Type checking on modified services | **Clean (0 errors)** | 8.8s |

---

## 4. Acceptance Verdict

**Verdict: PASS**

All five residual risk areas are remediated, tested, and verified on live PostgreSQL and headless browser environments without breaking changes to existing contracts or regressions across the test suite.
