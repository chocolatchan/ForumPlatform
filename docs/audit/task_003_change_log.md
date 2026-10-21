# Task 003: Core Framework Setup and Database Migration Baseline Change Log

## Overview

Task 003 established the complete minimum executable foundation for the Forum Platform without implementing any business workflows (such as registration, OTP verification, login, thread creation, voting, or moderation actions).

This baseline includes:
1. Reusable async database engine and transaction management (`DatabaseSessionManager`).
2. Pydantic-based configuration management enforcing `postgresql+asyncpg://` database drivers.
3. Declarative base models for all 6 microservices adhering strictly to PostgreSQL naming conventions and decoupled schemas without cross-service foreign keys.
4. Six independent Alembic migration environments with async execution support and baseline migration files.
5. Unified migration CLI tool (`scripts/migrate.py`).
6. FastAPI service skeletons for all 6 microservices featuring `/health/live` and `/health/ready` endpoints with lifecycle connection pool cleanup.
7. High-performance API Gateway with priority route resolution, async streaming forwarding, hop-by-hop header removal, canonical request ID generation/propagation, and RFC 7807-compatible error envelopes for upstream timeouts (504) and outages (503).
8. Automated test suite verifying configuration validation, session management, routing, proxy error envelopes, and service health checks.

---

## Inventory of Created and Modified Files

### 1. Project Configuration & Dependencies
- `pyproject.toml`
  - Switched build-backend to `setuptools.build_meta`.
  - Added `greenlet>=3.0.0` and `sqlalchemy[asyncio]>=2.0.35` for async engine support under Python 3.14.
  - Added `pythonpath = ["."]` in pytest options.
- `pyrightconfig.yml`
  - Configured language server typing mode and include path.

### 2. Common Infrastructure (`src/common/`)
- `src/common/__init__.py`: Package initialization.
- `src/common/config.py`: `BaseServiceSettings` providing shared settings validation, environment variable resolution, and automatic coercion of `postgresql://` to `postgresql+asyncpg://`.
- `src/common/models.py`: Declarative `Base` with standard metadata naming conventions (`POSTGRES_NAMING_CONVENTION`), and `TimestampMixin`.
- `src/common/database.py`: `DatabaseSessionManager` encapsulating `create_async_engine`, `async_sessionmaker`, scoped unit-of-work sessions and transactions (`session()`, `transaction()`), database ping checks (`ping()`), and graceful engine disposal (`close()`).
- `src/common/logging.py`: Centralized logging configuration featuring regex-based log redaction filter for sensitive fields (passwords, tokens, cookies, secrets).

### 3. Service Skeletons (`src/services/`)
- **User Service (`src/services/user/`)**:
  - `config.py`: Service configuration (`UserSettings`).
  - `database.py`: Database session manager instance for `forum_users`.
  - `models.py`: ORM models for `UserAccount`, `UserRoleHistory`, `UserSession`, `PasswordResetToken`, and `UserOutboxEvent` in schema `users`.
  - `app.py`: FastAPI app with async lifespan database disposal, `/health/live`, and `/health/ready`.
- **Content Service (`src/services/content/`)**:
  - `config.py`: Service configuration (`ContentSettings`).
  - `database.py`: Database session manager instance for `forum_content`.
  - `models.py`: ORM models for `Category`, `Tag`, `Thread`, `ThreadTag`, `Comment`, `ImageAttachment`, and `ContentOutboxEvent` in schema `content`.
  - `app.py`: FastAPI app with `/health/live` and `/health/ready`.
- **Interaction Service (`src/services/interaction/`)**:
  - `config.py`: Service configuration (`InteractionSettings`).
  - `database.py`: Database session manager instance for `forum_interactions`.
  - `models.py`: ORM models for `ThreadVote`, `CommentVote`, `ThreadBookmark`, `ThreadViewDeduplication`, `UserReputation`, and `InteractionOutboxEvent` in schema `interactions`.
  - `app.py`: FastAPI app with `/health/live` and `/health/ready`.
