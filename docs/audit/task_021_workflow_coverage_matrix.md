# Task 021: Core Forum Workflow Coverage Matrix

**Date:** 2026-10-21  
**Scope:** Traceability matrix from requirement -> endpoint -> service owner -> automated test -> execution result across all 6 core forum workflows.

---

## 1. Traceability Matrix

| Workflow Area | Requirement / Scenario | Endpoint | Service Owner | Automated Test Coverage | Result |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Auth & Lifecycle** | Guest registration with OTP | `POST /api/v1/auth/register` | User Service | `test_email_delivery.py`<br>`test_user_auth_lifecycle.py` | **PASS** |
| | OTP verification & account activation | `POST /api/v1/auth/verify-otp` | User Service | `test_email_delivery.py`<br>`test_user_auth_lifecycle.py` | **PASS** |
| | Credential authentication & session cookie | `POST /api/v1/auth/login` | User Service | `test_frontend_e2e.py`<br>`test_browser_e2e.py` | **PASS** |
| | CSRF token retrieval & validation | `GET /api/v1/auth/csrf` | User Service / Gateway | `test_frontend_e2e.py`<br>`test_security_hardening.py` | **PASS** |
| | Authenticated profile retrieval | `GET /api/v1/users/me` | User Service | `test_browser_e2e.py`<br>`test_user_auth_lifecycle.py` | **PASS** |
| | Session termination & Redis revocation | `POST /api/v1/auth/logout` | User Service | `test_frontend_e2e.py`<br>`test_browser_e2e.py` | **PASS** |
| **Discussion Lifecycle** | List categories (public read) | `GET /api/v1/categories` | Content Service | `test_content_service.py`<br>`test_tier1_api.py` | **PASS** |
| | Create thread (Member/Admin) | `POST /api/v1/threads` | Content Service | `test_content_service.py`<br>`test_crud_integration.py` | **PASS** |
| | Read thread by ID (public read) | `GET /api/v1/threads/{id}` | Content Service | `test_content_service.py`<br>`test_browser_e2e.py` | **PASS** |
| | Update thread (Author/Moderator/Admin) | `PUT /api/v1/threads/{id}` | Content Service | `test_content_service.py`<br>`test_crud_integration.py` | **PASS** |
| | Soft-delete thread (Author/Mod/Admin) | `DELETE /api/v1/threads/{id}` | Content Service | `test_content_service.py`<br>`test_crud_integration.py` | **PASS** |
| | Create comment on thread | `POST /api/v1/threads/{id}/comments` | Content Service | `test_content_service.py`<br>`test_browser_e2e.py` | **PASS** |
| | Read comments on thread | `GET /api/v1/threads/{id}/comments` | Content Service | `test_content_service.py`<br>`test_browser_e2e.py` | **PASS** |
| | Soft-delete comment | `DELETE /api/v1/comments/{id}` | Content Service | `test_content_service.py` | **PASS** |
| **Interactions** | Like/Vote on thread (idempotent toggle) | `POST /api/v1/threads/{id}/vote` | Interaction Service | `test_interaction_service.py`<br>`test_crud_integration.py` | **PASS** |
| | Retract vote / change vote | `POST /api/v1/threads/{id}/vote` | Interaction Service | `test_interaction_service.py` | **PASS** |
| | View vote counts | `GET /api/v1/threads/{id}/votes` | Interaction Service | `test_interaction_service.py` | **PASS** |
| | Bookmark thread (create & delete) | `POST/DELETE /api/v1/threads/{id}/bookmark` | Interaction Service | `test_interaction_service.py` | **PASS** |
| **Moderation & RBAC** | Member reports thread/comment | `POST /api/v1/reports` | Moderation Service | `test_moderation_service.py`<br>`test_browser_e2e.py` | **PASS** |
| | Moderator reviews pending reports | `GET /api/v1/reports` | Moderation Service | `test_moderation_service.py`<br>`test_browser_e2e.py` | **PASS** |
| | Moderator resolves report / applies action | `POST /api/v1/reports/{id}/resolve` | Moderation Service | `test_moderation_service.py`<br>`test_browser_e2e.py` | **PASS** |
| | Moderator locks/pins thread | `PATCH /api/v1/threads/{id}/status` | Content Service | `test_content_service.py` | **PASS** |
| | RBAC enforcement: Member denied mod action | `GET /api/v1/reports` | Moderation Service | `test_browser_e2e.py`<br>`test_security_hardening.py` | **PASS** |
| | RBAC enforcement: Mod denied admin action | `GET /api/v1/admin/users` | User Service | `test_browser_e2e.py`<br>`test_security_hardening.py` | **PASS** |
| | RBAC enforcement: Admin user status update | `PATCH /api/v1/admin/users/{id}/status`| User Service | `test_browser_e2e.py`<br>`test_crud_integration.py` | **PASS** |
| **Search & Notification** | Outbox publishes thread created event | Background / DB Outbox | Content -> Outbox | `test_outbox_worker.py`<br>`test_outbox_hardening.py` | **PASS** |
| | Search consumer indexes thread | Background / DB Events | Search Service | `test_search_service.py`<br>`test_e2e_integration.py` | **PASS** |
| | Full-text search for indexed threads | `GET /api/v1/search` | Search Service | `test_search_service.py`<br>`test_e2e_integration.py` | **PASS** |
| | Notification consumer emits notification | Background / DB Events | Notification Service| `test_notification_service.py`<br>`test_e2e_integration.py` | **PASS** |
| | User retrieves own notifications | `GET /api/v1/notifications` | Notification Service| `test_notification_service.py` | **PASS** |
| | User marks notification as read | `PATCH /api/v1/notifications/{id}/read`| Notification Service| `test_notification_service.py` | **PASS** |

---

## 2. Test Execution Summary

- **Total Backend Tests:** 108 passed
- **Total Browser E2E Tests (Playwright Firefox):** 8 passed
- **Total Combined Tests:** 116 passed, 0 failed, 0 skipped
- **Overall Workflow Verdict:** **PASS**
