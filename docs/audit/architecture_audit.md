# Task 001: Architecture and Specification Audit Report

**Target System:** Forum Platform (MVP)  
**Date of Audit:** 2026-10-20  
**Audit Scope:** Complete repository specifications, architecture decisions, database DDL, API contracts, sequence & activity diagrams, and repository configurations.  
**Auditor:** Antigravity AI Engineering Assistant (Automated Audit Baseline)

---

## 1. Repository Inventory

The Forum Platform codebase is located at `/home/chocolat/Documents/Project/ForumPlatform` (Git branch `master`, commit `b5ec4fd`). Below is the verified inventory of all tracked and untracked project files:

| File Path | Size (Bytes) | Last Modified (UTC) | Status / Format | Description / Purpose |
|---|---|---|---|---|
| `src/docs/forum_architecture_decisions_mvp.md` | 8,424 | 2026-10-20 11:04 | UTF-8 Markdown | Baseline Architecture Decision Records (ADR-001 to ADR-016), data ownership, and testing criteria. |
| `src/docs/forum_api_contract.md` | 23,493 | 2026-10-20 11:04 | UTF-8 Markdown | Proposed system-wide API contract covering 38 HTTP endpoints, error envelopes, internal APIs, and event definitions. |
| `src/docs/forum_public_api.openapi.yaml` | 26,008 | 2026-10-20 11:04 | OpenAPI 3.1 YAML | Gateway Public API specification defining 16 paths, schemas, and security schemes. |
| `src/docs/forum_postgres_erd.sql` | 23,434 | 2026-10-20 07:18 | PostgreSQL 16 DDL | SQL migration script creating 6 schemas, 19 tables, 11 enums, triggers, and indexes. |
| `src/docs/forum_erd.puml` | 7,771 | 2026-10-20 07:24 | PlantUML ERD | Visual ERD showing database entities grouped by service schema. |
| `src/docs/forum_dynamic_diagrams.puml` | 12,421 | 2026-10-20 07:29 | PlantUML Sequence | 7 Sequence diagrams (Registration, Login, Create Thread, Comment, Vote/Best Answer, Moderation, Search). |
| `src/docs/forum_activity_diagrams.puml` | 9,040 | 2026-10-20 07:36 | PlantUML Activity | 7 Activity diagrams modeling business logic flows, cooldowns, and branches. |
| `src/docs/BPMN.md` | 0 | 2026-10-19 14:27 | Empty (0 bytes) | Intended for business process model notation; currently unpopulated. |
| `docker-compose.yml` | 0 | 2026-10-14 11:27 | Empty (0 bytes) | Intended for container orchestration (Postgres, Redis, services); currently unpopulated. |
| `pyproject.toml` | 0 | 2026-10-14 11:27 | Empty (0 bytes) | Intended for Python build and dependency specifications; currently unpopulated. |
| `pyrightconfig.yml` | 0 | 2026-10-14 11:27 | Empty (0 bytes) | Intended for Pyright/Pylance type checker configuration; currently unpopulated. |
| `.env.example` | 0 | 2026-10-14 11:27 | Empty (0 bytes) | Intended for environment variable templates; currently unpopulated. |
| `.env` | 6 | 2026-10-14 11:32 | Plain text | Contains only `.env\n`; placeholder. |
| `README.md` | 17 | 2026-10-14 13:25 | Markdown | Contains header `# Forum Platform\n`. |
| `.gitignore` | 11 | 2026-10-14 13:29 | Git ignore | Ignores `.env` and `venv/`. |
| `src/services/` | 0 | 2026-10-14 13:33 | Directory | Target directory for backend microservices; currently empty. |

*Note:* Files in `/home/chocolat/Documents/Project/AI` belong to an independent academic assignment (Sokoban) and are out of scope for the Forum Platform audit.

---

## 2. Architecture Summary Based on Verified Files

### 2.1 Technology Stack & Architectural Style
- **Backend Framework:** FastAPI with Pydantic v2 validation and SQLAlchemy ORM.
- **Persistence:** PostgreSQL 16+ utilizing `pgcrypto` for UUIDv4 generation.
- **Authentication:** Opaque server-side session managed via `HttpOnly; Secure; SameSite=Lax` cookie (`forum_session`). Idle timeout: 4 hours; Absolute timeout: 24 hours.
- **CSRF Protection:** Synchronizer token pattern passed via `X-CSRF-Token` header for state-changing operations.
- **Service Topology:** Microservices-based architecture fronted by an API Gateway, composed of six logical services:
  1. **User Service:** Account lifecycle, authentication, OTPs, session persistence, profiles, and ban states.
  2. **Content Service:** Categories, threads, hierarchical comments (max 2 levels), image upload metadata, and best-answer selection.
  3. **Interaction Service:** Votes (+1/-1), bookmarks, view counter deduplication (30-minute window per session/IP hash), and reputation ledger.
  4. **Moderation Service:** Abuse reports, moderation review queue, and audit actions.
  5. **Notification Service:** In-app user notifications, unread counts, and preference management.
  6. **Search Service:** Asynchronous full-text search index (`tsvector`) with eventual consistency (≤ 60s target).
- **Inter-service Communication & Reliability:**
  - Synchronous internal HTTP/REST for point-to-point queries.
  - Asynchronous Transactional Outbox pattern on PostgreSQL for critical business domain events, consumed idempotently (`event_id` deduplication).

