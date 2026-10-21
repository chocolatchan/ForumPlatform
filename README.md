# Forum Platform

A modern, microservice-architected forum backend platform built with **FastAPI**, **Pydantic v2**, **SQLAlchemy**, and **PostgreSQL 16+**.

---

## Architecture Overview

The system consists of an **API Gateway** and six independent logical services:
1. **User Service** (`forum_users`): Account lifecycle, Argon2id credentials, OTPs, and server-side sessions.
2. **Content Service** (`forum_content`): Categories, threads, hierarchical comments (max 2 levels), and image attachments.
3. **Interaction Service** (`forum_interactions`): Voting (+1/-1), bookmarking, view deduplication (30m window), and reputation ledger.
4. **Moderation Service** (`forum_moderation`): Abuse reports (threads, comments, accounts), moderation queue, and audit actions.
5. **Notification Service** (`forum_notifications`): In-app notifications, unread counts, and user preferences.
6. **Search Service** (`forum_search`): Full-text search and query suggestions using PostgreSQL `tsvector`.

Architecture decisions and specifications:
- [Architecture Decision Records (MVP)](src/docs/forum_architecture_decisions_mvp.md)
- [API Contract Specification](src/docs/forum_api_contract.md)
- [Canonical OpenAPI 3.1 Spec](src/docs/forum_public_api.openapi.yaml)
- [PostgreSQL ERD DDL](src/docs/forum_postgres_erd.sql)
- [Entity-Relationship Diagram](src/docs/forum_erd.puml)
- [Sequence Diagrams](src/docs/forum_dynamic_diagrams.puml)
- [Activity Diagrams](src/docs/forum_activity_diagrams.puml)
- [BPMN Scope Status](src/docs/BPMN.md)
- [Audit & Readiness Reports](docs/audit/)

---

## Local Development Setup

### 1. Prerequisites
- **Python:** 3.12 or newer
- **Docker & Docker Compose:** for local PostgreSQL 16 and Redis 7
- **Git**

### 2. Environment Configuration
Create your local environment file from the template:
```bash
cp .env.example .env
```
*(Review `.env` to customize local ports or secret keys as needed.)*

### 3. Start Local Infrastructure
Launch PostgreSQL and Redis containers with automatic 6-database provisioning:
```bash
docker compose up -d
```
Verify containers are healthy:
```bash
docker compose ps
```

### 4. Python Environment & Dependencies
Initialize and activate your Python virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

### 5. Database Migrations
Apply baseline schema migrations across all 6 service databases:
```bash
python scripts/migrate.py upgrade all
```

---

## Running the Platform & Swagger UI

### 1. Complete Dockerized Environment (Docker Compose)
Launch the entire stack (Gateway, 6 Microservices, PostgreSQL, and Redis) containerized on the bridge network:
```bash
# Build and start all services in the background
docker compose up -d --build

# Inspect running containers and health checks
docker compose ps
```

To run database migrations within the Docker environment:
```bash
docker compose exec user-service python scripts/migrate.py upgrade all
```

To reset development data and reinitialize databases completely:
```bash
# Stop containers and destroy volumes
docker compose down -v

# Recreate containers and re-run migrations
docker compose up -d
docker compose exec user-service python scripts/migrate.py upgrade all
```

### 2. Local Process Execution (Alternative)
Run the services locally using your virtual environment:
```bash
# 1. Start Postgres & Redis only:
docker compose up -d postgres redis

# 2. Run migrations:
python scripts/migrate.py upgrade all

# 3. Run API Gateway (Port 8000)
uvicorn src.gateway.app:app --host 0.0.0.0 --port 8000 --reload

# 4. Run Services:
uvicorn src.services.user.app:app --port 8001 --reload
uvicorn src.services.content.app:app --port 8002 --reload
uvicorn src.services.interaction.app:app --port 8003 --reload
uvicorn src.services.moderation.app:app --port 8004 --reload
uvicorn src.services.notification.app:app --port 8005 --reload
uvicorn src.services.search.app:app --port 8006 --reload
```

