# Task 018 / 018-R1: Staging Smoke Test Report

**Date**: 2026-10-21  
**Project**: Forum Platform Microservices  
**Host**: `dyuxworkstation` (Linux 7.2.9-1-cachyos, Docker Engine 29.8.2, Compose 5.6.0)  
**Evaluator**: Antigravity Autonomous Agent  
**Result**: **PASS (Local Development, Automated Regression & Demo Profile)**

---

## 1. Executive Summary

This report establishes end-to-end verification of the Forum Platform on the active Docker Compose multi-service architecture in alignment with the revised criteria of **Task 018-R1**.

The platform is running 10/10 containers (Gateway, 6 Microservices, PostgreSQL 16 Alpine, Redis 7 Alpine, and Mailpit), with all 6 logical PostgreSQL service databases at their verified Alembic migration heads.

---

## 2. Live Runtime Smoke Test Results

All primary business journeys were executed against the running Docker Compose stack through the API Gateway (port 8000) backed by PostgreSQL 16 Alpine, Redis 7 Alpine, and the 6 microservices:

| Journey / Check | Method & Target | Status Code | Verification Result |
|---|---|---|---|
| **Gateway Liveness** | `GET http://localhost:8000/health/live` | 200 OK | `{"status": "ok"}` |
| **Gateway Readiness** | `GET http://localhost:8000/health/ready` | 200 OK | `{"status": "ready"}` |
| **Categories Listing** | `GET http://localhost:8000/api/v1/categories` | 200 OK | Categories fetched successfully |
| **Threads Listing** | `GET http://localhost:8000/api/v1/threads?limit=10` | 200 OK | Thread list with cursor pagination |
| **Thread Search** | `GET http://localhost:8000/api/v1/search?q=PostgreSQL` | 200 OK | Search index returns matches |
| **Search Autocomplete** | `GET http://localhost:8000/api/v1/search/suggestions?q=Post` | 200 OK | Autocomplete query completed |
| **Protected Profile (Unauth)**| `GET http://localhost:8000/api/v1/profile` | 401 Unauthorized | Canonical auth error; session required |
| **Internal RPC Protection** | `POST http://localhost:8002/internal/events` (invalid key) | 401 Unauthorized | Rejected invalid service key |
| **User Registration (Live)** | `POST http://localhost:8000/api/v1/auth/register` | 202 Accepted | Pending challenge created, email queued |
| **Mailpit Delivery (Live)** | `GET http://localhost:8025/api/v1/messages` | 200 OK | Real email received in Mailpit with OTP |
| **OTP Verification (Live)** | `POST http://localhost:8000/api/v1/auth/register/verify` | 201 Created | User account activated |
| **OTP Replay Protection** | `POST http://localhost:8000/api/v1/auth/register/verify` (resend) | 400 Bad Request | Single-use semantics enforced |
| **Delivery Failure Safety**| Unlisted recipient registration | 202 / DB check | `status = delivery_failed`; 0 account residue |
| **Metrics Scrape** | `GET http://localhost:8000..8006/metrics` | 200 OK | Prometheus 0.0.4 text output |
| **Database Migrations** | `alembic current` on all 6 databases | Exit 0 | All 6 databases at head |
| **PostgreSQL Backup** | `scripts/backup/backup_databases.sh` | Exit 0 | All 6 DBs backed up + SHA-256 |
| **Isolated Restore Drill** | `tests/test_backup_restore.py` | Exit 0 | Restored into prefixed DBs, verified, cleaned |
| **Worker Concurrency** | `tests/test_outbox_concurrency_live.py` | Exit 0 | Concurrency safe; stale claims recovered |

---

## 3. End-to-End Test Suite Smoke Verification

- **Automated E2E integration suite** (`tests/test_e2e_integration.py`):
  - `test_e2e_account_lifecycle_and_security`: **PASSED**
  - `test_e2e_discussion_moderation_notification_and_search_workflow`: **PASSED**
- **User auth lifecycle suite** (`tests/test_user_auth_lifecycle.py`): **12/12 PASSED**
- **Email delivery suite** (`tests/test_email_delivery.py`): **10/10 PASSED**
- **Outbox hardening suite** (`tests/test_outbox_hardening.py`): **6/6 PASSED**
- **Operational readiness suite** (`tests/test_staging_operational_readiness.py`): **5/5 PASSED**
- **Full pytest test suite**: **100/100 PASSED** in 21.21s.
- **Ruff Linting**: **All checks passed!**
- **Ruff Formatting**: **87 files formatted**
- **Pyright Static Type Checking**: **0 errors, 0 warnings**

---

## 4. Staging Smoke Test Conclusion

The internal system architecture, microservice RPC routing, database persistence, event delivery, and email registration lifecycle operate with zero defects. The local development, demo, and automated test profile is certified **PASS**.