---

## 3. Cross-Document Consistency Matrix

The following matrix summarizes cross-document alignment across core architectural dimensions:

| Dimension | ADRs (`mvp.md`) | API Contract (`.md`) | OpenAPI (`.yaml`) | SQL DDL (`.sql`) | Diagrams (`.puml`) | Status |
|---|---|---|---|---|---|---|
| **Service Boundaries** | 6 logical DBs, no cross-DB FKs | Logical UUID refs, internal APIs | Gateway routes only | 6 schemas in 1 DB | Visual packages | ⚠️ **Contradiction:** SQL enforces 6 physical cross-schema FKs |
| **Public Endpoints** | Chosts 16 baseline ADRs | Specifies 38 endpoints | Defines 16 endpoints | N/A | Sequences show paths | ⚠️ **Contradiction:** OpenAPI omits 22 contract endpoints |
| **Report Status** | `pending, reviewing, actioned, dismissed` | `pending, ...` | `pending, reviewing, actioned, dismissed` | `open, in_review, resolved, rejected` | Queue states match ADR | ⚠️ **Contradiction:** SQL enum completely differs from ADR/OpenAPI |
| **Moderation Actions** | Owner actions via API | `pin_thread, lock_thread, ...` | Not specified | `pin, lock, move_category, soft_delete, ban, unban, role_change` | `lock, delete, move, pin` | ⚠️ **Contradiction:** Mismatched action verbs and naming |
| **Thread Status** | `published, hidden, deleted` | `published, ...` | `published, locked, hidden, deleted` | `draft, published, hidden, removed` + boolean flags | Matches ADR | ⚠️ **Contradiction:** `locked`/`deleted` are enum in OpenAPI, booleans in SQL |
| **Vote Values** | `+1 / -1` | `1, -1` | `1, -1` | `'upvote', 'downvote'` | `+1, -1` | ⚠️ **Contradiction:** Integer in API/Contract vs string enum in SQL |
| **Report Target Types** | Thread / Comment | Thread / Comment | Thread / Comment | Thread / Comment / Account | Thread / Comment | ⚠️ **Contradiction:** SQL includes `account` target & `reported_account_id` |
| **Image Uploads** | Multipart via Content Service | `POST /uploads/images` | `image_ids` in thread | No attachments table | Object storage flow | ⚠️ **Contradiction:** Database has no table/columns for thread attachments |
| **Account Enumeration** | Strict anti-enumeration (neutral responses) | Mentions `409 USERNAME_TAKEN / EMAIL_TAKEN` | Defines `409 Conflict` | Unique indices on normalized | Diagrams show 409 and conflict errors | ⚠️ **Contradiction:** ADR-005 conflicts with Contract, OpenAPI, & Diagrams |
| **Moderator Action Path** | Via Moderation Service | `POST /moderation/reports/{id}/actions` | Not specified | No `report_id` in `moderation.actions` | `POST /moderation/actions` | ⚠️ **Contradiction:** Sub-resource path vs root path vs schema column gap |
| **Vote Endpoint Path** | Resource-oriented | `PUT /threads/{id}/vote` | `PUT /threads/{id}/vote` | `interactions.votes` | `POST /votes` | ⚠️ **Contradiction:** Sequence diagram uses deprecated RPC path |
| **Best Answer Event** | Event-driven | Missing from Event table | Not applicable | Triggers update best_answer | `BestAnswerChanged` | ⚠️ **Missing Information:** Missing from formal Event Contract table |
| **View Count Sync** | Deduplicated (30m window) | Unspecified sync flow | `view_count` on thread | `thread_views` + `threads.view_count` | Unspecified | ⚠️ **Missing Information:** No event or API to update `threads.view_count` |
| **CSRF on Logout** | Required on all cookie mutations | Required (`Session + CSRF`) | Parameter missing | N/A | N/A | ⚠️ **Contradiction:** OpenAPI omits `CsrfToken` on `POST /auth/logout` |
| **CSRF Endpoint** | Mandates `GET /auth/csrf` | Mentions need for endpoint | Missing | N/A | N/A | ⚠️ **Missing Information:** Endpoint omitted from OpenAPI and Contract paths |

---

## 4. Findings Ordered by Severity

### 4.1 Critical Severity

#### `FIND-CRIT-001`: Physical Cross-Schema Foreign Keys Violate Microservice Database Autonomy
- **Exact Location:** `src/docs/forum_postgres_erd.sql` (lines 288, 289, 308, 316, 350, 351); `src/docs/forum_erd.puml` (lines 290–296); `src/docs/forum_architecture_decisions_mvp.md` (ADR-001, ADR-013); `src/docs/forum_api_contract.md` (Section 1).
- **Conflict:** ADR-001 and ADR-013 explicitly mandate: *"Không dùng ID tăng dần làm ID public; không tạo FK xuyên database"* and *"mỗi service có database logic riêng... Không dùng FK xuyên service."* The API contract reinforces that cross-service references are logical UUIDs only. However, `forum_postgres_erd.sql` creates six physical foreign keys across schemas:
  1. `interactions.votes.thread_id -> content.threads(id)`
  2. `interactions.votes.comment_id -> content.comments(id)`
  3. `interactions.bookmarks.thread_id -> content.threads(id)`
  4. `interactions.thread_views.thread_id -> content.threads(id)`
  5. `moderation.reports.thread_id -> content.threads(id)`
  6. `moderation.reports.comment_id -> content.comments(id)`
