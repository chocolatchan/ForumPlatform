# Task 027-H1-R2: Independent Repository Audit and Remediation

Date: 2026-10-22
Repository: `chocolatchan/ForumPlatform` (local checkout `/home/chocolat/Documents/Project/ForumPlatform`)
Branch: `master`

## 1. Repository integrity and inspection scope

### 1.1 Baseline actually observed

| Item | Observed value |
| :--- | :--- |
| Working directory | `/home/chocolat/Documents/Project/ForumPlatform` |
| Branch | `master` tracking `origin/master` |
| Local HEAD at start | `d2ea0c333eeb42dc784011f2d116f28bf61f365b` (`027-H1`) |
| `origin/master` after `git fetch` | `d2ea0c333eeb42dc784011f2d116f28bf61f365b` |
| Remote | `git@github.com:chocolatchan/ForumPlatform.git` |

`git fetch origin` completed successfully and `origin/master` resolved to the same commit as local
`HEAD`. No remote divergence existed at the start of the task.

### 1.2 Pre-existing uncommitted work (preserved)

The checkout already contained uncommitted changes from an earlier, unfinished attempt:

- Modified: `src/gateway/forwarder.py`, `src/services/content/app.py`,
  `src/services/content/config.py`, `src/services/content/storage.py`,
  `tests/test_browser_e2e.py`, `tests/test_content_service.py`
- Untracked at start: `tests/test_attachment_concurrency.py`,
  `tests/test_concurrency_postgres.py`

Those changes were not reset, cleaned, stashed, or overwritten. They were inspected, retained where
correct, and corrected where they did not satisfy the requirements. During the task, additional
untracked files appeared in the checkout (`docs/audit/task_027_h1_r1_*.md`,
`tests/test_concurrency_postgres.py`) and `tests/test_attachment_concurrency.py` disappeared. These
are treated as concurrent user/agent work, are not deleted by this task, and are called out in the
final status section.

### 1.3 Notable discrepancy in prior documentation

`docs/audit/task_027_h1_r1_change_log.md` claims an implementation that does not exist in this
checkout:

- it references `Attachment.storage_path`, while the model has `Attachment.object_key`;
- it references `STORAGE_RECONCILIATION_*` settings, while `ContentSettings` uses
  `attachment_reconciliation_*`;
- it references `save_image()`, which does not exist;
- it claims the Gateway already streamed with an async generator, while the source at audit time
  still accumulated `body_chunks` and called `b"".join(body_chunks)`.

The R1 report is therefore not accepted as evidence. Every finding below was re-derived from the
actual source and re-tested.

### 1.4 Relevant implementation map

| Concern | File / function |
| :--- | :--- |
| Upload, metadata insert, serving | `src/services/content/app.py::upload_image`, `serve_image` |
| Attachment linking | `src/services/content/app.py::create_thread` |
| Expiration cleanup / reconciliation / worker | `src/services/content/app.py::cleanup_expired_pending_attachments`, `reconcile_orphaned_storage_files`, `_pending_attachment_cleanup_loop`, `lifespan` |
| Image decoding | `src/services/content/storage.py::validate_and_normalize_image` |
| Models / constraints | `src/services/content/models.py::Attachment` |
| Gateway forwarding | `src/gateway/forwarder.py::forward_request` |
| Gateway client lifecycle | `src/gateway/app.py::lifespan` |
| Configuration | `src/services/content/config.py` |
| Unit / integration tests | `tests/test_content_service.py`, `tests/test_image_validation.py`, `tests/test_gateway.py`, `tests/test_attachment_concurrency_pg.py` |
| Live stack | `docker-compose.yml`, `Dockerfile` |

## 2. Method

For each reported finding the audit recorded: the exact function, the observed behavior, the
conditions needed to reproduce it, what existing tests actually assert, severity, remediation, and a
verdict. A finding was only marked `RESOLVED` when a code change and a regression test that observes
database/filesystem state (not only an HTTP status) supported it. Findings that could not be
reproduced were not patched merely to match the hypothesis.

## 3. Finding-by-finding audit

### P1-A: Filesystem and database state during cleanup / failed uploads

**Status: CONFIRMED — RESOLVED (with one documented retention limitation).**

