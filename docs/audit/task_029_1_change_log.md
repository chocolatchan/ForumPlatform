# Task 029.1: Account Recovery Security Verification & Concurrency Audit Change Log

## Date: 2026-10-22

### Summary of Changes

Remediated subtle security and concurrency gaps identified during the Task 029.1 audit of the account recovery and password reset lifecycle. Added live PostgreSQL concurrency tests to verify atomic row locking and single-use guarantees.

---

### Files Modified & Created

#### 1. `src/services/user/app.py`
- **Finding SEC-029-01 (Trusted Proxy Client IP Resolution)**:
  - Replaced direct socket peer retrieval (`request.client.host`) in `recovery_request`, `recovery_verify`, and `recovery_reset` with `get_client_ip(request, user_settings.trusted_proxies)`.
  - Guarantees `X-Forwarded-For` header is only inspected when requests originate from trusted upstream reverse proxies (e.g., API Gateway).
- **Finding SEC-029-02 (Challenge Cooldown & Resend Quota Enforcement)**:
  - Added atomic pre-check in `POST /auth/recovery/request` against active `OneTimeToken` records.
  - Enforces 60-second cooldown (`resend_after`) and maximum 3 resends (`resend_count < 3`).
  - Suppresses duplicate challenge issuance while preserving neutral HTTP 202 response.

#### 2. `tests/test_recovery_concurrency_pg.py` (New Test File)
- Implemented three concurrency test scenarios executed against real PostgreSQL 16 (`forum_users`):
  1. `test_postgres_concurrent_otp_verification_single_winner`: Validates that racing requests verifying the same valid OTP yield exactly one `200 OK` (with ticket) and one `400 Bad Request` (`INVALID_OTP`).
  2. `test_postgres_concurrent_reset_ticket_single_winner`: Validates that racing reset requests using the same ticket proposing different passwords yield exactly one `204 No Content` and one `400 Bad Request` (`INVALID_RESET_TOKEN`), updating password history exactly once.
  3. `test_postgres_concurrent_failed_otp_attempts_lockout`: Validates that concurrent failed OTP attempts atomically increment the attempts counter and lock out the challenge upon reaching 5 attempts.

#### 3. Documentation Deliverables
- Created `docs/audit/task_029_1_security_audit.md`.
- Created `docs/audit/task_029_1_validation_report.md`.
- Created `docs/audit/task_029_1_change_log.md`.