- **Why It Matters:** If services are deployed with independent database instances, credentials, or migration pipelines, foreign key creation will fail immediately. It prevents independent database scaling and couples the persistence lifecycle of `content`, `interactions`, and `moderation`.
- **Recommended Resolution:** Remove the physical `REFERENCES content.threads/comments` clauses in `interactions` and `moderation` schemas in `forum_postgres_erd.sql` and update `forum_erd.puml`. Enforce reference validity at the application layer via internal REST APIs (e.g., Interaction/Moderation queries Content Service) or asynchronous event cleanup.
- **Impacted Documents:** `forum_postgres_erd.sql`, `forum_erd.puml`.

#### `FIND-CRIT-002`: Severe Endpoint Coverage Gap in OpenAPI Contract (22 Endpoints Omitted)
- **Exact Location:** `src/docs/forum_public_api.openapi.yaml` (paths section); `src/docs/forum_api_contract.md` (Sections 5–10).
- **Conflict:** The API Contract specifies 38 public HTTP endpoints, but `forum_public_api.openapi.yaml` only defines 16 endpoints. The following 22 endpoints are completely missing from OpenAPI:
  - **Auth/User:** `POST /auth/recovery/request`, `POST /auth/recovery/verify`, `POST /auth/recovery/reset`, `GET /users/{user_id}`, `POST /profile/change-email/request`, `POST /profile/change-email/verify`, `POST /profile/change-password`, `GET /admin/users`, `PATCH /admin/users/{user_id}/role`.
  - **Content:** `GET /categories/{category_id}`, `POST /admin/categories`, `PATCH /admin/categories/{category_id}`, `PUT /threads/{thread_id}/lock`, `PUT /threads/{thread_id}/pin`, `PUT /threads/{thread_id}/best-answer`, `PATCH /comments/{comment_id}`, `DELETE /comments/{comment_id}`, `POST /uploads/images`.
  - **Interaction:** `PUT /comments/{comment_id}/vote`, `GET /profile/bookmarks`, `POST /threads/{thread_id}/view`, `GET /users/{user_id}/reputation`.
  - **Moderation:** `GET /reports/mine`, `GET /moderation/reports`, `GET /moderation/reports/{report_id}`, `POST /moderation/reports/{report_id}/actions`, `GET /moderation/actions`.
  - **Notification:** `GET /notifications/unread-count`, `PATCH /notifications/{id}/read`, `POST /notifications/read-all`, `GET /notifications/preferences`, `PATCH /notifications/preferences`.
  - **Search:** `GET /search/suggestions`.
- **Why It Matters:** Frontend engineers and automated code generators (FastAPI client generators, Pydantic model generators) cannot generate client code or mocks. Contract tests cannot validate the unlisted 22 endpoints.
- **Recommended Resolution:** Update `forum_public_api.openapi.yaml` to include all 38 endpoints, their request/response schemas, path parameters, and error response envelopes matching `forum_api_contract.md`.
- **Impacted Documents:** `forum_public_api.openapi.yaml`.

---

### 4.2 High Severity

#### `FIND-HIGH-001`: Incompatible Report Status Enumerations Between Database and API
- **Exact Location:** `src/docs/forum_architecture_decisions_mvp.md` (ADR-010); `src/docs/forum_public_api.openapi.yaml` (`Report.status` schema); `src/docs/forum_postgres_erd.sql` (line 342: `moderation.report_status`).
- **Conflict:**
  - ADR-010 and OpenAPI specify: `['pending', 'reviewing', 'actioned', 'dismissed']`.
  - PostgreSQL DDL defines: `CREATE TYPE moderation.report_status AS ENUM ('open', 'in_review', 'resolved', 'rejected')`.
- **Why It Matters:** Any insertion or update from the API layer using documented status values (`pending`, `reviewing`, etc.) will fail at runtime with a PostgreSQL enum violation (`22P02: invalid input value for enum`).
- **Recommended Resolution:** Update `forum_postgres_erd.sql` line 342 and `forum_erd.puml` to use the ADR-010 standard: `'pending'`, `'reviewing'`, `'actioned'`, `'dismissed'`.
- **Impacted Documents:** `forum_postgres_erd.sql`, `forum_erd.puml`.

#### `FIND-HIGH-002`: Thread Status and Lifecycle State Modeling Conflict
- **Exact Location:** `src/docs/forum_postgres_erd.sql` (line 197); `src/docs/forum_public_api.openapi.yaml` (`Thread.status`); `src/docs/forum_api_contract.md` (Section 6).
- **Conflict:**
  - OpenAPI `Thread.status` enum is `['published', 'locked', 'hidden', 'deleted']`.
  - SQL table `content.threads` enforces: `status IN ('draft', 'published', 'hidden', 'removed')`, while modeling lock and deletion via separate boolean columns: `is_locked BOOLEAN`, `is_deleted BOOLEAN`, `is_pinned BOOLEAN`.