#### Observed behavior at audit time

`upload_image` wrote the file, then inserted metadata. On database failure it attempted
`target_path.unlink(missing_ok=True)` and logged failures. That part was acceptable because a file
left with no metadata row is discoverable by reconciliation.

The cleanup path was not acceptable:

```python
async with content_db.transaction() as session:
    ... select expired pending ... with_for_update(skip_locked=True)
    for att in expired_attachments:
        att.status = "deleted"
        file_path.unlink()          # <-- physical delete INSIDE the transaction
```

Two defects followed:

1. **Deletion before commit.** If `unlink()` succeeded and the transaction later rolled back, the
   attachment returned to `pending` while its bytes were already gone. A subsequent successful
   thread link would publish a thread referencing a missing file.
2. **No recovery after unlink failure.** If `unlink()` raised, the transaction still committed
   `status = 'deleted'`. The old reconciliation only deleted files whose `object_key` had *no*
   metadata row at all, so a `deleted` row kept its orphan file forever.

The existing H1 test only asserted `not old_file_path.exists()` after a successful cleanup. It did
not simulate unlink failure or commit rollback, so it did not prove either direction of consistency.

#### Reproduction conditions

- Cleanup transaction commits `deleted`, then `unlink()` raises `OSError` (permissions, transient
  filesystem error): file remains with metadata `deleted`, never reclaimed by the old reconciler.
- Cleanup unlinks successfully, then the transaction fails on commit: file is gone while the row is
  still `pending`; a later link succeeds and serves 404.

#### Remediation

- File deletion moved **after** the cleanup transaction commits. A failure or crash between commit
  and unlink leaves `deleted` metadata plus a file, a state reconciliation can safely repair.
- Added `_remove_stored_file(object_key)` as the single best-effort deletion helper used by upload
  failure cleanup and expiration cleanup. It returns `False` on `OSError`; callers never pretend the
  delete succeeded.
- Upload writes now `flush()` + `fsync()` before metadata insert, and a write failure unlinks the
  partial file and returns `500 STORAGE_ERROR`.
- Database-failure cleanup uses the same helper. If that unlink fails, no metadata row exists, so the
  reconciler discovers the file after the grace period.
- Reconciliation now treats metadata `status = 'deleted'` (in addition to absent metadata) as
  terminal and eligible for physical removal, after an immediate per-file recheck.

#### Evidence

- `tests/test_content_service.py::test_db_failure_unlink_failure_is_recovered_by_reconciliation`:
  database insert forced to fail, immediate unlink forced to fail; asserts no row exists, file exists,
  then reconciliation removes it.
- `tests/test_content_service.py::test_cleanup_unlink_failure_is_recovered_by_reconciliation`:
  cleanup commit succeeds, unlink forced to fail; asserts row is `deleted`, file exists, then
  reconciliation removes it.
- `tests/test_content_service.py::test_cleanup_rollback_preserves_pending_metadata_and_file`:
  transaction commit forced to fail; asserts file still exists and row is still `pending`.
- `tests/test_content_service.py::test_reconciliation_safety_bounds_and_retry` covers retry after
  unlink failure and database-unavailable safe abort.

#### Residual limitation

Soft-deleting a thread (`delete_thread`) does not delete its attachments or files. Images correctly
return 404 because serving requires a `published` thread, but bytes remain on disk until a future
hard-purge feature. This is a retention/storage-growth limitation, not a consistency violation, and
it is intentionally not conflated with orphan reconciliation.

---

### P1-B: Race between attachment linking and expiration cleanup

**Status: CONFIRMED — RESOLVED.**

#### Observed behavior at audit time

`create_thread` did lock attachments (`order_by(Attachment.id).with_for_update()`), but the lock was
taken **after** the thread and tags had been inserted and flushed. More importantly, the invalid
attachment branch used `return error_response(...)` **inside** `async with content_db.transaction()`.
`AsyncSession.begin()` commits on normal exit, so an invalid attachment could return HTTP 400 while
committing a published thread row (and no outbox event) — a real half-created state.

Cleanup also had no deterministic lock order (`.limit()` without `.order_by()`) and deleted files
inside its transaction (see P1-A), so "cleanup wins" could still destroy bytes needed by a
concurrent link that had not yet failed.

