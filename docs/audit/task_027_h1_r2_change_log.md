# Task 027-H1-R2: Change Log

Date: 2026-10-22
Branch: `master`
Baseline HEAD: `d2ea0c333eeb42dc784011f2d116f28bf61f365b`

## 1. Summary

This task independently re-audited five image-upload findings, corrected the parts that were wrong
or incomplete in the pre-existing working tree, and added regression tests that assert database and
filesystem state. No schema migration, new service, new scheduler, authorization redesign, or Task 028
work was introduced.

## 2. Source changes

### `src/services/content/storage.py`

- Rewrote `validate_and_normalize_image` around a full Pillow decode:
  - byte-size limit before decode and after re-encode;
  - signature + decoded-format agreement;
  - `Image.MAX_IMAGE_PIXELS = 16_000_000`, `DecompressionBombWarning`/`Error` treated as rejection;
  - width/height 8192 and 16,000,000 pixel limits;
  - `img.load()` decodes the complete compressed bitstream;
  - `ImageOps.exif_transpose` before metadata-stripping re-encode;
  - clean JPEG/PNG/WebP output, explicit format allow-list.
- Kept `SAFE_OBJECT_KEY_PATTERN` (`uploads/images/<32 lowercase hex>.<jpg|png|webp>`), `sniff_image_type`,
  `ensure_storage_dir`, `generate_object_key`, `get_absolute_file_path`.
- Removed the old structural-only PNG/JPEG/WebP validators.

### `src/services/content/app.py`

- `create_thread`: locks eligible attachment rows **before** thread/tag/outbox creation, ordered by
  `Attachment.id`; invalid attachment now returns 400 with no committed thread.
- `cleanup_expired_pending_attachments`: ordered `FOR UPDATE SKIP LOCKED`; commits `deleted` before
  any file deletion; deletes files only after commit.
- Added `_remove_stored_file` best-effort helper with sanitized logging.
- Rewrote `reconcile_orphaned_storage_files` to:
  - scan only the configured `images` root;
  - skip symlinks (checked before stat and before unlink);
  - accept only server-generated object keys;
  - honor the 24 h mtime grace;
  - batch-query metadata, then recheck per file before unlink;
  - treat missing metadata or `status='deleted'` as eligible, preserve `pending`/`linked`;
  - abort on database errors; bound by `batch_size`; idempotent.
- Worker loop now runs reconciliation on `attachment_reconciliation_interval_seconds` instead of on
  every cleanup tick.
- `upload_image`: fsyncs the file before metadata insert; on write failure unlinks and returns
  `500 STORAGE_ERROR`; on DB failure uses `_remove_stored_file` and leaves recovery to reconciliation.
- `serve_image`: cache policy now public only for a linked attachment on a **published** thread;
  pending/hidden/deleted use `no-store, private`.
- Replaced deprecated `Query(enum=[...])` with `typing.Literal` for `sort` so the app boots under the
  FastAPI version installed by a fresh Docker build.

### `src/gateway/forwarder.py`

- Replaced in-memory `body_chunks` + `b"".join(...)` with an async `_stream_request_body` generator
  passed directly to `client.build_request(..., content=...)`.
- Added cumulative 5 MiB limit enforcement during transmission (`GatewayPayloadTooLargeError`).
- Early `Content-Length` check remains a hint only; the header is stripped from outgoing requests and
  HTTPX recomputes chunked framing.
- Added `ClientDisconnect` handling (499) and request-stream cleanup on timeout/network/abort.
- Upstream responses remain streamed with `aiter_raw()`; `Set-Cookie` multi-value forwarding retained.

### `src/services/moderation/app.py`

- Environment-compatibility fix: replaced `Query(enum=[...])` with `typing.Literal` for
  `status_filter` and `target_type`. This prevents a fresh Docker image with FastAPI 0.159.1 from
  failing at import. No moderation behavior change.