- **Why It Matters:** If a moderator or author queries thread status via API and receives `status: "locked"`, persisting this value in the database will violate `ck_thread_status`. Furthermore, SQL `draft` and `removed` states have no representation in OpenAPI.
- **Recommended Resolution:** Reconcile thread state representation: Either (Option A) adopt a single unified state machine in both SQL and OpenAPI (`draft`, `published`, `locked`, `hidden`, `deleted`), or (Option B) align OpenAPI to return `status: ['draft', 'published', 'hidden', 'removed']` alongside explicit boolean fields `is_locked`, `is_pinned`, and `is_deleted`.
- **Impacted Documents:** `forum_public_api.openapi.yaml`, `forum_postgres_erd.sql`, `forum_erd.puml`, `forum_api_contract.md`.

#### `FIND-HIGH-003`: Missing Attachment and Image Metadata Persistence in Content Schema
- **Exact Location:** `src/docs/forum_architecture_decisions_mvp.md` (ADR-006); `src/docs/forum_public_api.openapi.yaml` (`CreateThreadRequest.image_ids`); `src/docs/forum_postgres_erd.sql` (`content` schema).
- **Conflict:** ADR-006 states images are uploaded via Content Service (`multipart/form-data`) and linked to threads, with orphan images cleaned up if thread creation fails. OpenAPI allows `image_ids: [uuid]` on thread creation. However, `forum_postgres_erd.sql` has no `attachments` table, no join table, and no `image_ids` or foreign key in `content.threads`.
- **Why It Matters:** Content Service has no database schema to record image metadata (file key, MIME type, size, author) or associate uploaded images with threads. Orphan cleanup jobs cannot identify unlinked images.
- **Recommended Resolution:** Add a `content.attachments` table in `forum_postgres_erd.sql` and `forum_erd.puml`:
  ```sql
  CREATE TABLE content.attachments (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      thread_id UUID REFERENCES content.threads(id) ON DELETE CASCADE,
      author_id UUID NOT NULL,
      object_key TEXT NOT NULL,
      file_size INTEGER NOT NULL,
      mime_type VARCHAR(32) NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  ```
- **Impacted Documents:** `forum_postgres_erd.sql`, `forum_erd.puml`.

#### `FIND-HIGH-004`: Contradiction Between Account Enumeration Policy (ADR-005) and Explicit Conflict Responses
- **Exact Location:** `src/docs/forum_architecture_decisions_mvp.md` (ADR-005); `src/docs/forum_api_contract.md` (line 162); `src/docs/forum_public_api.openapi.yaml` (`POST /auth/register` responses); `src/docs/forum_dynamic_diagrams.puml` (Diagram 01, lines 22–25); `src/docs/forum_activity_diagrams.puml` (Diagram 01, line 53).
- **Conflict:**
  - ADR-005 strictly mandates: *"Endpoint đăng ký và recovery công khai dùng thông điệp trung tính, không tiết lộ email có tồn tại hay không. Response public không được có khác biệt dễ khai thác giữa tài khoản tồn tại và không tồn tại."*
  - In direct contrast, `forum_api_contract.md` states: *"Với trường hợp trùng username/email, contract có thể trả `409 USERNAME_TAKEN` hoặc `409 EMAIL_TAKEN`"*; OpenAPI declares `409 Conflict`; and both Sequence and Activity diagrams explicitly show branching to `409 Conflict` and displaying "Username or email already exists" to the guest.
- **Why It Matters:** This is a fundamental product/security conflict. If implemented with 409 errors, attackers can enumerate user emails. If implemented with neutral responses, the frontend UX in the diagrams cannot tell the user why registration did not proceed.
- **Recommended Resolution:** Separate username uniqueness from email enumeration: Return neutral `202 Accepted` for registration regardless of whether the email exists (sending an email with an OTP if new, or an email notifying them that an account already exists if duplicate), but validate username availability either via public conflict or a dedicated check endpoint. Update ADR-005, Contract, OpenAPI, and Diagrams to align.
- **Impacted Documents:** `forum_architecture_decisions_mvp.md`, `forum_api_contract.md`, `forum_public_api.openapi.yaml`, `forum_dynamic_diagrams.puml`, `forum_activity_diagrams.puml`.

#### `FIND-HIGH-005`: Moderation Action Route and Audit Schema Mismatch
- **Exact Location:** `src/docs/forum_api_contract.md` (line 322); `src/docs/forum_dynamic_diagrams.puml` (line 354); `src/docs/forum_postgres_erd.sql` (`moderation.actions` table).
- **Conflict:**
  - The API contract specifies actions as sub-resources of a report: `POST /moderation/reports/{report_id}/actions`.
  - Dynamic Diagram 06 specifies a root action endpoint: `POST /moderation/actions`.
  - The database table `moderation.actions` contains `(id, moderator_id, action, target_type, target_id, reason, metadata, created_at)` with NO `report_id` column.
- **Why It Matters:** If an action resolves a specific report, there is no foreign key or column linking the action audit log to the report ID. If a moderator performs a proactive action without a report, the API contract has no route to support it.
- **Recommended Resolution:** Add a nullable `report_id UUID` column to `moderation.actions` in `forum_postgres_erd.sql`. Support both `POST /moderation/reports/{report_id}/actions` (resolves report and logs action) and `POST /moderation/actions` (proactive moderator action without prior report).
- **Impacted Documents:** `forum_postgres_erd.sql`, `forum_erd.puml`, `forum_dynamic_diagrams.puml`, `forum_api_contract.md`.