Existing SQLite tests proved only sequential second-link rejection. The pre-existing PostgreSQL test
simulated the locking logic in a separate transaction rather than calling the real endpoint, so it did
not prove the actual handler semantics.

#### Reproduction conditions

- Call `POST /threads` with an image id that is missing/foreign/already linked: response is 400 but a
  `content.threads` row is committed.
- Race `POST /threads` and cleanup on the same expired pending attachment: both can report success in
  the old ordering because cleanup deleted the file before its commit was guaranteed.

#### Remediation

- Attachment rows are selected and locked **before** creating the thread. An invalid attachment now
  returns 400 before any thread/tag/outbox mutation exists.
- Lock acquisition is ordered by `Attachment.id` in both linking and cleanup.
- Cleanup uses `order_by(Attachment.id).with_for_update(skip_locked=True)` and commits the `deleted`
  transition before touching the filesystem.
- With READ COMMITTED + `FOR UPDATE`, a linker blocked behind cleanup re-evaluates the updated row
  and sees `status != 'pending'`, so it fails with `INVALID_ATTACHMENT`; it cannot publish a thread
  referencing a deleted attachment.
- Safe errors remain `400 INVALID_ATTACHMENT` for expired/deleted/foreign/already-linked ids.

#### Evidence

- `tests/test_content_service.py::test_invalid_attachment_does_not_commit_partial_thread` asserts the
  thread count is zero after the 400.
- `tests/test_attachment_concurrency_pg.py::test_postgres_real_linking_vs_cleanup_race` calls the
  actual FastAPI endpoint and the actual cleanup function eight times against live PostgreSQL. Each
  iteration asserts exactly one committed outcome:
  - linking: 201, `linked`, `thread_id` set, published thread matches, file exists;
  - cleanup: 400 `INVALID_ATTACHMENT`, `deleted`, no thread row, file absent.
  No 500/deadlock/inconsistent state occurred. The observed run produced eight `cleanup` wins because
  the cleanup coroutine was scheduled first; this still exercises the serialization path, and the
  deterministic invalid/expired cases cover the linking-wins path.

---

### P1-C: Gateway request-body buffering

**Status: CONFIRMED — RESOLVED (streaming behaviour, with documented mid-stream abort semantics).**

#### Observed behavior at audit time

`forward_request` accumulated every chunk in `body_chunks`, then executed `body = b"".join(...)` and
passed `content=body` to HTTPX. It checked `Content-Length` early and counted chunks, but the whole
body (up to 5 MiB) was materialised per request. The H1 report's claim of "without buffering into
memory" described only the size ceiling, not the transmission path.

#### Reproduction conditions

Any request through the Gateway: `httpx.Request.content` is a single `bytes` object rather than an
async iterator. Peak Gateway memory grows with the full request body for every concurrent request.

#### Remediation

- The incoming body is now passed to `client.build_request(..., content=_stream_request_body(...))`,
  an async generator that counts bytes cumulatively and raises `GatewayPayloadTooLargeError` when
  the 5 MiB limit is crossed.
- `Content-Length` is removed from outgoing headers; HTTPX/httpcore recompute chunked framing. The
  declared length is used only as an early rejection hint, never as the authoritative admission
  control, so a missing, invalid, or falsely small header cannot bypass the cumulative limit.
- Multipart `Content-Type`/boundary, cookies, CSRF headers, and route/query behaviour are preserved.
- Oversize crossed mid-stream aborts the HTTPX request stream; the partial body is not retried and no
  upstream response is forwarded. The Gateway returns 413. Because the upstream never receives a
  complete body, it cannot commit a partial upload. This is the explicit answer to "what happens if
  the limit is crossed after transmission begins".
- Client disconnect is caught as `ClientDisconnect` and returns 499 after closing the request stream;
  timeouts/network failures close the stream and return 504/503. Upstream responses are still streamed
  via `aiter_raw()`, not buffered.
- The Content Service retains its independent 2 MiB file limit.

#### Evidence

