# Task 027: Image Upload Lifecycle Architecture & Design

## 1. Overview and Problem Statement
In Task 024-R1-F and Task 025 baseline audits, `POST /api/v1/uploads/images` was cataloged as returning `501 Not Implemented`. In addition, while the database schema `content.attachments` and the OpenAPI schema `Attachment` were already modeled, there was no functional image upload pipeline, persistent storage strategy, or frontend UI attachment workflow.

Task 027 implements the complete end-to-end image upload lifecycle across:
1. **API Gateway (`forum_api_gateway`)**: Routes `/api/v1/uploads/images` directly to Content Service (`8002`).
2. **Content Service (`forum_content_service`)**:
   - `POST /uploads/images`: Authenticated (`forum_session`), CSRF-protected (`X-CSRF-Token`), validates file format via magic bytes, enforces max size (2MB = 2,097,152 bytes), writes file to local persistent volume, and creates `content.attachments` row with status `pending`.
   - `GET /uploads/images/{image_id}`: Serves image files directly with `X-Content-Type-Options: nosniff`, `Cache-Control`, and appropriate MIME type.
   - `POST /threads`: Accepts `image_ids: list[UUID]`. Validates each attachment exists, belongs to the current author, and is in `pending` status. Atomically associates them with the new thread, transitions status to `linked`, and sets `linked_at = now()`.
3. **Storage Persistence**:
   - Docker volume `content_uploads:/app/uploads` attached to `content-service` ensuring uploaded image files survive container restarts and deployments.
   - File storage scheme: `uploads/images/{uuid}.{ext}`, isolating stored files from client-supplied filenames to prevent path traversal and collision attacks.
4. **Security Controls**:
   - Streamed size verification preventing unbounded memory consumption (rejection at byte 2,097,153 with HTTP 413).
   - Strict raster format restriction: JPEG (`image/jpeg`), PNG (`image/png`), WebP (`image/webp`) only.
   - Buffer sniffing against byte signatures (magic bytes) rather than trusting client-provided `Content-Type` header (HTTP 415 on mismatch or spoofed extension).
   - EXIF/metadata handling and SVG rejection (SVG strictly rejected as non-raster / XSS vector).
5. **Frontend Client (`frontend/`)**:
   - Adds `api.uploadImage(file)` supporting `FormData` and `X-CSRF-Token`.
   - Enhances `#view-create-thread` with an image attachment file input and drop/select control, preview chips with removal capability, automatic insertion of markdown `![description](/api/v1/uploads/images/{id})`, and binding of `image_ids` to the thread creation payload.

---

## 2. Attachment Lifecycle State Machine

```
              [ Client Upload ]
                      │
                      ▼
               ┌──────────────┐
               │   PENDING    │  (thread_id = NULL, author_id = author)
               └──────┬───────┘
                      │
        ┌─────────────┴─────────────┐
        │ [ Thread Created ]        │ [ Retention Expired / Cleaned ]
        ▼                           ▼
 ┌──────────────┐            ┌──────────────┐
 │    LINKED    │            │   ORPHANED   │
 └──────┬───────┘            └──────┬───────┘
        │                           │
        │ [ Thread / Attachment     │ [ Hard Deletion / GC ]
        │   Deleted ]               │
        ▼                           ▼
 ┌──────────────┐            ┌──────────────┐
 │   DELETED    │            │   (Purged)   │
 └──────────────┘            └──────────────┘
```

### State Definitions:
1. `pending`: The file has been validated and saved to disk; attachment record inserted with `author_id`. Not yet linked to any thread.
2. `linked`: The author submitted `POST /threads` with this `image_id`. The thread exists and now owns the attachment (`thread_id` set, `linked_at` populated).
3. `orphaned`: Pending attachments older than the orphan TTL (e.g. 24h) that were never linked to a thread.
4. `deleted`: Soft-deleted attachments when parent thread is permanently expunged or image removed by author/moderator.

---

## 3. Storage Strategy and Volume Architecture

- Directory inside container: `/app/uploads/images`
- Volume mount in `docker-compose.yml`:
  ```yaml
  content-service:
    volumes:
      - content_uploads:/app/uploads
  ...
  volumes:
    content_uploads:
  ```
- File naming format:
  `{id}.{ext}` where `{id}` is the attachment UUID and `{ext}` is the verified format extension (`.jpg`, `.png`, `.webp`).
  User-supplied filenames are never used in path resolution, eliminating path traversal (`../../`) vulnerabilities.

---

## 4. Image Sniffing & Validation Matrix

| Target MIME Type | Extension | Magic Bytes / Header Signatures |
| :--- | :--- | :--- |
| `image/jpeg` | `.jpg` | Starts with `FF D8 FF` and contains SOI |
| `image/png` | `.png` | Starts with `89 50 4E 47 0D 0A 1A 0A` (PNG header) followed by `IHDR` chunk |
| `image/webp` | `.webp` | Starts with `52 49 46 46` (`RIFF`), bytes 8-12: `57 45 42 50` (`WEBP`) |

Any other header, executable (ELF, PE), script, or SVG (`<svg`, `<?xml`) is rejected with HTTP 415 Unsupported Media Type.

---

## 5. API Gateway and Content Routing

- `POST /api/v1/uploads/images`:
  - Request: `multipart/form-data` with field `file`
  - Headers: `Cookie: forum_session=...`, `X-CSRF-Token: ...`
  - Response `201 Created`:
    ```json
    {
      "id": "123e4567-e89b-12d3-a456-426614174000",
      "object_key": "uploads/images/123e4567-e89b-12d3-a456-426614174000.png",
      "file_size": 24500,
      "mime_type": "image/png",
      "status": "pending",
      "created_at": "2026-10-22T08:00:00Z"
    }
    ```
- `GET /api/v1/uploads/images/{image_id}`:
  - Serves image file bytes with `Content-Type: image/...`, `X-Content-Type-Options: nosniff`, `Cache-Control: public, max-age=86400`.
- `POST /api/v1/threads`:
  - Body includes `"image_ids": ["123e4567-e89b-12d3-a456-426614174000"]`.
  - Content Service verifies author ownership and pending status, updates record to `linked`, and links `thread_id`.