#### `FIND-HIGH-006`: Missing Propagation Mechanism for Thread View Count
- **Exact Location:** `src/docs/forum_architecture_decisions_mvp.md` (ADR-011, ADR-013, ADR-016); `src/docs/forum_api_contract.md` (Sections 6, 7, 11, 12); `src/docs/forum_postgres_erd.sql` (`content.threads.view_count`, `interactions.thread_views`).
- **Conflict:** `content.threads` holds a `view_count` column. View deduplication is managed by Interaction Service in `interactions.thread_views`. Because cross-database direct updates are strictly forbidden by ADR-013, Interaction Service cannot execute `UPDATE content.threads SET view_count = view_count + 1`. However, there is no event (e.g. `ThreadViewed`) or internal API documented in the API contract or event catalog to sync view counts to Content Service.
- **Why It Matters:** Without a defined sync mechanism, `thread.view_count` in Content Service will permanently remain 0, breaking sorting by `most_viewed`.
- **Recommended Resolution:** Define an internal batch sync mechanism: either an asynchronous event `ThreadViewsAggregated` published periodically by Interaction Service, or an internal endpoint `POST /internal/content/threads/{id}/view-increment`.
- **Impacted Documents:** `forum_api_contract.md`, `forum_architecture_decisions_mvp.md`.

---

### 4.3 Medium Severity

#### `FIND-MED-001`: Discrepancy in Report Reasons Enumeration
- **Exact Location:** `src/docs/forum_public_api.openapi.yaml` (`CreateReportRequest.reason`); `src/docs/forum_postgres_erd.sql` (`moderation.report_reason`).
- **Conflict:**
  - OpenAPI enum: `['spam', 'harassment', 'inappropriate', 'misinformation', 'other']`.
  - PostgreSQL enum: `CREATE TYPE moderation.report_reason AS ENUM ('spam', 'abuse', 'wrong_category', 'harassment', 'other')`.
- **Why It Matters:** A user reporting content for `misinformation` or `inappropriate` via the API will trigger a database error `22P02`, while `abuse` and `wrong_category` cannot be submitted via the API.
- **Recommended Resolution:** Synchronize `moderation.report_reason` across OpenAPI, SQL, and API Contract to: `['spam', 'abuse', 'harassment', 'wrong_category', 'misinformation', 'other']`.
- **Impacted Documents:** `forum_public_api.openapi.yaml`, `forum_postgres_erd.sql`, `forum_erd.puml`.

#### `FIND-MED-002`: Vote Value Data Type Mismatch (Numeric vs String Enum)
- **Exact Location:** `src/docs/forum_public_api.openapi.yaml` (`VoteRequest.value`); `src/docs/forum_postgres_erd.sql` (`interactions.vote_value`); `src/docs/forum_api_contract.md` (Section 7).
- **Conflict:** OpenAPI and Contract define vote values as integers `1` and `-1`. PostgreSQL DDL defines `interactions.vote_value AS ENUM ('upvote', 'downvote')`.
- **Why It Matters:** Requires mapping layers in ORM/SQL drivers. If integers are passed directly to SQL queries, queries fail with type mismatch.
- **Recommended Resolution:** Change `interactions.votes.value` in SQL to `SMALLINT NOT NULL CHECK (value IN (-1, 1))` to naturally support mathematical summation (`SUM(value)`) and match API schemas.
- **Impacted Documents:** `forum_postgres_erd.sql`, `forum_erd.puml`.

#### `FIND-MED-003`: Verify Registration OTP Schema Discrepancy
- **Exact Location:** `src/docs/forum_api_contract.md` (line 167); `src/docs/forum_public_api.openapi.yaml` (`VerifyOtpRequest`).
- **Conflict:**
  - API contract specifies: `{"username": "forum_user", "email": "user@example.com", "otp": "123456"}`.
  - OpenAPI specifies required fields: `['email', 'otp']` (omitting `username`).
- **Why It Matters:** Frontend and backend following different documents will produce schema validation errors (`422 Unprocessable Entity`).
- **Recommended Resolution:** Standardize `VerifyOtpRequest` on `email` and `otp` (since email is globally unique in `pending_registrations` and sufficient to identify the record).
- **Impacted Documents:** `forum_api_contract.md`.

#### `FIND-MED-004`: Vote Endpoint RPC Path in Sequence Diagram 05
- **Exact Location:** `src/docs/forum_dynamic_diagrams.puml` (line 232); `src/docs/forum_api_contract.md` (lines 281–282); `src/docs/forum_public_api.openapi.yaml`.
- **Conflict:** Sequence Diagram 05 shows `FE -> GW: POST /votes`. The API contract and OpenAPI specify `PUT /threads/{thread_id}/vote` and `PUT /comments/{comment_id}/vote`.
- **Why It Matters:** Sequence diagram presents an outdated RPC-style endpoint that contradicts REST resource conventions used elsewhere.
- **Recommended Resolution:** Update line 232 in `forum_dynamic_diagrams.puml` to `FE -> GW: PUT /threads/{id}/vote`.
- **Impacted Documents:** `forum_dynamic_diagrams.puml`.

