# Task 020: Quality & Pytest Warnings Investigation Report

## 1. Executive Summary

During full regression testing (`./venv/bin/pytest -q`), **193 warnings** are emitted across the backend test suite.

This audit analyzed the origin, severity, risk level, and future remediation plan for each emitted warning category.

### Summary Verdict:
- **Zero test failures:** All 115 tests pass.
- **Zero functional defects or security risks:** The warnings are deprecation notices originating from external third-party dependencies (`httpx` and `starlette`/`fastapi`) running on modern Python 3.14.
- **Action plan established:** Documented migration path for httpx 1.0 release without breaking current backward compatibility.

---

## 2. Warnings Breakdown & Root Cause Analysis

### Category A: `httpx._client.py` Per-Request Cookies Deprecation (~191 occurrences)
- **Warning text:**
  `DeprecationWarning: Setting per-request cookies=<...> is being deprecated, because the expected behaviour on cookie persistence is ambiguous. Set cookies directly on the client instance instead.`
- **Files emitting warning:**
  - `tests/test_content_service.py` (39 warnings)
  - `tests/test_notification_service.py` (40 warnings)
  - `tests/test_moderation_service.py` (41 warnings)
  - `tests/test_e2e_integration.py` (32 warnings)
  - `tests/test_interaction_service.py` (33 warnings)
  - `tests/test_crud_integration.py` (1 warning)
  - `tests/test_user_auth_lifecycle.py` (5 warnings)
- **Root Cause:**
  Backend integration test fixtures instantiate a single `AsyncClient` and simulate multiple users (e.g., `admin`, `author`, `stranger`) by passing `cookies={"forum_session": token}` per request call (e.g., `client.post(..., cookies={...})`).
  HTTPX 0.28+ deprecated per-request cookies in favor of binding cookies to client instances (`AsyncClient(cookies={...})`) to avoid state pollution in HTTP/2 multi-stream multiplexing.
- **Impact & Risk Assessment:**
  - **Current status:** Low. Behavior in HTTPX 0.28 is completely backward-compatible and reliable.
  - **Future compatibility:** When HTTPX releases version 1.0, per-request cookie passing may raise a `TypeError`.
- **Recommended Remediation:**
  In subsequent test refactoring, tests simulating multiple actors should instantiate dedicated client contexts (or use `client.cookies.set()`) rather than passing per-request cookie dictionaries.

---

### Category B: `fastapi/routing.py` HTTP 422 Status Constant (~2 occurrences)
- **Warning text:**
  `StarletteDeprecationWarning: 'HTTP_422_UNPROCESSABLE_ENTITY' is deprecated. Use 'HTTP_422_UNPROCESSABLE_CONTENT' instead.`
- **Files emitting warning:**
  - `tests/test_interaction_service.py::test_thread_and_comment_voting_lifecycle`
  - `tests/test_tier1_api.py::test_request_validation_failures_return_canonical_422`
- **Root Cause:**
  FastAPI's internal routing machinery references Starlette's `HTTP_422_UNPROCESSABLE_ENTITY`. The RFC 9110 HTTP standard renamed 422 to "Unprocessable Content" in modern specifications, and Starlette 0.40+ issues a warning when importing the legacy constant name.
- **Impact & Risk Assessment:**
  - **Current status:** None. Emitted from upstream FastAPI internal dependencies, not forum application codebase.
  - **Resolution:** Will be resolved automatically upon upstream FastAPI release adopting Starlette 0.45+.

---

## 3. Configuration & Suppression Plan

To prevent deprecation noise during continuous integration while preserving visibility of real application defects, `pyproject.toml` can optionally configure pytest filter warnings:

```toml
[tool.pytest.ini_options]
filterwarnings = [
    "ignore:Setting per-request cookies:DeprecationWarning:httpx",
    "ignore:'HTTP_422_UNPROCESSABLE_ENTITY' is deprecated:starlette.exceptions.StarletteDeprecationWarning",
]
```
For Task 020, warnings were kept unsuppressed to enable transparent documentation and audit evidence.
