# Task 006.5 Change Log: Dockerized SMTP Email Catcher with Recipient Allowlist

**Milestone:** Task 006.5  
**Component:** User Service, Docker Compose, Mailpit SMTP Catcher, Test Suite  
**Date:** 2026-10-20  
**Status:** Completed & Fully Verified  

---

## 1. Summary of Changes

Task 006.5 integrates a local, containerized SMTP email catcher (**Mailpit**) into the existing Docker Compose development environment and configures the User Service to deliver all registration OTPs and OTP resends via SMTP with strict, fail-closed recipient allowlist enforcement.

Key accomplishments:
1. **Containerized SMTP Catcher (`mailpit`):**
   - Added `axllent/mailpit:latest` service to `docker-compose.yml` on the shared `forum_network`.
   - Exposed Mailpit web UI and REST API on host port `8025:8025`.
   - Kept SMTP port `1025` strictly internal to the Docker network (no host exposure).
   - Configured Mailpit health check (`/mailpit readyz`).
2. **Fail-Closed Recipient Allowlist:**
   - Enforced recipient allowlisting at both Mailpit level (`MP_SMTP_ALLOWED_RECIPIENTS`) and application level (`UserSettings.get_allowed_recipients()`).
   - Default allowlist permits only `developer@example.test,tester@example.test`.
   - Disallowed, unlisted, or partially matched addresses (e.g. `developer@example.test.bad`) are strictly rejected.
   - Empty or unconfigured allowlist fails closed (rejects all outbound emails).
3. **Safe Failure Handling & Transaction Consistency:**
   - In `POST /auth/register` and `POST /auth/register/resend-otp`, if SMTP delivery fails or recipient is rejected by allowlist:
     - Public API returns neutral `202 Accepted` preserving DEC-001 anti-enumeration.
     - Database pending record is transitioned to `status = 'delivery_failed'`, ensuring that unreceived OTPs cannot be used to activate an account.
     - Attempting to verify an OTP whose delivery failed returns `400 Bad Request` (`INVALID_OTP`).
4. **Zero Log & Response Leakage:**
   - Neither OTP codes nor raw email bodies are logged or returned in responses.
5. **Dual Mode Backend Support:**
   - Configured `email_delivery_backend`: defaults to `"memory"` for fast, dependency-free in-memory unit tests, while Docker Compose sets `EMAIL_DELIVERY_BACKEND=smtp` to route through Mailpit.

---

## 2. Modified and Created Files

| File | Change Type | Purpose |
| :--- | :--- | :--- |
| `src/services/user/config.py` | Modified | Added SMTP settings (`smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `email_from`, `email_delivery_backend`, `dev_email_allowlist`) and `get_allowed_recipients()` helper. |
| `src/services/user/otp_delivery.py` | Modified | Implemented `deliver_otp()` with allowlist validation, `_send_smtp_email_sync()` using standard `smtplib` and `EmailMessage`, error mapping (`EmailDeliveryError`), and safe logging. |
| `src/services/user/app.py` | Modified | Updated `register()` and `resend_registration_otp()` to invoke `deliver_otp()`, catch `EmailDeliveryError`, mark challenge status `delivery_failed`, and preserve neutral `202 Accepted`. |
| `docker-compose.yml` | Modified | Added `mailpit` container service (`axllent/mailpit:latest`), internal SMTP port `1025`, web UI port `8025`, healthcheck, and configured `user-service` to connect to `mailpit:1025`. |
| `.env.example` | Modified | Added `EMAIL_DELIVERY_BACKEND`, `SMTP_HOST`, `SMTP_PORT`, `EMAIL_FROM`, `DEV_EMAIL_ALLOWLIST`, `MAILPIT_UI_PORT`, and `MAILPIT_ALLOWED_RECIPIENTS`. |
| `tests/test_email_delivery.py` | Created | Automated test suite covering allowlist parsing, fail-closed empty allowlist, partial match rejection, SMTP 550 error handling, registration delivery, safe failure invalidation, and resend. |
| `tests/test_user_auth_lifecycle.py` | Modified | Updated autouse fixture to provide allowed recipients for authentication lifecycle tests. |
| `tests/test_crud_integration.py` | Modified | Updated test registration email to use allowed recipient `developer@example.test`. |
| `README.md` | Modified | Documented Mailpit web UI, REST API, allowlist behavior, and verification workflow. |
| `docs/audit/task_006_5_change_log.md` | Created | Comprehensive change log for Task 006.5. |
| `docs/audit/task_006_5_validation_report.md` | Created | Validation report recording automated and live Docker verification results. |
| `docs/audit/implementation_readiness.md` | Modified | Updated milestone status to reflect Task 006.5 completion. |