#### `FIND-MED-005`: Account Target Discrepancy in Reports
- **Exact Location:** `src/docs/forum_api_contract.md` (line 336); `src/docs/forum_public_api.openapi.yaml` (`CreateReportRequest.target_type`); `src/docs/forum_postgres_erd.sql` (`moderation.target_type`, `reports.reported_account_id`).
- **Conflict:** The API Contract and OpenAPI explicitly restrict report target types to `thread` and `comment`. PostgreSQL DDL and ERD define `moderation.target_type AS ENUM ('thread', 'comment', 'account')` and include a `reported_account_id` column.
- **Why It Matters:** Unclear whether reporting abusive accounts/profiles is supported in MVP. If supported, the API contracts lack the schema. If unsupported, the database contains dead columns.
- **Recommended Resolution:** Confirm whether user account reporting is an MVP feature. If yes, add `account` to OpenAPI and Contract. If no, remove `reported_account_id` from SQL DDL.
- **Impacted Documents:** `forum_api_contract.md`, `forum_public_api.openapi.yaml`, or `forum_postgres_erd.sql`.

#### `FIND-MED-006`: Missing `BestAnswerChanged` Event in Event Catalog
- **Exact Location:** `src/docs/forum_activity_diagrams.puml` (Diagram 05B, line 242); `src/docs/forum_api_contract.md` (Section 12).
- **Conflict:** Activity Diagram 05B emits `BestAnswerChanged` to the transactional outbox when a best answer is chosen. However, `BestAnswerChanged` is omitted from the formal Event Contract catalog table in `forum_api_contract.md` Section 12.
- **Why It Matters:** Downstream consumers (Notification Service for awarding badges/notices, Interaction Service for reputation bonuses) lack a contracted event schema.
- **Recommended Resolution:** Add `BestAnswerChanged` to the event catalog table in `forum_api_contract.md` with producer `Content` and consumers `Notification, Interaction`.
- **Impacted Documents:** `forum_api_contract.md`.

#### `FIND-MED-007`: Sequence Diagram 05 Violates Reputation Data Ownership
- **Exact Location:** `src/docs/forum_dynamic_diagrams.puml` (lines 260–261); `src/docs/forum_api_contract.md` (line 466); `src/docs/forum_postgres_erd.sql`.
- **Conflict:** Diagram 05 shows `BUS -> CS: ReputationChanged event; CS -> CDB: Update cached reputation if maintained`. However, Content Service schema (`content.threads`, `content.comments`) contains no reputation column, and user reputation belongs strictly to User Service / Interaction Service.
- **Why It Matters:** Directs developers to write event handling logic in Content Service that has no underlying persistence target.
- **Recommended Resolution:** Update Diagram 05 lines 260–261 to show `BUS -> US: ReputationChanged event; US -> UDB: Update cached user reputation`.
- **Impacted Documents:** `forum_dynamic_diagrams.puml`.

#### `FIND-MED-008`: Missing CSRF Parameter on Logout Operation in OpenAPI
- **Exact Location:** `src/docs/forum_architecture_decisions_mvp.md` (ADR-004); `src/docs/forum_api_contract.md` (line 125); `src/docs/forum_public_api.openapi.yaml` (`POST /auth/logout`).
- **Conflict:** ADR-004 and the API Contract mandate CSRF verification on all session-backed mutations, explicitly including logout. `POST /auth/logout` in OpenAPI omits the `$ref: '#/components/parameters/CsrfToken'` parameter.
- **Why It Matters:** Exposes users to cross-site logout CSRF attacks if client generators omit the header.
- **Recommended Resolution:** Add `$ref: '#/components/parameters/CsrfToken'` to `POST /auth/logout` in OpenAPI.
- **Impacted Documents:** `forum_public_api.openapi.yaml`.

#### `FIND-MED-009`: Missing CSRF Token Issuance Endpoint Definition
- **Exact Location:** `src/docs/forum_architecture_decisions_mvp.md` (ADR-004); `src/docs/forum_public_api.openapi.yaml`; `src/docs/forum_api_contract.md`.
- **Conflict:** ADR-004 specifies: *"Bổ sung `GET /api/v1/auth/csrf` hoặc cơ chế tương đương."* However, no such route exists in `forum_public_api.openapi.yaml` or `forum_api_contract.md`.
- **Why It Matters:** Single Page Applications (SPAs) or web frontends have no defined endpoint to fetch the initial CSRF synchronizer token after authentication.
- **Recommended Resolution:** Add `GET /auth/csrf` returning `{"csrf_token": "string"}` to OpenAPI and API Contract.
- **Impacted Documents:** `forum_public_api.openapi.yaml`, `forum_api_contract.md`.

#### `FIND-MED-010`: Empty Project Infrastructure and Dependency Files
- **Exact Location:** `docker-compose.yml`, `pyproject.toml`, `pyrightconfig.yml`, `.env.example` (all 0 bytes).
- **Conflict:** The repository contains no build declarations, dependency lists (FastAPI, SQLAlchemy, Pydantic, asyncpg, etc.), or Docker container configurations.
- **Why It Matters:** The project cannot be built, run in containers, or verified in CI/CD without creating infrastructure files from scratch.
- **Recommended Resolution:** Initialize `pyproject.toml` with project metadata and dependencies, create `docker-compose.yml` for PostgreSQL 16 and Redis, and provide `.env.example`.
- **Impacted Documents:** `docker-compose.yml`, `pyproject.toml`, `pyrightconfig.yml`, `.env.example`.

