# Task 020: RBAC Consistency & Policy Audit Report

## 1. Executive Summary

This report establishes role-based access control (RBAC) consistency across three layers of the Forum Platform:
1. **OpenAPI Specification Contract** (`docs/forum_public_api.openapi.yaml`).
2. **Backend Microservice Enforcement** (FastAPI route dependencies in `src/services/`).
3. **Frontend Client Navigation & Controls** (`frontend/src/app.js` and `frontend/src/auth.js`).

### Core Resolution: Role Delegation Permissions
An audit of `PATCH /admin/users/{user_id}/role` revealed a discrepancy between the OpenAPI contract and the backend implementation:
- **OpenAPI Contract:** Explicitly specifies `summary: Change user role (Admin)` with required security scopes limited to `admin`.
- **Pre-Task Implementation:** Accepted `role IN ('admin', 'moderator')` in `src/services/user/app.py`.
- **Hardening Applied:** Modified `admin_change_user_role` in `src/services/user/app.py` to enforce `dependencies=[Depends(require_role("admin")), Depends(verify_csrf_header)]`. Moderators are strictly restricted to content moderation (locking, deleting, reports, user bans) and cannot alter user account privilege tiers.

---

## 2. Role Privilege Matrix

| Functionality / Endpoint | Guest | Member | Moderator | Admin | Enforcement Mechanism |
|:---|:---:|:---:|:---:|:---:|:---|
| **View Discussions Feed** (`GET /threads`) | Allowed | Allowed | Allowed | Allowed | Public access |
| **View Categories** (`GET /categories`) | Allowed | Allowed | Allowed | Allowed | Public access |
| **Register & Verify OTP** (`POST /auth/register*`) | Allowed | N/A | N/A | N/A | Rate-limited public |
| **Log In / Out** (`POST /auth/login`, `POST /auth/logout`) | Allowed | Allowed | Allowed | Allowed | Public / Authenticated |
| **Create Thread** (`POST /threads`) | Denied | Allowed | Allowed | Allowed | `require_role("member", "moderator", "admin")` |
| **Edit Own Thread** (`PATCH /threads/{id}`) | Denied | Author Only | Author Only | Author Only | Resource ownership check |
| **Delete Own Thread (No comments)** (`DELETE /threads/{id}`) | Denied | Author Only | Author Only | Author Only | ADR-008 ownership check |
| **Delete Thread With Comments** (`DELETE /threads/{id}`) | Denied | Denied | Allowed | Allowed | `require_role("moderator", "admin")` |
| **Pin / Lock Thread** (`PATCH /threads/{id}/pin`, `/lock`) | Denied | Denied | Allowed | Allowed | `require_role("moderator", "admin")` |
| **Review Moderation Queue** (`GET /moderation/reports`) | Denied | Denied | Allowed | Allowed | `require_role("moderator", "admin")` |
| **Take Moderation Action** (`POST /moderation/actions`) | Denied | Denied | Allowed | Allowed | `require_role("moderator", "admin")` |
| **Create Category** (`POST /admin/categories`) | Denied | Denied | Denied | Allowed | `require_role("admin")` |
| **Modify User Role** (`PATCH /admin/users/{id}/role`) | Denied | Denied | Denied | Allowed | `require_role("admin")` |

---

## 3. Frontend Client-Side Routing & Navigation Alignment

1. **Unauthenticated / Guest Experience:**
   - Discussions list and search bar visible.
   - `+ New Thread` button hidden from the DOM (`display: none`).
   - Navbar displays: `Discussions`, `Log In`, and `Sign Up`.
   - Accessing restricted views redirects to Home with an error notice.
2. **Member Experience:**
   - Navbar displays: `Discussions`, `member` badge, `Profile (<username>)`, and `Log Out`.
   - `+ New Thread` button visible and active.
   - Admin/moderator navigation buttons are not rendered.
   - If route `#view-admin` is programmatically invoked, the router intercepts the request:
     ```javascript
     if (viewId === "view-admin" && !auth.isModerator()) {
       navigateTo("view-home");
       showAlert("error", "Access Denied: Administrative and moderator privileges required.");
       return;
     }
     ```
3. **Moderator Experience:**
   - Navbar displays `Moderation Queue` button leading to moderation actions.
   - User role modification table is hidden.
4. **Admin Experience:**
   - Navbar displays `Admin Panel` button.
   - User role management table and category creator tools are active.

---

## 4. Verification Evidence

- Backend unit & integration tests: `tests/test_user_auth_lifecycle.py`, `tests/test_content_service.py`, and `tests/test_moderation_service.py` pass.
- Browser test `test_browser_rbac_and_restricted_navigation` executes full client-side redirection and verified that non-privileged callers are denied access with clear error banners.
- Multi-role consistency: **VERIFIED & CONSISTENT**.
