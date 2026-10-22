# Task 026-R1: Password Change Security and Contract Audit Report

**Author:** Antigravity  
**Audit Date:** 2026-10-22  
**Target Repository:** `chocolatchan/ForumPlatform`  
**Overall Verdict:** **PASS WITH FINDINGS**  

---

## 1. Executive Summary

An independent security, contract, and lifecycle audit of Task 026 was performed across the User Service, API Gateway, frontend, database transaction semantics, OpenAPI definitions, and automated test coverage.

The password change implementation fulfills canonical API requirements and ADR specifications:
- `POST /api/v1/profile/change-password` routes to `user-service:8001/profile/change-password`.
- Canonical request property `current_password` is supported with backward-compatible `old_password` alias resolution.
- Enforces server-side opaque `forum_session` cookie authentication (`401`) and synchronizer `X-CSRF-Token` validation (`403`).
- Current password check (`400 INVALID_PASSWORD`), minimum length check (`422 VALIDATION_ERROR`), and duplicate reuse prevention (`400 PASSWORD_REUSE_FORBIDDEN`) function reliably.
- In accordance with ADR-003, all other active sessions for the account are revoked, and the initiator's current session remains valid (`204 No Content`).

---

## 2. In-Depth Scope Findings

### 2.1 Test-Only Endpoint Safety (`POST /test/reset-rate-limiters`)
- **Inspection:** Located at [`src/services/user/app.py:162-171`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/user/app.py#L162-L171).
- **Gateway Reachability:**
  - `src/gateway/router.py` does not map `/test/` routes.
  - Runtime verification through Gateway `curl http://localhost:8000/api/v1/test/reset-rate-limiters` returned:
    `HTTP 404 {"error": {"code": "ROUTE_NOT_FOUND"}}`.
  - The endpoint is unreachable from external clients traversing the API Gateway.
- **Production Environment Guard:**
  - Evaluated code checks `if user_settings.environment == "production": raise HTTPException(status_code=404, detail="Not available")`.
  - Regression test added in [`tests/test_security_hardening.py:test_test_endpoint_safety_in_production`](file:///home/chocolat/Documents/Project/ForumPlatform/tests/test_security_hardening.py#L415) verifying HTTP 404 in production.
- **Finding Severity:** Low (Resolved). Guarded by environment check and blocked by API Gateway router.

### 2.2 Password Policy Consistency
- **Inspection:**
  - Registration password: `password: str = Field(..., min_length=8)` (`RegisterRequest`, `schemas.py:67`, OpenAPI line 1741).
  - Password Reset: `new_password: str = Field(..., min_length=8)` (`ResetPasswordRequest`, `schemas.py:95`, OpenAPI line 280).
  - Change Password: `new_password: str = Field(..., min_length=8)` (`ChangePasswordRequest`, `schemas.py:117`, OpenAPI line 443).
- **Consistency Analysis:**
  - Minimum length (8 characters) is uniformly enforced across all three password-entry routes.
  - ADR-015 specifies Argon2id password hashing and explicit normalization rules: *"Normalization thống nhất ở validation, DB uniqueness và login. Không trim mật khẩu âm thầm."* (Do not silently trim passwords).
  - Neither registration nor change password silently trims whitespace or applies arbitrary max-length limits that truncate entropy before Argon2id hashing.
- **Finding Severity:** Informational / Verified Consistent.

### 2.3 Transaction Boundaries and Session Semantics
- **Inspection:** Located at [`src/services/user/app.py:761-787`](file:///home/chocolat/Documents/Project/ForumPlatform/src/services/user/app.py#L761-L787).
- **Transaction Scope:**
  ```python
  async with user_db.transaction() as db_session:
      acc_query = select(Account).where(Account.id == ctx.user.id).with_for_update()
      account = (await db_session.execute(acc_query)).scalar_one()

      db_session.add(PasswordHistory(account_id=account.id, password_hash=account.password_hash, created_at=now))
      account.password_hash = new_hash
      account.password_changed_at = now

      await db_session.execute(
          update(Session)
          .where(Session.account_id == account.id, Session.id != ctx.session.id, Session.revoked_at.is_(None))
          .values(revoked_at=now, revocation_reason="password_changed")
      )
  ```
- **Concurrency & Failure Semantics:**
  - Explicit row lock (`.with_for_update()`) added to serialize concurrent password change requests on the same account.
  - `user_db.transaction()` uses `async with session.begin():` which guarantees atomic commit or automatic rollback if history insertion, password update, or session revocation fails.
  - Multi-session policy confirmed: `Session.id != ctx.session.id` leaves the current active session intact while revoking all other active sessions with `revocation_reason="password_changed"`.
- **Finding Severity:** Low (Hardened with `.with_for_update()`).

### 2.4 API and Schema Contract
- **Inspection:**
  - OpenAPI Specification (`src/docs/forum_public_api.openapi.yaml:422-451`):
    - Path: `/profile/change-password`
    - Security: `sessionCookie`
    - Parameters: `CsrfToken`
    - Request Body: `current_password` (required), `new_password` (required, minLength 8)
    - Responses: `204`, `400`, `401`.
  - Schema mapping in `src/services/user/schemas.py`:
    ```python
    class ChangePasswordRequest(BaseModel):
        current_password: str = Field(
            ...,
            validation_alias=AliasChoices("current_password", "old_password"),
            serialization_alias="current_password",
        )
        new_password: str = Field(..., min_length=8)
    ```
  - Both canonical payload `{"current_password": "...", "new_password": "..."}` and legacy payload `{"old_password": "...", "new_password": "..."}` are validated and tested in `test_password_change_lifecycle`.
- **Frontend Contract Handling:**
  - Handles `204` cleanly with empty body.
  - Handles `400`, `401`, `403`, and `422` with contextual error banner messaging.
  - Form inputs are cleared upon successful submission; passwords are never persisted to `localStorage`, `sessionStorage`, or console logs.
- **Finding Severity:** Informational / Verified Compliant.

---

## 3. Findings Summary Table

| Finding ID | Scope | Description | Severity | Status |
|---|---|---|---|---|
| **F-026-01** | Test Endpoint | Rate limiter reset endpoint in production environment | Low | **RESOLVED** (`404` in production + blocked by Gateway) |
| **F-026-02** | Concurrency | Concurrent password mutation race on account row | Low | **RESOLVED** (Enforced `.with_for_update()` in transaction) |
| **F-026-03** | Schema Alias | Acceptance of `old_password` alias alongside canonical `current_password` | Informational | **DOCUMENTED** & Verified via integration test |
| **F-026-04** | Password Policy | Uniformity of password length across registration and password change | Informational | **VERIFIED** (Consistent min length 8) |

---

## 4. Final Verdict

**VERDICT: PASS WITH FINDINGS**  
All identified findings have been substantiated, tested, and resolved within architecture boundaries. No regressions were introduced into existing test suites.