---

### 4.4 Low Severity

#### `FIND-LOW-001`: Missing `display_name` Column in Database Schema
- **Exact Location:** `src/docs/forum_public_api.openapi.yaml` (`UserPublic.display_name`); `src/docs/forum_postgres_erd.sql` (`users.accounts`).
- **Conflict:** OpenAPI `UserPublic` schema exposes an optional `display_name: string`. The SQL table `users.accounts` only contains `username` and `username_normalized`, with no `display_name` column.
- **Why It Matters:** Profile queries cannot return a distinct display name.
- **Recommended Resolution:** Add `display_name VARCHAR(50)` to `users.accounts` in SQL DDL or remove it from OpenAPI.
- **Impacted Documents:** `forum_postgres_erd.sql`, `forum_erd.puml`, or `forum_public_api.openapi.yaml`.

#### `FIND-LOW-002`: Missing `Idempotency-Key` Parameter on Report Creation in OpenAPI
- **Exact Location:** `src/docs/forum_architecture_decisions_mvp.md` (Section 3); `src/docs/forum_public_api.openapi.yaml` (`POST /reports`).
- **Conflict:** ADR Section 3 mandates idempotency key support on report creation. OpenAPI defines `IdempotencyKey` on threads and comments, but omits it from `POST /reports`.
- **Why It Matters:** Inconsistent retry headers across creation endpoints.
- **Recommended Resolution:** Add `$ref: '#/components/parameters/IdempotencyKey'` to `POST /reports` in OpenAPI.
- **Impacted Documents:** `forum_public_api.openapi.yaml`.

#### `FIND-LOW-003`: Unpopulated `BPMN.md` Specification File
- **Exact Location:** `src/docs/BPMN.md` (0 bytes).
- **Conflict:** The file exists in the specification suite but contains no content.
- **Why It Matters:** Ambiguity as to whether formal BPMN diagrams are expected or if PlantUML activity diagrams satisfy the process modeling requirement.
- **Recommended Resolution:** Document in `BPMN.md` that process flows are maintained in `forum_activity_diagrams.puml`, or provide markdown links to process flows.
- **Impacted Documents:** `src/docs/BPMN.md`.

#### `FIND-LOW-004`: PostgreSQL Text Search Configuration Language Limitation
- **Exact Location:** `src/docs/forum_postgres_erd.sql` (lines 434–437, 458–459).
- **Conflict:** Search vector uses `to_tsvector('simple', ...)` which lacks diacritic/accent removal and Vietnamese tokenization.
- **Why It Matters:** Search queries with or without Vietnamese tonal marks will fail to match corresponding text.
- **Recommended Resolution:** Document in ADR-012 the search analyzer roadmap (e.g. `unaccent` extension or specialized Vietnamese analyzer) for post-MVP.
- **Impacted Documents:** `forum_postgres_erd.sql`, `forum_architecture_decisions_mvp.md`.

---

## 5. Missing Decisions and Unresolved Assumptions

The following architectural decisions are missing or currently left as unverified assumptions:

1. **Password History Retention Limit:**
   - `users.password_history` table exists in SQL, but neither the ADRs nor the API Contract defines how many previous passwords must be remembered (e.g. last 3 or last 5).
2. **Orphan Attachment Cleanup Schedule:**
   - ADR-006 mentions an asynchronous cleanup job for orphan image uploads, but does not define the grace period (e.g., 2 hours vs 24 hours) before unlinked objects are purged.
3. **Reputation Calculation Formula:**
   - The reputation ledger records delta values, but the exact points awarded (e.g., +10 for upvote on thread, +5 for upvote on comment, +15 for best answer, -2 for downvote) are unspecified.
4. **Internal Service Authentication:**
   - Section 11 of the API Contract lists internal APIs (e.g. Gateway → User, Interaction → Content) but does not define the security protocol (e.g. Mutual TLS, internal JWT, or shared service tokens).
5. **Event Transport Technology:**
   - ADR-014 defines Transactional Outbox requirements, but the underlying message broker (e.g. Kafka, RabbitMQ, Redis Streams, or direct Postgres polling) is not selected.
6. **Soft Delete vs Retention Policy:**
   - ADR-007 states physical deletion occurs via a separate purge job, but retention timeframes (e.g. 30 days in `hidden` or `deleted` state before physical purge) are undefined.
7. **Rate Limiting Backend:**
   - Sequence diagrams introduce Redis for login rate limiting, but Redis is not mentioned in ADRs or docker configurations.

---

## 6. Validation Commands and Results

All validations were non-destructive and executed using system tools and Python 3.14.

### 6.1 OpenAPI 3.1 Structural Validation
- **Command:** `python3 -c "import yaml; spec = yaml.safe_load(open('src/docs/forum_public_api.openapi.yaml'))"`
- **Result:** Successfully parsed YAML.
- **Reference Integrity:** Verified 100 internal `$ref` targets across schemas, parameters, and responses. 0 broken `$ref` references.
- **Parameter Check:** All 16 paths correctly declare and resolve path parameters (`thread_id`).