- `tests/test_gateway.py::test_gateway_streams_request_body_without_buffering` uses an instrumented
  HTTPX client whose `send()` reads the request stream directly (unlike `MockTransport`, which calls
  `aread()` and would hide buffering). It asserts the upstream sees the first chunk while the ASGI
  receive callable has **not** finished, that no `Content-Length` is forwarded, and that framing is
  chunked.
- Additional tests cover exactly at limit, over limit with no `Content-Length`, misleading small
  `Content-Length`, declared oversize rejection without reading the body, multipart/header/cookie
  preservation, client disconnect, and upstream timeout without retry.
- Live evidence through the rebuilt Docker Gateway/Content stack is recorded in the validation report.

#### Residual limitation

If the limit is crossed after bytes have reached the upstream socket, the upstream may observe an
aborted request. It must not act on partial bodies; this stack's FastAPI/uvicorn parsing does not
commit a request until the body is complete. No automatic retry is performed.

---

### P1-D: Full image decoding

**Status: CONFIRMED — RESOLVED.**

#### Observed behavior at audit time

The original `validate_image_content` parsed only container structures: PNG IHDR/IEND, JPEG
SOI/EOI + SOF dimensions, and WebP RIFF/VP8 headers. A file with valid structural markers but corrupt
compressed pixel data passed. At the start of this task an uncommitted partial change had introduced
Pillow and `validate_and_normalize_image`, but:

- Pillow was not declared in `pyproject.toml`, so Docker builds would not install it;
- decompression-bomb warnings were not converted to rejection;
- the normalized output size was not checked against the 2 MiB database constraint;
- no tests covered corrupt JPEG/WebP bitstreams, pixel-count boundaries, or metadata stripping.

#### Reproduction conditions

A PNG with a real signature/IHDR and garbage IDAT plus a syntactically valid IEND; a JPEG truncated
inside the entropy-coded scan but ending with EOI; a WebP with a valid RIFF/VP8 header and corrupt
payload. All passed the old structural parser.

#### Remediation

- Pillow (`pillow>=11.0.0`, resolved to 13.0.0 in the venv) is now a declared runtime dependency and
  is therefore installed by the existing Dockerfile `pip install .` step. No new image/service was
  added.
- `validate_and_normalize_image`:
  - enforces the byte limit before decoding;
  - sniffs signatures and requires the decoded `Image.format` to agree;
  - sets `Image.MAX_IMAGE_PIXELS = 16_000_000` and converts
    `Image.DecompressionBombWarning`/`Error` into rejection;
  - enforces `width <= 8192`, `height <= 8192`, and `width*height <= 16_000_000`;
  - calls `img.load()` to decode the complete compressed bitstream;
  - applies EXIF orientation (`ImageOps.exif_transpose`) and re-encodes without EXIF/metadata;
  - rejects if the normalized bytes exceed 2 MiB.
- Stored extension, stored MIME, and served MIME all derive from the sniffed/decoded format, never
  from the client filename or `Content-Type`. The approved list is explicit: JPEG, PNG, WebP.

#### Evidence

- `tests/test_image_validation.py` (new): valid PNG/JPEG/WebP decode and normalize; corrupt
  compressed data for all three formats; PNG with plausible IHDR and garbage IDAT; EXIF orientation
  and metadata stripping; width boundary (8192 accepted, 8193 rejected); pixel boundary (16,000,000
  accepted, 16,384,000 rejected); oversized bytes; non-image payloads.
- `tests/test_content_service.py::test_client_extension_and_mime_are_not_trusted`: PNG bytes named
  `lies.jpg` with `Content-Type: image/jpeg` are stored as `image/png` with a `.png` object key.
- Existing upload lifecycle and hardening tests still pass with normalized output.

---

### P2-A: Filesystem-to-database reconciliation

**Status: CONFIRMED — RESOLVED.**

#### Observed behavior at audit time

An uncommitted reconciler existed, but it had the following gaps:

- it only treated files with **zero** metadata as orphans, so `deleted` rows with failed unlinks were
  permanent leaks (see P1-A);
- `attachment_reconciliation_interval_seconds` was defined but unused (reconciliation ran on every
  cleanup tick);
- the tests covered only one old orphan, one recent file, and one invalid filename; they did not
  cover database failure, unlink failure, symlink escape, live-metadata preservation, deleted-metadata
  cleanup, bounding, or idempotence.

