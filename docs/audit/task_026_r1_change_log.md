# Task 026-R1: Change Log

**Task:** Password Change Security and Contract Audit  
**Audit Date:** 2026-10-22  
**Target Repository:** `chocolatchan/ForumPlatform`  

---

### Modified Files

1. **`src/services/user/app.py`**
   - Added `.with_for_update()` to account lookup inside `change_password` and `update_own_profile` transactions to eliminate potential concurrency races on account state mutations.
   - Verified environment guard `if user_settings.environment == "production": raise HTTPException(404)` on `/test/reset-rate-limiters` and `/test/otp`.

2. **`tests/test_security_hardening.py`**
   - Added `test_test_endpoint_safety_in_production` verifying that test hooks (`/test/reset-rate-limiters` and `/test/otp`) return HTTP 404 in production environment.

3. **`tests/test_user_auth_lifecycle.py`**
   - Expanded `test_password_change_lifecycle` to verify that `old_password` alias continues to function for backward compatibility alongside canonical `current_password`.

4. **`docs/audit/`**
   - Created `docs/audit/task_026_r1_security_contract_audit.md`.
   - Created `docs/audit/task_026_r1_validation_report.md`.
   - Created `docs/audit/task_026_r1_change_log.md`.
