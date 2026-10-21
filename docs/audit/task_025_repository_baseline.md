# Task 025: Repository Baseline & Architecture Source of Truth
## Reproducible Baseline for Subsequent Engineering Tasks

### 1. Baseline Anchor
- **Baseline Commit SHA:** `c7fc8181cd1e903fa1947ff0f56c1da65546f450`
- **Commit Subject:** `024-R1-F`
- **Remote Default Branch:** `master`
- **Repository Remote:** `https://github.com/chocolatchan/ForumPlatform.git`
- **Status:** Fully synchronized with GitHub remote `origin/master`. Working tree clean.

---

### 2. Architecture & Service Topology

The ForumPlatform repository implements a technical brutalist single-page frontend backed by a distributed microservice architecture routing through an API Gateway:

```
[ Browser Client (Vanilla ES Modules) ]
                    │
            HTTP/1.1 (port 8000)
                    ▼
          [ API Gateway ] ── (Stateless Connection Pooling via StatelessCookieJar)
                    │
   ┌────────────────┼────────────────┬────────────────┬────────────────┬────────────────┐
   ▼ (8001)         ▼ (8002)         ▼ (8003)         ▼ (8004)         ▼ (8005)         ▼ (8006)
[ User Svc ]    [ Content Svc ]   [ Interact Svc ]  [ Moderation ]   [ Notification ]  [ Search Svc ]
   │                │                │                │                │                │
   └────────────────┴────────────────┴───────┬────────┴────────────────┴────────────────┘
                                             ▼
                                  [ PostgreSQL 16 (5432) ]
                                   schemas: users, content,
                                   interactions, moderation,
                                   notifications, search
```

---

### 3. Canonical Contract & Security Invariants

1. **Source of Truth for API Specifications:**
   - Canonical OpenAPI document: `src/docs/forum_public_api.openapi.yaml`
   - High-level architecture and decisions: `src/docs/forum_architecture_decisions_mvp.md` (ADR-001 through ADR-016)
   - API contract matrix: `src/docs/forum_api_contract.md`
2. **Session and Cookie Invariants:**
   - Sessions are opaque, random high-entropy tokens hashed via SHA-256 before storage in PostgreSQL `users.sessions`.
   - Delivered to browsers as an `HttpOnly`, `SameSite=lax`, `Path=/` cookie named `forum_session`.
   - Production / staging environments require `Secure` flag via `is_cookie_secure()`.
   - Zero authentication tokens or sensitive credentials permitted in `localStorage` or `sessionStorage`.
3. **Gateway Stateless Connection Pool:**
   - The Gateway's singleton `httpx.AsyncClient` is configured with `StatelessCookieJar()` to prevent caching and cross-request leakage of `Set-Cookie` tokens across pooled HTTP connections.
4. **CSRF Protection:**
   - Synchronizer token pattern. Obtained via `GET /api/v1/auth/csrf`.
   - Header `X-CSRF-Token` mandatory on state-modifying requests (`POST`, `PUT`, `PATCH`, `DELETE`).
5. **Anti-Enumeration Protections:**
   - User registration (`POST /api/v1/auth/register`) and resend OTP (`POST /api/v1/auth/register/resend-otp`) always return neutral `202 Accepted` regardless of whether the identifier exists or is eligible.
   - Authentication failure (`POST /api/v1/auth/login`) returns generic `401 Unauthorized` with message `"Invalid username/email or password."`.
6. **Thread Deletion Rules (ADR-008):**
   - Thread owners can only hard-delete their own thread if it has 0 comments and no selected best answer.
   - If active comments or a best answer exist, the Content Service rejects deletion with `409 Conflict` (`THREAD_REQUIRES_MODERATION`).

---

### 4. Canonical Verification Commands

Future tasks can reproduce this baseline using the following standard commands:

```bash
# 1. Start local container stack
docker compose up -d

# 2. Run backend ASGI integration suite (8 tests)
./venv/bin/pytest tests/test_frontend_e2e.py -v

# 3. Run full browser Playwright suite (16 tests)
./venv/bin/pytest tests/test_browser_e2e.py -v

# 4. Run combined test validation (24 tests total)
./venv/bin/pytest tests/test_frontend_e2e.py tests/test_browser_e2e.py -v

# 5. Linting and formatting checks
./venv/bin/ruff check src/ tests/
./venv/bin/ruff format --check src/ tests/

# 6. Static type analysis
./venv/bin/pyright --pythonpath ./venv/bin/python src/
```

---

### 5. Known Limitations & Deferred Work

The following capabilities are acknowledged as intentionally deferred post-MVP increments:
1. **Attachment Uploads:** `POST /api/v1/uploads/images` returns `501 Not Implemented`. Local image authoring uses markdown image link syntax.
2. **Password & Email Mutation:** `POST /api/v1/profile/change-password` and `POST /api/v1/profile/change-email/request` return `501 Not Implemented`.
3. **Detailed Reputation Transaction Ledger:** Itemized ledger table is maintained internally by `InteractionService`; public API exposes the authoritative aggregate score via `GET /api/v1/users/{id}/reputation`.
4. **Production Infrastructure:** Cloud TLS termination (HTTPS reverse proxy), automated WAF rules, managed RDS, and external transactional SMTP relay (SES/SendGrid) are outside the local container development scope.