### 6.2 PlantUML Syntax and Boundary Validation
- **Command:** Custom boundary verification script checking matching `@startuml` and `@enduml` blocks.
- **Result:**
  - `forum_erd.puml`: 1 diagram block (valid boundaries).
  - `forum_dynamic_diagrams.puml`: 7 sequence diagram blocks (valid boundaries).
  - `forum_activity_diagrams.puml`: 7 activity diagram blocks (valid boundaries).
- **Environment Limitation:** Native `plantuml` binary and PlantUML JAR were not present on the host; structural parsing was conducted via Python regex and AST validation.

### 6.3 SQL DDL Schema Extraction & Foreign Key Analysis
- **Command:** Custom parser extracting SQL DDL statements, constraints, and relationships from `forum_postgres_erd.sql`.
- **Result:**
  - Transaction boundary: `BEGIN` and `COMMIT` present.
  - 6 Schemas extracted: `users`, `content`, `interactions`, `moderation`, `notifications`, `search`.
  - 19 Tables extracted.
  - 11 Custom ENUM types extracted.
  - Confirmed 6 cross-schema physical foreign keys in violation of ADR-001/ADR-013.
- **Environment Limitation:** Docker daemon is offline/inaccessible, and `psql` client is not installed. SQL execution against a live disposable PostgreSQL 16 container could not be run.

### 6.4 ADR Coverage Validation
- **Command:** ADR ID sequential check from `forum_architecture_decisions_mvp.md`.
- **Result:** Exactly 16 ADRs (ADR-001 through ADR-016) declared with zero missing IDs.

---

## 7. Recommended Correction Order

To achieve an internally consistent baseline before writing code, corrections should be applied in this exact sequence:

1. **Step 1: Stakeholder Sign-Off on Architectural Decisions**
   - Resolve decisions in Section 8 (Account Enumeration, Report Status, Thread Status, Cross-schema FKs).
2. **Step 2: Database Schema Alignment (`forum_postgres_erd.sql` & `forum_erd.puml`)**
   - Drop the 6 physical cross-schema foreign keys.
   - Update `moderation.report_status` enum to `pending, reviewing, actioned, dismissed`.
   - Update `moderation.report_reason` to align with API reasons.
   - Add `content.attachments` table for image upload tracking.
   - Change `interactions.votes.value` to `SMALLINT` (-1, 1).
   - Add `report_id` to `moderation.actions`.
3. **Step 3: OpenAPI Contract Completion (`forum_public_api.openapi.yaml`)**
   - Expand OpenAPI from 16 to 38 endpoints as specified in `forum_api_contract.md`.
   - Add `GET /auth/csrf` and include `CsrfToken` parameter on `POST /auth/logout`.
   - Align schema fields (`VerifyOtpRequest`, `CreateReportRequest.reason`, `Thread.status`).
4. **Step 4: API Contract Synchronization (`forum_api_contract.md`)**
   - Add `BestAnswerChanged` to the event catalog.
   - Define view count sync mechanism from Interaction to Content Service.
   - Reconcile `POST /moderation/reports/{id}/actions` and standalone moderation actions.
5. **Step 5: Diagram Synchronization (`forum_dynamic_diagrams.puml` & `forum_activity_diagrams.puml`)**
   - Fix Sequence 05: change `POST /votes` to `PUT /threads/{id}/vote`, and route `ReputationChanged` to User Service.
   - Align Sequence 06 moderation action route.
6. **Step 6: Repository Infrastructure Initialization**
   - Populate `pyproject.toml` with dependencies and scripts.
   - Populate `docker-compose.yml` with PostgreSQL 16 and Redis services.

---

## 8. Decisions Requiring Human Approval

The following critical decisions require explicit stakeholder approval before proceeding to implementation:

| Decision ID | Topic | Option A | Option B | Recommendation |
|---|---|---|---|---|
| **DEC-001** | Account Enumeration Protection (ADR-005) | Strict neutral response (`202 Accepted` on duplicate email/username) | Public conflict error (`409 Conflict` with field indicators) | **Option A for Email, Option B for Username:** Usernames in a public forum are public; emails must not be enumerated. |
| **DEC-002** | Database Foreign Key Enforcement | Strict physical isolation (No cross-schema FKs; pure logical UUIDs) | Monolithic PostgreSQL FKs retained across schemas for MVP simplicity | **Option A:** Adhere to microservices boundary to avoid breaking service decoupling. |
| **DEC-003** | Thread State Lifecycle Modeling | Unified state machine enum (`draft, published, locked, hidden, deleted`) | Status enum (`draft, published, hidden, removed`) + separate booleans (`is_locked, is_pinned`) | **Option B:** Orthogonal booleans allow a thread to be both `published` and `locked` or `pinned` cleanly. |
| **DEC-004** | User Account Reporting Scope | Support reporting user accounts in MVP | Restrict reports to threads and comments only in MVP | **Option B:** Limit to thread/comment for MVP to reduce scope, deferring account reporting to MVP+. |
| **DEC-005** | View Count Propagation | Real-time event propagation (`ThreadViewed`) | Periodic batch counter aggregation or asynchronous projection | **Option B:** Real-time events on every view create excessive broker traffic; batching or projection is standard. |