#### Reproduction conditions

Create a file older than the grace period with no row, a file with a `deleted` row, a symlink with a
valid-looking name, and a live `linked` row; then run the reconciler and inspect the filesystem.

#### Remediation

The reconciler was retained but corrected and hardened:

- scans only `<storage_local_dir>/images`;
- ignores symlinks before any stat/open and rechecks before unlink;
- only accepts `^uploads/images/[a-f0-9]{32}\.(jpg|png|webp)$` (the exact server-generated key shape);
- ignores files whose mtime is within the 24 h safety age;
- queries metadata for the whole candidate batch, then rechecks each file immediately before unlink;
- treats metadata absent or `status='deleted'` as eligible; any other status (`pending`, `linked`) is
  preserved;
- any database error aborts the scan with zero deletions;
- processes at most `batch_size` candidates per run and is idempotent;
- runs from the existing Content Service lifespan cleanup loop at
  `attachment_reconciliation_interval_seconds` (default 24 h), not a new scheduler/service;
- shares the same state model as P1-A: `deleted` metadata is the durable recovery marker.

#### Evidence

`tests/test_content_service.py::test_reconciliation_safety_bounds_and_retry` seeds five old orphans,
a recent file, a non-conforming filename, a symlink to an outside file, and a live `linked`
attachment. It asserts: DB-unavailable scan deletes nothing; unlink failure deletes nothing and is
retried; bounded batches eventually remove only the five orphans; subsequent scans return 0; live,
recent, unknown-name, symlink, and outside-target files remain. The cleanup-unlink test additionally
proves `deleted`-metadata files are reclaimed.

---

## 4. Authorization and cache regression review

Reviewed `serve_image` and its dependency `get_optional_current_user`.

| State | Observed behavior | Verdict |
| :--- | :--- | :--- |
| `pending` | Only the uploader may read; anonymous/stranger get uniform 404. | Correct |
| `linked` + thread `published` | Public read; `Cache-Control: public, max-age=86400`. | Correct |
| `linked` + thread `hidden` | Thread author, moderator, admin only; others 404. | Correct |
| `linked` + thread `deleted` / no thread | Uniform 404. | Correct |
| attachment `deleted` | Uniform 404. | Correct |
| `linked` + thread hidden/deleted | **Fixed in this task** to `Cache-Control: no-store, private` (previously any `linked` attachment got public max-age, including hidden threads). | Correct |

Authorization is evaluated from current database state on every request. No signed URLs or
authorization redesign was introduced.

**Unavoidable caching caveat:** a browser/CDN that already cached bytes while a thread was published
may continue to display them after the thread becomes hidden/deleted. Server-side 404 responses and
`no-store` on subsequent responses cannot revoke bytes already stored by a client. This is documented,
not claimed as solvable by headers.

## 5. Unresolved questions / residual risks

1. Soft-deleted threads retain attachment rows and bytes (retention only; no authorization leak).
2. Mid-stream Gateway oversize aborts the upstream request after a prefix may have been sent; safe
   only because the upstream does not commit incomplete bodies. This is documented and tested by
   observing the abort, not by claiming zero bytes reached the socket.
3. No distributed transaction exists between PostgreSQL and the filesystem. Recovery is the explicit
   `pending → deleted → reconcile` lifecycle plus grace-period orphan scanning, as required.
4. The pre-existing R1 documentation does not match the checkout and was not used as evidence.

## 6. Verdicts

| Finding | Independent verdict |
| :--- | :--- |
| P1-A filesystem/database consistency | **RESOLVED** (thread-deletion retention documented) |
| P1-B linking vs cleanup race | **RESOLVED** |
| P1-C Gateway body buffering | **RESOLVED** |
| P1-D full image decoding | **RESOLVED** |
| P2-A filesystem reconciliation | **RESOLVED** |

Overall: **PASS WITH CONDITIONS** pending the live/Browser results recorded in the validation report.
The conditions are the documented retention limitation, the mid-stream abort contract, and the
inability to revoke already-cached public bytes. None is a critical consistency, concurrency,
authorization, or resource-exhaustion defect.