### 3. Centralized Swagger UI
Access the interactive public Swagger UI through the Gateway:
- **Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc:** [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Canonical OpenAPI 3.1 Document:** [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

The Swagger UI points directly to the Gateway (`http://localhost:8000/api/v1`), exposing all 57 public operations with real database-backed Tier I CRUD integration across all 6 microservices.

---

## User Authentication & Account Lifecycle (Task 006)

The User Service implements complete database-backed authentication following **ADR-003**, **ADR-004**, **ADR-015**, and **DEC-001**:

### 1. Register a New Account (Anti-Enumeration DEC-001)
```bash
curl -i -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"dev_user","email":"developer@example.test","password":"Password123!"}'
# -> 202 Accepted: {"status":"pending_verification","message":"If the registration details are eligible, a verification code has been sent."}
```

### 2. Inspect Verification OTP in Development (Task 006.5 Mailpit Catcher)
All development emails are captured by **Mailpit** running in Docker:
- **Mailpit Web UI:** [http://localhost:8025](http://localhost:8025)
- **Mailpit REST API:** `http://localhost:8025/api/v1/messages`
- **Internal SMTP Port:** `1025` (internal Docker network only)
- **Recipient Allowlist (Fail-Closed):** Only addresses matching `DEV_EMAIL_ALLOWLIST` (default: `developer@example.test,tester@example.test`) are delivered. All other addresses are rejected with hard SMTP 550 errors. Failed delivery marks the pending challenge as `delivery_failed`, preventing activation of unreceived OTPs.

To query the latest OTP via Mailpit REST API:
```bash
curl -s http://localhost:8025/api/v1/messages | python3 -c "import sys, json; msgs = json.load(sys.stdin)['messages']; print(msgs[0]['Snippet'] if msgs else 'No messages')"
```
Or via the local test helper:
```bash
curl http://localhost:8001/test/otp?recipient=developer@example.test
# -> {"code":"123456"}
```

### 3. Verify OTP & Activate Account
```bash
curl -i -X POST http://localhost:8000/api/v1/auth/register/verify \
  -H "Content-Type: application/json" \
  -d '{"email":"developer@example.test","otp":"123456"}'
# -> 201 Created: {"user":{"id":"...","username":"dev_user","role":"member",...}}
```

### 4. Login & Session Cookie (ADR-003)
Authenticate with username or email:
```bash
curl -i -X POST http://localhost:8000/api/v1/auth/login \
  -c cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"login":"dev_user","password":"Password123!"}'
# -> 200 OK + Set-Cookie: forum_session=...; HttpOnly; SameSite=Lax; Path=/
```

### 5. Inspect Authenticated Session
```bash
curl -i http://localhost:8000/api/v1/auth/session -b cookies.txt
# -> 200 OK: {"user":{"username":"dev_user",...},"session":{"idle_expires_at":"...","absolute_expires_at":"..."}}
```

### 6. CSRF Token & Protected Profile Mutation (ADR-004)
Fetch synchronizer CSRF token and update own profile:
```bash
CSRF_TOKEN=$(curl -s http://localhost:8000/api/v1/auth/csrf | python3 -c "import sys, json; print(json.load(sys.stdin)['csrf_token'])")

curl -i -X PATCH http://localhost:8000/api/v1/profile \
  -b cookies.txt \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"display_name":"Developer","bio":"Forum Engineer"}'
# -> 200 OK
```

### 7. Logout & Session Revocation
```bash
curl -i -X POST http://localhost:8000/api/v1/auth/logout \
  -b cookies.txt \
  -H "X-CSRF-Token: $CSRF_TOKEN"
# -> 204 No Content + clears forum_session cookie
```

### 8. Security Hardening & Abuse Controls (Task 007)
The User Service enforces thread-safe sliding window rate limiting and hardened concurrency controls:
- **Rate Limits:**
  - `POST /auth/register`: 10 requests / minute per client
  - `POST /auth/login`: 10 requests / minute per client
  - `POST /auth/register/verify`: 15 requests / minute per client
  - `POST /auth/register/resend-otp`: 5 requests / minute per client
  - *Response:* Canonical `429 Too Many Requests` (`RATE_LIMITED`).
- **Immediate Privilege Invalidation:** Role changes executed by administrators immediately revoke all active sessions belonging to the target account, preventing downgraded accounts from retaining privileges.
- **Production Secret Validation:** In `production` environment, server startup fails-fast if `CSRF_SECRET` uses insecure default placeholders or is shorter than 32 characters.
- **Pinned Dependencies:** Mailpit container pinned to `v1.31.6@sha256:23fe39e81bde596a918000b3135c0f85dc8bb4c9ea6be274b051143f3e300387`.

### 9. Content Service Discussion Workflows (Task 008)
The Content Service provides database-backed discussion management accessible through the API Gateway:
- **Categories:** Public listing (`GET /api/v1/categories`) and detail (`GET /api/v1/categories/{id}`); Admin creation (`POST /api/v1/admin/categories`) and updating with slug uniqueness conflict detection (`409 CATEGORY_SLUG_CONFLICT`).
- **Threads:**
  - Create thread with deduplicated and normalized tags (`POST /api/v1/threads`). Emits `ThreadCreated` outbox event.
  - List published threads with cursor pagination and filtering (`GET /api/v1/threads?category_id={id}&tag={tag}&sort=newest`).
  - Edit own thread title, body, and tags (`PATCH /api/v1/threads/{id}`).
  - Moderation controls: Lock thread (`PUT /api/v1/threads/{id}/lock`) and pin thread (`PUT /api/v1/threads/{id}/pin`) restricted to moderators/admins.
  - Q&A best answer selection: Mark best answer comment (`PUT /api/v1/threads/{id}/best-answer`) restricted to thread author.
  - Deletion policy (ADR-008): Author deletion is rejected with `409 THREAD_REQUIRES_MODERATION` if active comments or an accepted best answer exist. Moderators may soft-delete (`DELETE /api/v1/threads/{id}`).
- **Hierarchical Comments:**
  - Create top-level comments and 1-level replies (`POST /api/v1/threads/{id}/comments`). Replies to replies are rejected (`400 COMMENT_DEPTH_EXCEEDED`).
  - Comments on locked threads are rejected (`409 THREAD_LOCKED`).
  - Soft-deletion (`DELETE /api/v1/comments/{id}`) marks comment deleted while preserving tree hierarchy; comment body displays `[deleted]`.
- **Identity & Gateway Security:** Gateway automatically strips client-supplied identity/role headers (`X-Authenticated-User-ID`, `X-Authenticated-Role`, `X-Forwarded-*`). Content Service authenticates requests against User Service via internal REST session lookup without cross-database foreign keys (DEC-002).

### 10. Moderation Service Core Workflows (Task 010)
The Moderation Service implements abuse reporting, triage lifecycle, and audit enforcement accessible via the API Gateway:
- **Abuse Reporting:**
  - Create abuse report against thread, comment, or user account (`POST /api/v1/reports`). Initial state is `pending`.
  - Self-reporting an account is rejected (`400 SELF_REPORT_NOT_ALLOWED`).
  - Active duplicate report prevention: Only one active (`pending` or `reviewing`) report is allowed per reporter and target (`409 DUPLICATE_REPORT`).
  - Retrieve caller's own submitted reports (`GET /api/v1/reports/mine`).
- **Moderation Queue & Triage:**
  - Moderator/Admin queue listing (`GET /api/v1/moderation/reports?status=pending&target_type=thread`) with cursor pagination.
  - Inspect report details (`GET /api/v1/moderation/reports/{id}`).
  - Claim report (`PATCH /api/v1/moderation/reports/{id}/review`), transitioning status `pending` -> `reviewing`.
- **Enforcement Actions & Proactive Moderation:**
  - Resolve report (`POST /api/v1/moderation/reports/{id}/actions`):
    - `dismiss_report`: Transitions report to `dismissed`.
    - Content/Account enforcement (`lock`, `pin`, `soft_delete`, `ban`, `unban`, `role_change`): Executes remote service mutation via REST client (DEC-002), transitions report to `actioned`, and records action in audit trail.
  - Proactive moderation without report (`POST /api/v1/moderation/actions`).
  - Audit trail listing (`GET /api/v1/moderation/actions`) with cursor pagination and target/moderator filters.
  - Outbox event: Commits `ModerationActionApplied` domain event to `moderation.outbox_events`.

### 11. Notification Service Core Workflows (Task 011)
The Notification Service provides persistent inbox notifications, preference management, and domain event consumption accessible via the API Gateway:
- **Inbox Management:**
  - Retrieve cursor-paginated notification feed (`GET /api/v1/notifications?limit=20&unread_only=true`).
  - Retrieve specific notification detail (`GET /api/v1/notifications/{id}`).
  - Mark single notification as read (`PATCH /api/v1/notifications/{id}/read`). Requires valid CSRF token.
  - Mark all user notifications as read (`POST /api/v1/notifications/read-all`). Requires valid CSRF token.
  - Quick unread count query (`GET /api/v1/notifications/unread-count`).
- **Notification Preferences:**
  - Retrieve current user delivery preferences (`GET /api/v1/notifications/preferences`). Automatically populates defaults if not yet customized.
  - Update preferences (`PATCH /api/v1/notifications/preferences`). Toggle channels (`in_app`, `email`) and categories (`comment_replies`, `thread_interactions`, `system_announcements`). Requires valid CSRF token.
- **Domain Event Ingestion & Idempotency:**
  - Ingestion endpoint (`POST /api/v1/notifications/internal/events`) processes outbox events from other services.
  - Supported events: `UserRegistered`, `CommentCreated`, `BestAnswerChanged`, `VoteChanged`, `ModerationActionApplied`.
  - Enforces atomic deduplication against `notifications.consumed_events` using unique `(event_id, recipient_id)`.
  - Self-notification exclusion (e.g. author replies to own thread or upvotes own content are discarded).
  - Preference suppression: Events matching disabled preference categories/channels are marked consumed but do not create inbox notifications.

### 12. Search Service Core Workflows (Task 012)
The Search Service provides full-text search, autocompletion suggestions, and domain event index synchronization accessible via the API Gateway:
- **Search & Suggestions Endpoints:**
  - Search visible threads (`GET /api/v1/search?q={query}&category_id={id}&tag={tag}&sort=relevance&limit=20&cursor={cursor}`).
  - Query sorting options: `relevance` (most recently indexed), `newest` (chronological by thread creation), and `most_viewed` (popularity).
  - Autocomplete query suggestions (`GET /api/v1/search/suggestions?q={prefix}&limit=5`). Validates minimum query length of 2 characters.
  - Stable cursor-based pagination encoding opaque timestamp tokens.
- **Visibility & Public Content Policy:**
  - Excludes unpublished, draft, hidden, or soft-deleted threads (`is_visible = false`).
  - Locked threads remain visible in search results according to architecture policy.
  - Moderation enforcement actions (`soft_delete`, `hide`) dynamically mark indexed documents hidden.
- **Event-Driven Index Synchronization & Deduplication:**
  - Ingestion endpoint (`POST /api/v1/search/internal/events`) processes outbox events from other services.
  - Supported events: `ThreadCreated`, `ThreadUpdated`, `ThreadDeleted`, `ThreadLockToggled`, `ThreadPinToggled`, `ThreadViewCountIncremented` (DEC-005), `ModerationActionApplied`.
  - Atomic idempotency: Deduplicates incoming events using `search.consumed_events` keyed on unique `event_id`.
  - Monotonic versioning: Updates increment `version` and update timestamps, protecting against stale overwrite.
- **Admin Reconciliation & Rebuilding:**
  - Admin reindex endpoint (`POST /api/v1/search/admin/reindex`) fetches published threads from Content Service REST API without cross-database queries (DEC-002).
  - Protected with admin role requirement and synchronizer CSRF token validation.

---

## Validation & Quality Commands

### OpenAPI Specification Validation
Validate OpenAPI 3.1 schema references and path integrity:
```bash
python3 -c "import yaml; spec = yaml.safe_load(open('src/docs/forum_public_api.openapi.yaml')); print(f'Parsed {len(spec[\"paths\"])} paths successfully.')"
```

### Type Checking & Linting
```bash
# Type check with Pyright
pyright --pythonpath ./venv/bin/python3

# Lint code with Ruff
ruff check .

# Format code with Ruff
ruff format --check .
```

### Automated Tests
```bash
pytest -v
```
