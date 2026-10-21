# Task 021: RBAC Acceptance & Authorization Matrix

**Date:** 2026-10-21  
**Scope:** Role-Based Access Control (RBAC) validation across all 4 system roles (Guest, Member, Moderator, Administrator) in both Gateway / API layers and Frontend UI.

---

## 1. Access Control Matrix

| System Action / Endpoint | Guest (Unauth) | Member (`user`) | Moderator (`moderator`) | Administrator (`admin`) |
| :--- | :---: | :---: | :---: | :---: |
| **Browse Threads & Categories** (`GET /threads`) | Allow (200) | Allow (200) | Allow (200) | Allow (200) |
| **View Thread Detail & Comments** (`GET /threads/{id}`) | Allow (200) | Allow (200) | Allow (200) | Allow (200) |
| **Create Thread** (`POST /threads`) | Deny (401) | Allow (201) | Allow (201) | Allow (201) |
| **Create Comment** (`POST /threads/{id}/comments`) | Deny (401) | Allow (201) | Allow (201) | Allow (201) |
| **Vote / Bookmark** (`POST /threads/{id}/vote`) | Deny (401) | Allow (200) | Allow (200) | Allow (200) |
| **Edit Own Thread** (`PUT /threads/{id}`) | Deny (401) | Allow (200, Owner) | Allow (200) | Allow (200) |
| **Edit Others' Thread** (`PUT /threads/{id}`) | Deny (401) | Deny (403) | Allow (200) | Allow (200) |
| **Report Content** (`POST /reports`) | Deny (401) | Allow (201) | Allow (201) | Allow (201) |
| **View Moderation Queue** (`GET /reports`) | Deny (401) | Deny (403) | Allow (200) | Allow (200) |
| **Resolve Report / Sanction** (`POST /reports/{id}/resolve`)| Deny (401) | Deny (403) | Allow (200) | Allow (200) |
| **Lock / Pin Thread** (`PATCH /threads/{id}/status`) | Deny (401) | Deny (403) | Allow (200) | Allow (200) |
| **View User Directory** (`GET /admin/users`) | Deny (401) | Deny (403) | Deny (403) | Allow (200) |
| **Modify User Role/Status** (`PATCH /admin/users/{id}/status`)| Deny (401) | Deny (403) | Deny (403) | Allow (200) |

---

## 2. API-Level RBAC Enforcement Verification

- **Enforcement Mechanics:**
  - Microservices implement `get_current_active_user` and role-checking dependencies: `require_moderator`, `require_admin`.
  - Roles are extracted from the server-side Redis session created during authentication.
  - When a user role fails the authorization check, the dependency raises `HTTPException(status_code=403, detail="Forbidden")`.
  - Unauthenticated requests (missing or invalid `forum_session` cookie) raise `HTTPException(status_code=401, detail="Unauthorized")`.

- **Automated Test Results:**
  - `tests/test_security_hardening.py`: Verified Member cannot access Moderator endpoints (403) or Admin endpoints (403). Verified Moderator cannot access Admin user management (403).
  - `tests/test_browser_e2e.py`: Verified non-admin receiving 403 on `/api/v1/admin/users`. Verified non-moderator receiving 403 on `/api/v1/reports`.
  - All positive and negative authorization branches validated with **100% pass rate**.

---

## 3. Frontend UI Role-Aware Rendering Verification

- **Navigation & Page Rendering in `frontend/src/app.js`:**
  - When role is `guest`: Navigation displays `Login` and `Register`. Thread creation button is hidden.
  - When role is `user`: Navigation displays `Threads`, `Profile`, `Logout`. Moderation and Admin navigation links are not rendered.
  - When role is `moderator`: Navigation renders `Moderation` link (`#/moderation`). Admin link remains hidden.
  - When role is `admin`: Navigation renders both `Moderation` and `Admin` links (`#/admin`).
  - Route Guards: Direct URL navigation (e.g. hash routing to `#/admin` by a normal user) invokes API calls that return 403, correctly triggering the application's error alert and redirecting to safe views without exposing internal data.

---

## 4. Verdict
- RBAC model is strictly and consistently enforced across all services and layers.
- Status: **PASS**.
