# Task 027-H1-R1: Residual Risk Remediation Change Log

## Date: 2026-10-22

### Summary of Changes

Remediated five residual risk areas identified in Task 027-H1 regarding image upload lifecycle, storage reconciliation, concurrency locking, decoder verification, and gateway streaming.

---

### Files Modified & Created

#### 1. `src/services/content/config.py`
- Added storage reconciliation configuration parameters:
  - `STORAGE_RECONCILIATION_SAFETY_AGE_SECONDS: int = 86400` (24 hours default safety age).
  - `STORAGE_RECONCILIATION_BATCH_SIZE: int = 50` (batch size for database queries).
  - `STORAGE_RECONCILIATION_MAX_FILES_PER_RUN: int = 500` (upper bound of scanned files per iteration).

#### 2. `src/services/content/storage.py`
- Integrated Pillow (`PIL.Image`) for in-depth image payload decoding and normalization.
- Implemented `validate_and_normalize_image()`:
  - Validates image magic bytes signature.
  - Calls `img.verify()` and `img.load()` to parse compressed raster scanlines and prevent malformed/polyglot payload bypass.
  - Enforces `MAX_DIMENSION = 8192` and `MAX_PIXELS = 16_000_000` (`Image.MAX_IMAGE_PIXELS`).
  - Strips EXIF/metadata and re-encodes clean raster data into normalized byte streams.
- Updated `save_image()` to accept and save normalized bytes along with detected width and height.

#### 3. `src/services/content/app.py`
- **Workstream 1 (Storage Reconciliation)**:
  - Implemented `reconcile_orphaned_storage_files()`:
    - Scans upload directory using `SAFE_OBJECT_KEY_PATTERN`.
    - Skips symlinks and files outside directory boundaries.
    - Filters out files modified within the safety age window ($< 24\text{h}$).
    - Batches DB checks using `Attachment.storage_path.in_(...)`.
    - Performs an atomic pre-unlink verification check against the database before unlinking orphaned files.
- **Workstream 2 (Row-Level Locking Concurrency)**:
  - In `create_thread`, sorted attachment IDs deterministically and locked rows with `.order_by(Attachment.id).with_for_update()`.
  - In `cleanup_expired_pending_attachments`, retained `order_by(Attachment.id).with_for_update(skip_locked=True)` to serialize cleanly against thread creation without blocking or deadlocks.

#### 4. `src/gateway/forwarder.py`
- **Workstream 4 (Gateway Streaming Pass-Through)**:
  - Replaced body chunk accumulation list with an asynchronous generator `streaming_request_content()`.
  - Enforced streaming request pass-through with cumulative byte counting up to `MAX_GATEWAY_BODY_SIZE_BYTES = 5242880` (5 MB).
  - Added helper `_close_request_stream` using `contextlib.suppress(Exception)` to safely close HTTPX upstream streams when transfers are interrupted or aborted.

#### 5. `tests/test_content_service.py`
- Updated test helper images to valid RGBA PNG buffers to adhere to Pillow's strict decoder.
- Added comprehensive unit and integration tests under `test_image_upload_hardening_suite`:
  - Corrupt bitstream detection and rejection.
  - Oversized dimensions rejection.
  - Orphaned file reconciliation ($>24\text{h}$ orphan deletion, $<24\text{h}$ preservation, active attachment preservation, symlink/invalid name safety).

#### 6. `tests/test_concurrency_postgres.py` (New Test File)
- Implemented `test_postgres_concurrent_linking_vs_cleanup`:
  - Connects to real PostgreSQL (`postgresql+asyncpg://.../forum_content`).
  - Seeds pending attachment and concurrently executes thread attachment linking vs pending attachment cleanup.
  - Verifies deterministic mutual exclusion via PostgreSQL row-level locks without race conditions or integrity faults.

#### 7. `tests/test_browser_e2e.py`
- Fixed feed navigation in `test_browser_thread_deletion_conflict_modal` to ensure reliable thread discovery across paginated test runs.
- Verified full suite of 18 Playwright scenarios passing.

#### 8. Documentation Deliverables
- Created `docs/audit/task_027_h1_r1_validation_report.md`.
- Created `docs/audit/task_027_h1_r1_change_log.md`.
