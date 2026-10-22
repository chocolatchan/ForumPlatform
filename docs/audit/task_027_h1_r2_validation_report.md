# Task 027-H1-R2: Validation Report

Date: 2026-10-22
Checkout: `/home/chocolat/Documents/Project/ForumPlatform`
Branch: `master`
Baseline HEAD / `origin/master`: `d2ea0c333eeb42dc784011f2d116f28bf61f365b`

## 1. Environment

| Component | Observed |
| :--- | :--- |
| Host | Linux |
| Test interpreter | `./venv/bin/python` Python 3.14.8 |
| Venv FastAPI | 0.143.1 |
| Docker runtime FastAPI | 0.159.1 (fresh `pip install .` inside image) |
| Pillow | 13.0.0 (venv and container) |
| Redis / DB | live `forum_postgres` (PostgreSQL 16), `forum_redis` containers |
| Gateway / Content | rebuilt `forum_api_gateway`, `forum_content_service` containers, both healthy |
| Test DB for Content unit tests | SQLite file `/tmp/test_forum_content_task008.db` |
| Test DB for concurrency | live PostgreSQL `postgresql+asyncpg://forum_admin:***@localhost:5432/forum_content` |

The Docker build installed a newer FastAPI (0.159.1) than the venv (0.143.1). That exposed a
pre-existing `Query(enum=[...])` incompatibility at Content/Moderation import time. It was fixed by
using `typing.Literal` (see change log). This is recorded as an environment-compatibility fix, not as
one of the five findings.

## 2. Executed test commands and results

### 2.1 Backend regression package (SQLite unit/integration + live PostgreSQL concurrency)

```bash
./venv/bin/pytest \
  tests/test_content_service.py \
  tests/test_gateway.py \
  tests/test_image_validation.py \
  tests/test_attachment_concurrency_pg.py \
  tests/test_concurrency_postgres.py \
  tests/test_moderation_service.py \
  tests/test_config.py -q
```

Result: **47 passed in 4.02s**, zero failures, zero skips.

Scope caveat: the Content Service unit/integration tests use SQLite. They prove handler/state
behavior, not PostgreSQL row locking. The row-locking evidence comes only from
`tests/test_attachment_concurrency_pg.py` (and the pre-existing `tests/test_concurrency_postgres.py`)
against the live PostgreSQL container.

### 2.2 PostgreSQL race (real endpoint + real cleanup function)

```bash
./venv/bin/pytest tests/test_attachment_concurrency_pg.py -q -s
```

Result: **1 passed in 0.69s**.

Observed winners in the final run (8 iterations, alternating scheduling bias):

```text
postgres race outcomes: ['linking', 'cleanup', 'linking', 'cleanup',
                         'linking', 'cleanup', 'linking', 'cleanup']
```

Each iteration asserted one of exactly two committed states:

- linking: HTTP 201, attachment `linked` with `thread_id`, a single published thread with that id,
  file present;
- cleanup: HTTP 400 `INVALID_ATTACHMENT`, attachment `deleted`, no thread row, file absent.

No HTTP 500, deadlock, double success, or published thread referencing a deleted attachment was
observed.

### 2.3 Frontend integration

```bash
./venv/bin/pytest tests/test_frontend_e2e.py -q
```

Result: **8 passed in 1.41s**.

### 2.4 Live browser Playwright E2E

```bash
./venv/bin/pytest tests/test_browser_e2e.py -q
```

Result: **18 passed in 41.40s** against the live Docker stack. This includes the image upload,
preview, thread publish, and attachment rendering journey.

### 2.5 Static analysis

```bash
./venv/bin/ruff check src/ tests/
# All checks passed!

./venv/bin/pyright --pythonpath venv/bin/python \
  src/ tests/test_content_service.py tests/test_gateway.py \
  tests/test_image_validation.py tests/test_attachment_concurrency_pg.py
# 0 errors, 0 warnings, 0 informations
```

A pyright run without `--pythonpath venv/bin/python` reports unresolved third-party imports because
that interpreter does not see the project venv. That invocation is not used as evidence.

## 3. Live Docker validation

### 3.1 Build / startup

```bash
docker compose build gateway content-service
docker compose up -d --no-deps gateway content-service
curl http://localhost:8000/health/live   # 200
curl http://localhost:8002/health/live   # 200
docker exec forum_content_service python -c "import PIL, fastapi; ..."
# Pillow 13.0.0 FastAPI 0.159.1
```

The Content Service log showed `Application startup complete` and background outbox/cleanup workers
started; no startup exceptions after the FastAPI compatibility fix.

### 3.2 Real upload path through Gateway (multipart, declared length)

A real account/session was seeded in `forum_users`, then the following were sent through
`http://localhost:8000/api/v1`:

| Scenario | Result |
| :--- | :--- |
| Valid PNG multipart upload | `201`, `mime_type=image/png`, object key `.png` |
| Fetch pending image as uploader through Gateway | `200`, `image/png`, `Cache-Control: no-store, private`, PNG bytes |
| 2 MiB + 10 byte file (Content Service limit) | `413 PAYLOAD_TOO_LARGE` from Content Service |
| 6 MiB chunked body with no trusted Content-Length | `413 PAYLOAD_TOO_LARGE` from Gateway |