- **Moderation Service (`src/services/moderation/`)**:
  - `config.py`: Service configuration (`ModerationSettings`).
  - `database.py`: Database session manager instance for `forum_moderation`.
  - `models.py`: ORM models for `ModerationReport`, `ModerationAction`, and `ModerationOutboxEvent` in schema `moderation`.
  - `app.py`: FastAPI app with `/health/live` and `/health/ready`.
- **Notification Service (`src/services/notification/`)**:
  - `config.py`: Service configuration (`NotificationSettings`).
  - `database.py`: Database session manager instance for `forum_notifications`.
  - `models.py`: ORM models for `NotificationPreference` and `NotificationItem` in schema `notifications`.
  - `app.py`: FastAPI app with `/health/live` and `/health/ready`.
- **Search Service (`src/services/search/`)**:
  - `config.py`: Service configuration (`SearchSettings`).
  - `database.py`: Database session manager instance for `forum_search`.
  - `models.py`: ORM models for `SearchDocument` in schema `search`.
  - `app.py`: FastAPI app with `/health/live` and `/health/ready`.

### 4. API Gateway (`src/gateway/`)
- `src/gateway/config.py`: Gateway settings (`GatewaySettings`) with upstream URLs and timeouts.
- `src/gateway/router.py`: `GatewayRouter` with ordered route pattern matching prioritizing specific sub-routes (e.g. `/threads/{id}/vote`, `/profile/bookmarks` -> Interaction Service) over coarse prefixes (`/threads/*` -> Content Service).
- `src/gateway/forwarder.py`: Async streaming forwarder with `X-Request-ID` extraction/generation, hop-by-hop header filtering, upstream error handling (504 timeout, 503 unavailable, 502 bad gateway), and canonical error responses.
- `src/gateway/app.py`: Gateway FastAPI application with pooled `httpx.AsyncClient` lifespan management and `/api/v1/{path:path}` proxy route.

### 5. Alembic Database Migrations (`migrations/` and `scripts/`)
- `migrations/script.py.mako`: Template for new revisions.
- `migrations/async_env.py`: Async migration engine adapter with automatic schema provisioning.
- `migrations/{users,content,interactions,moderation,notifications,search}/alembic.ini`: Independent config per service.
- `migrations/{users,content,interactions,moderation,notifications,search}/env.py`: Dedicated migration entry point per service binding to service-specific metadata.
- `migrations/{users,content,interactions,moderation,notifications,search}/versions/20261020_0001_initial_*.py`: Initial baseline migrations representing the reconciled DDL schemas.
- `scripts/migrate.py`: Unified CLI to upgrade, downgrade, inspect, or create revisions across all services or for an individual service.

### 6. Automated Test Suite (`tests/`)
- `tests/test_config.py`: Validates settings defaults, driver auto-conversion, and port overrides.
- `tests/test_database.py`: Validates `DatabaseSessionManager` lifecycle, closing, and exception guards.
- `tests/test_gateway.py`: Validates route resolution, health checks, unknown routes (404), upstream timeouts (504), and service outages (503).
- `tests/test_services.py`: Smoke tests `/health/live` across all 6 service apps.

---

## Architectural Compliance Verification
- **DEC-001 (Account Enumeration):** Framework establishes error envelope format supporting neutral response workflows.
- **DEC-002 (Database Isolation):** 6 discrete database session managers, 6 independent Alembic roots, zero foreign keys cross-linking databases.
- **DEC-003 (Thread Lifecycle):** `Thread` model and Content migration contain `lifecycle_status`, `is_locked`, and `is_pinned`.
- **DEC-004 (Account Reporting):** `ModerationReport` model and Moderation migration contain `reported_account_id` and check constraints validating report target cardinality.
- **DEC-005 (View Count Propagation):** `ThreadViewDeduplication` model created in Interaction Service and `view_count` counter established on `Thread` in Content Service, with transactional outbox tables ready for event relay.