### `src/services/content/config.py`

- Retained the pre-existing uncommitted reconciliation settings:
  `attachment_reconciliation_interval_seconds`, `attachment_reconciliation_safety_age_seconds`,
  `attachment_reconciliation_batch_size` (all default 86400 / 86400 / 100).

## 3. Dependency / build changes

### `pyproject.toml`

- Added `pillow>=11.0.0` to runtime dependencies. Resolved to Pillow 13.0.0 in the venv and Docker
  image. The existing `Dockerfile` already installs the project with `pip install .`, so no Dockerfile
  edit was required. No lockfile exists in this repository.

No migration files were added or modified: the `content.attachments` schema and
`ix_attachments_orphan` index already support the lifecycle.

## 4. Test changes

### Added

- `tests/test_image_validation.py` — full decode/normalization tests: valid PNG/JPEG/WebP, corrupt
  compressed bitstreams, plausible-header/garbage-IDAT PNG, EXIF orientation/stripping, width/pixel
  boundaries, byte limit, non-image payloads.
- `tests/test_attachment_concurrency_pg.py` — real `create_thread` endpoint vs real
  `cleanup_expired_pending_attachments` on live PostgreSQL; asserts both winner states, no 500, no
  thread referencing a deleted attachment; verifies physical file presence/absence.

### Modified

- `tests/test_content_service.py`
  - Added corruption/MIME-extension tests, invalid-attachment rollback test, DB-failure + failed-unlink
    reconciliation test, cleanup failed-unlink reconciliation test, cleanup commit-rollback test,
    and comprehensive reconciliation safety/bounds/retry test.
  - Extended privacy tests for hidden-thread cache headers, deleted thread, and deleted attachment.
  - Updated fixture PNGs to clean RGB 1×1 images and assertions to accept normalized output.
  - Added `time`, `select`, `func`, and `Attachment` imports for the new state assertions.
- `tests/test_gateway.py`
  - Added instrumented-HTTPX streaming tests (below/at/above limit, no/misleading/oversized
    Content-Length, multipart + cookie + CSRF preservation, client disconnect, upstream timeout).
- `tests/test_browser_e2e.py`
  - Replaced the opaque 1×1 PNG fixture with a clean RGB PNG compatible with full decoding.

### Preserved but not authored here

- `tests/test_concurrency_postgres.py` (pre-existing untracked concurrent work) was left untouched and
  executed as part of the final test command.
- `tests/test_attachment_concurrency.py` existed at the start of this task and disappeared during it;
  it is not deleted or recreated by this task because it may be concurrent user/agent work.

## 5. Documentation

### Added

- `docs/audit/task_027_h1_r2_independent_audit.md`
- `docs/audit/task_027_h1_r2_validation_report.md`
- `docs/audit/task_027_h1_r2_change_log.md` (this file)

### Pre-existing, not modified

- `docs/audit/task_027_h1_r1_validation_report.md`
- `docs/audit/task_027_h1_r1_change_log.md`

These R1 documents appeared as untracked files during the task and do not match the checkout (for
example `Attachment.storage_path` and `save_image()` do not exist). They are preserved and explicitly
not used as evidence.

## 6. Files modified / added in this task

Modified:

- `pyproject.toml`
- `src/gateway/forwarder.py`
- `src/services/content/app.py`
- `src/services/content/config.py` (retained pre-existing edits)
- `src/services/content/storage.py`
- `src/services/moderation/app.py`
- `tests/test_browser_e2e.py`
- `tests/test_content_service.py`
- `tests/test_gateway.py`

Added:

- `tests/test_attachment_concurrency_pg.py`
- `tests/test_image_validation.py`
- `docs/audit/task_027_h1_r2_independent_audit.md`
- `docs/audit/task_027_h1_r2_validation_report.md`
- `docs/audit/task_027_h1_r2_change_log.md`

No commits or pushes were made. All changes remain in the working tree.