### 3.3 Genuine chunked multipart streaming

A multipart body was generated in memory but sent as an **async chunk generator** (37-byte chunks, no
Content-Length supplied by the client):

```text
chunked_multipart_status: 201
chunked_multipart_mime: image/png
chunked_get: [200, image/png, true]
bad_csrf_status: 403
```

This proves the Gateway constructed and transmitted a streaming request that the live Content Service
parsed as multipart, while preserving the session cookie and CSRF header (bad CSRF was still rejected).
It is not proof of a particular RSS number; see section 5.

### 3.4 Declared oversize rejection before body transmission

A raw TCP request with `Content-Length: 6291456` and no body returned immediately:

```text
HTTP/1.1 413 Content Too Large
x-request-id: a4ccbdaf-e4e7-421d-97ed-00127418d50e
{"error":{"code":"PAYLOAD_TOO_LARGE", ...}}
```

### 3.5 Filesystem observation

After the live uploads, `/app/uploads/images` in `forum_content_service` contained the newly generated
`{32-hex}.png` files with the normalized sizes (69–130 bytes for the 1×1 fixtures), confirming the
filesystem write path and UUID-based layout were preserved.

## 4. What each test actually proves

| Test | Proves | Does not prove |
| :--- | :--- | :--- |
| `test_cleanup_unlink_failure_is_recovered_by_reconciliation` | `deleted` metadata + failed unlink is reclaimed after recheck | PostgreSQL commit semantics (SQLite run) |
| `test_cleanup_rollback_preserves_pending_metadata_and_file` | no unlink occurs before commit; rollback keeps pending row + file | crash at the exact commit boundary |
| `test_db_failure_unlink_failure_is_recovered_by_reconciliation` | missing metadata orphan is discoverable and removed after grace | immediate recovery latency (grace is 24 h by design) |
| `test_invalid_attachment_does_not_commit_partial_thread` | invalid link returns 400 with zero committed thread rows | lock contention (single transaction) |
| `test_attachment_concurrency_pg.py` | real `create_thread` + real cleanup serialize on PG row locks; both winner paths valid | distributed multi-instance crash timing |
| `test_gateway_streams_request_body_without_buffering` | HTTPX receives an async iterator; upstream sees a chunk before ASGI receive finishes; no Content-Length forwarded; chunked framing | live kernel/socket streaming and RSS |
| live 3.3 / 3.4 | streaming path works end-to-end through Docker; 413 at/over limits | memory profile |
| `test_image_validation.py` | full decode rejects corrupt PNG/JPEG/WebP bitstreams; dimension/pixel boundaries; metadata stripping | decoder CVE resistance |
| Browser E2E | upload → preview → publish → render works in a real browser | high-concurrency behavior |

## 5. Unexecuted / limited validation (explicit)

- **No full `pytest tests/` run.** Only the suites listed in section 2 were executed. Load tests,
  backup/restore, and some cross-service suites were not re-run in this task.
- **No migration upgrade/downgrade run.** No schema change was introduced; the existing
  `content.attachments` table and `ix_attachments_orphan` index are unchanged.
- **No RSS/heap profiler.** Streaming is demonstrated by request-construction inspection, an
  instrumented HTTPX `send()`, and a live chunked multipart upload — not by measuring process memory.
- **Moderation container not rebuilt.** The `Literal` compatibility fix was unit-tested in-process;
  the running moderation container still uses its previous image.
- **Race distribution is scheduling-dependent.** The final PG run exercised both winners by biasing
  even iterations toward linking. An earlier unbiased run produced eight cleanup wins. Neither run
  produced an invalid state.
- **Already-cached public bytes cannot be revoked.** Browser/CDN caches populated while a thread was
  published may continue to show image bytes after hiding/deletion; this is inherent and documented,
  not a failed server-side check.

## 6. Failures and skips

- No failing tests in the executed commands.
- No skipped tests in the executed commands. The PostgreSQL test has a `pytest.skip` guard for an
  unavailable database; PostgreSQL was available and the test ran.

## 7. Required verdict inputs

| Finding | Verdict | Required regression evidence |
| :--- | :--- | :--- |
| P1-A | RESOLVED | rollback/unlink-failure/reconciliation tests + live file observation |
| P1-B | RESOLVED | real-endpoint PostgreSQL race with both outcomes, invalid-link rollback test |
| P1-C | RESOLVED | instrumented streaming test + live chunked multipart + live 5 MiB rejection |
| P1-D | RESOLVED | full-decode unit tests for corrupt PNG/JPEG/WebP + live upload |
| P2-A | RESOLVED | reconciliation safety/bounds/retry test + live container filesystem |

Overall: **PASS WITH CONDITIONS** (conditions listed in the independent audit and section 5).
No critical consistency, concurrency, authorization, or resource-exhaustion risk remains unresolved
within the scope of the five findings.
