# Task 006.5 Validation Report: Dockerized SMTP Email Catcher with Recipient Allowlist

**Milestone:** Task 006.5  
**Component:** User Service, Docker Compose, Mailpit SMTP Catcher, Test Suite  
**Date of Verification:** 2026-10-20  
**Verification Result:** ✅ **ALL ACCEPTANCE CRITERIA PASSED**  

---

## 1. Acceptance Criteria Verification

| Requirement | Acceptance Criteria | Result | Evidence |
| :--- | :--- | :--- | :--- |
| **Mailpit Service** | Stable container name `mailpit` in Docker Compose; internal SMTP port 1025; web UI on configurable host port (8025). | **PASSED** | `docker compose ps` shows `forum_mailpit` running healthy on `0.0.0.0:8025->8025/tcp`. SMTP 1025 is not published to host. |
| **Zero External Delivery** | Delivery strictly confined to local container network; no external SMTP relaying. | **PASSED** | Outbound traffic connects only to Docker network service `mailpit:1025`. |
| **Recipient Allowlist** | Only explicitly allowlisted addresses accepted; all others rejected; fail-closed on empty allowlist; partial matches rejected. | **PASSED** | Tested with unit & integration tests (`tests/test_email_delivery.py`): allowed addresses (`developer@example.test`) accepted; disallowed addresses (`hacker@example.com`, `developer@example.test.bad`) rejected. |
| **Safe Failure Handling** | Delivery failure must not leave a usable pending OTP in database; un-emailed OTP cannot be verified. | **PASSED** | Pending registration status is updated to `delivery_failed`; `POST /auth/register/verify` returns `400 Bad Request` (`INVALID_OTP`). |
| **Anti-Enumeration (DEC-001)** | Neutral `202 Accepted` returned on public API regardless of delivery success or failure. | **PASSED** | Live curl returned `202 Accepted` for both `tester@example.test` and `hacker@example.com`. |
| **No Sensitive Leaks** | No plaintext OTPs or email message bodies logged or returned in responses. | **PASSED** | Verified in container logs and response schemas. Only recipient and rejection status logged. |
| **Live Mailpit Verification** | Verified email arrives in Mailpit inbox with expected subject and OTP snippet. | **PASSED** | Query to `http://localhost:8025/api/v1/messages` confirmed arrival of registration verification message. |

---

## 2. Live Docker Environment Verification

### 2.1 Container Status
```bash
$ docker compose ps
NAME                         IMAGE                                COMMAND                  SERVICE                STATUS
forum_api_gateway            forumplatform-gateway                "uvicorn src.gateway…"   gateway                Up (healthy)
forum_content_service        forumplatform-content-service        "uvicorn src.service…"   content-service        Up (healthy)
forum_interaction_service    forumplatform-interaction-service    "uvicorn src.service…"   interaction-service    Up (healthy)
forum_mailpit                axllent/mailpit:latest               "/mailpit"               mailpit                Up (healthy)
forum_moderation_service     forumplatform-moderation-service     "uvicorn src.service…"   moderation-service     Up (healthy)
forum_notification_service   forumplatform-notification-service   "uvicorn src.service…"   notification-service   Up (healthy)
forum_postgres               postgres:16-alpine                   "docker-entrypoint.s…"   postgres               Up (healthy)
forum_redis                  redis:7-alpine                       "docker-entrypoint.s…"   redis                  Up (healthy)
forum_search_service         forumplatform-search-service         "uvicorn src.service…"   search-service         Up (healthy)
forum_user_service           forumplatform-user-service           "uvicorn src.service…"   user-service           Up (healthy)
```

### 2.2 Live Registration & Mailpit Message Delivery (Allowed Recipient)
```bash
$ curl -i -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "tester_mailpit", "email": "tester@example.test", "password": "Password123!"}'
HTTP/1.1 202 Accepted
{"status":"pending_verification","message":"If the registration details are eligible, a verification code has been sent."}

$ curl -s http://localhost:8025/api/v1/messages
{"total":1,"unread":1,"count":1,"messages_count":1,"messages_unread":1,"start":0,"tags":[],"messages":[{"ID":"3PFAV34qTVbWGZCmme9FXG","MessageID":"4BvDBGeHhDZfPByRdjpojR@mailpit","Read":false,"From":{"Name":"","Address":"noreply@forumplatform.local"},"To":[{"Name":"","Address":"tester@example.test"}],"Subject":"Your Forum Verification Code [Registration]","Snippet":"Hello, Your verification code is: 699541 This code will expire in 10 minutes. If you did not request this code, please disregard this message. -- Forum Platform Team"}]}

$ curl -i -X POST http://localhost:8000/api/v1/auth/register/verify \
  -H "Content-Type: application/json" \
  -d '{"email": "tester@example.test", "otp": "699541"}'
HTTP/1.1 201 Created
{"user":{"id":"f7fa29fb-4a6f-47d3-9c8a-047dce89ace0","username":"tester_mailpit","display_name":null,"bio":null,"role":"member"}}
```

### 2.3 Live Registration Rejection (Disallowed Recipient)
```bash
$ curl -i -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "hacker_user", "email": "hacker@example.com", "password": "Password123!"}'
HTTP/1.1 202 Accepted
{"status":"pending_verification","message":"If the registration details are eligible, a verification code has been sent."}

# Mailpit message count did NOT increase:
$ curl -s http://localhost:8025/api/v1/messages | grep -o '"total":[0-9]*'
"total":1

# Verification attempts fail:
$ curl -i -X POST http://localhost:8000/api/v1/auth/register/verify \
  -H "Content-Type: application/json" \
  -d '{"email": "hacker@example.com", "otp": "123456"}'
HTTP/1.1 400 Bad Request
{"error":{"code":"INVALID_OTP","message":"Invalid or expired verification challenge.","details":[],"request_id":"03abdf7c-7131-4475-b3e8-ab1f54535938"}}
```

---

## 3. Automated Test Suite Results

```bash
$ pytest -v
============================= test session starts ==============================
platform linux -- Python 3.14.7, pytest-9.1.1, pluggy-1.7.0
rootdir: /home/chocolat/Documents/Project/ForumPlatform
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.15.1, asyncio-1.4.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 52 items

tests/test_config.py .....                                               [  9%]
tests/test_crud_integration.py .......                                   [ 23%]
tests/test_database.py ...                                               [ 28%]
tests/test_email_delivery.py ..........                                  [ 48%]
tests/test_gateway.py .....                                              [ 57%]
tests/test_services.py ......                                            [ 69%]
tests/test_tier1_api.py ....                                             [ 76%]
tests/test_user_auth_lifecycle.py ............                           [100%]

======================== 52 passed, 4 warnings in 2.06s ========================
```

## 4. Linting and Type Checking

- **Pyright:** `pyright --pythonpath ./venv/bin/python3` -> `0 errors, 0 warnings, 0 informations`
- **Ruff:** `ruff check .` -> `All checks passed!`
