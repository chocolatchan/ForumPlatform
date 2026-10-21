# Task 018 / 018-R1: Staging Release Decision

**Date**: 2026-10-21  
**Project**: Forum Platform Microservices  
**Release Candidate**: `v1.0.0-rc1`  
**Evaluator**: Antigravity Autonomous Agent  
**Revision Specification**: Task 018-R1 (Separated Acceptance Dimensions)

---

## 1. Executive Summary & Separated Release Decisions

In accordance with **Task 018-R1**, acceptance criteria are explicitly separated across three operational dimensions so that local development and demo verification are not blocked by external internet requirements:

### 1. Local Development & Demo Profile: **PASS**
All local development, automated test regression, and demonstration requirements are satisfied with reproducible live evidence:
- Full system running in Docker Compose (10/10 containers healthy).
- Real OTP email received in Mailpit via API Gateway `/api/v1/auth/register`.
- OTP extracted from Mailpit and successfully verified via `/api/v1/auth/register/verify`.
- OTP single-use replay rejection confirmed (`400 INVALID_OTP`).
- Registration failure cleanup confirmed (`delivery_failed` status with 0 unverified account residue).
- 100/100 automated pytest tests passing, 0 Ruff lint errors, 0 Pyright static typing errors.

### 2. External SMTP Integration: **NOT TESTED (Supported by Code)**
- Direct SSL/TLS (`smtp_use_tls`) and opportunistic STARTTLS (`smtp_starttls`) configurations implemented and unit tested.
- Real third-party cloud provider delivery is classified as **NOT TESTED** due to the absence of live external SMTP credentials in the local environment.

### 3. Public Staging Ingress: **NOT TESTED (Supported by Code)**
- Secure session cookie configuration (`is_cookie_secure()`) defaults to `True` in staging and production, verified by automated unit tests.
- Reverse proxy trust configuration and identity header stripping verified.
- Public DNS and CA-signed certificate testing are classified as **NOT TESTED** as they are only applicable when deploying to a public cloud ingress.

---

## 2. Comprehensive Acceptance Matrix

| Criterion / Dimension | Required Evidence | Result | Notes |
|---|---|---|---|
| **Local Gateway & Routes** | 200 OK on health, categories, threads, search | **PASS** | Verified live on port 8000 |
| **Mailpit OTP Delivery** | Real email captured in Mailpit from Gateway registration | **PASS** | Verified live via Mailpit API (ID `1WcAsJzfUT4P7qWHuqvQcD`) |
| **Live OTP Verification** | Successful account creation from Mailpit OTP | **PASS** | Verified (`201 Created`, user activated) |
| **Single-Use Replay Guard**| Re-submitting consumed OTP rejected | **PASS** | Verified (`400 INVALID_OTP`) |
| **Delivery Failure Safety**| Unlisted recipient fail-closed with 0 account residue | **PASS** | Verified (`status=delivery_failed`, 0 accounts) |
| **Session Lifecycle** | Login, protected access, CSRF, logout | **PASS** | Verified in `tests/test_user_auth_lifecycle.py` |
| **Cookie Security Flags** | `HttpOnly`, `SameSite=Lax`, `Secure` in staging/prod | **PASS** | Code implemented & verified in tests |
| **Database Migrations** | Six databases at expected Alembic heads | **PASS** | Verified on all 6 logical PostgreSQL databases |
| **Backup & Restore Drill** | Multi-DB dump + isolated restore into prefixed DBs | **PASS** | Verified via `scripts/backup/*` and automated test |
| **Transactional Outbox** | Concurrency safe (`SKIP LOCKED`), recovery of stale claims | **PASS** | Verified in `tests/test_outbox_concurrency_live.py` |
| **Observability & Metrics** | Prometheus `/metrics` on Gateway + 6 services | **PASS** | Verified on ports 8000–8006 |
| **Code Quality Gates** | Pytest, Ruff linter/formatter, Pyright | **PASS** | 100/100 tests passed, 0 errors |
| **External Cloud SMTP** | Live dispatch to Internet mailbox via third-party provider | **NOT TESTED** | Optional for local/demo; code ready |
| **Public HTTPS Ingress** | Public DNS record + CA-signed certificate | **NOT TESTED** | Optional for local/demo; code ready |

---

## 3. Operator Instructions for Cloud Deployments

When preparing to transition from local/demo to live public staging:
1. **Public Ingress**: Configure reverse proxy (Caddy/Nginx) with Let's Encrypt TLS terminating to `forum_api_gateway:8000`.
2. **External SMTP**: Set `EMAIL_DELIVERY_BACKEND=smtp`, `SMTP_HOST=<provider>`, `SMTP_PORT=587`, `SMTP_STARTTLS=true`, `SMTP_USER`, and `SMTP_PASSWORD` in `.env`.
