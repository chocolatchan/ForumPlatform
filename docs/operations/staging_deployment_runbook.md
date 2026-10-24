# Operational Runbook: Staging Deployment

**Document Version:** 1.1.0

**Scope:** Staging Environment Deployment, Read-Only Migration Verification, and Health Verification

**Target Environment:** Docker Compose Staging Stack (`ENVIRONMENT=staging`)

---

## 1. Prerequisites and Secrets Preparation

1. **Environment Configuration:**
   Copy `.env.staging.example` to `.env` on the staging host.
   Ensure all mandatory secrets meet security criteria:
   - `ENVIRONMENT=staging`
   - `DEBUG=false`
   - `SECRET_KEY`: Minimum 32 random characters.
   - `CSRF_SECRET`: Minimum 32 random characters.
   - `INTERNAL_SERVICE_SECRET`: Minimum 32 random characters (non-default).
   - `POSTGRES_PASSWORD`: Strong non-default password.
2. **Ports & Network Security:**
   - **Exposed externally:** Gateway on port `8000`.
   - **Internal only (blocked from public ingress via firewall/security groups):**
     - Microservices (`8001-8006`)
     - PostgreSQL (`5432`)
     - Redis (`6379`)
     - Mailpit SMTP (`1025`) and UI (`8025`)

---

## 2. Staging Deployment Sequence

### Step 1: Preflight Verification

Verify that the configuration rejects insecure defaults:

```bash
./venv/bin/pytest tests/test_staging_operational_readiness.py -k test_staging_configuration_rejects_unsafe_defaults -v
```

### Step 2: Database and Infrastructure Startup

Bring up the backing data stores first:

```bash
docker compose up -d postgres redis mailpit
docker compose ps
```

Wait until `forum_postgres` and `forum_redis` report `(healthy)`.

### Email Configuration Contract (Mailpit)

Development and test SMTP delivery uses the `mailpit` Compose service, not a
host loopback address:

| Setting | Value | Notes |
|---|---|---|
| `EMAIL_DELIVERY_BACKEND` | `smtp` | Use `memory` only for deterministic tests. |
| `DEV_SMTP_HOST` | `mailpit` | Compose maps this to `SMTP_HOST`; `localhost` is rejected in development/test. |
| `DEV_SMTP_PORT` | `1025` | Mailpit's internal SMTP listener. |
| `DEV_SMTP_USER` / `DEV_SMTP_PASSWORD` | empty | Non-production Mailpit must not receive credentials. |
| `DEV_SMTP_USE_TLS` / `DEV_SMTP_STARTTLS` | `false` | Mailpit development/test SMTP is plain text. |
| `MAILPIT_UI_PORT` | `8025` | Host-published UI/API port, not the SMTP endpoint. |

Recipient allowlists have been removed. Development/test delivers every
application-generated message to Mailpit while preserving its original
recipient metadata. Production uses a separate transport configuration,
permits ordinary external domains, and suppresses the exact `test.dev` domain
and its subdomains before SMTP transport.

The same settings are supplied to the User and Notification services from the
`x-email-environment` anchor in `docker-compose.yml`. Both services depend on a
healthy `mailpit` container. The shared adapter validates the contract at
initialization and does not fall back to another SMTP endpoint when Mailpit is
unavailable.

Validate the effective Compose configuration without starting or rebuilding
containers:

```bash
docker compose config --quiet
```

Production is intentionally separate: Mailpit and loopback SMTP hosts are
rejected in production, so production requires an explicitly configured external
SMTP host and credentials. Do not reuse this Mailpit sample as production
configuration.

Development/test User and Notification containers also use the per-container
outbound SMTP guard documented in
`docs/operations/smtp_egress_guard_runbook.md`. The guard is skipped in
staging/production.

### Step 3: Verify Migration State Read-Only

**Target environment:** `staging`

**Target databases:** `forum_users`, `forum_content`, `forum_interactions`, `forum_moderation`, `forum_notifications`, `forum_search`

Expected current heads from the repository migration graph:

| Service | Database | Schema/version table | Expected head |
|---|---|---|---|
| users | `forum_users` | `users.alembic_version_users` | `users_0003` |
| content | `forum_content` | `content.alembic_version_content` | `content_0003` |
| interactions | `forum_interactions` | `interactions.alembic_version_interactions` | `interactions_0002` |
| moderation | `forum_moderation` | `moderation.alembic_version_moderation` | `moderation_0003` |
| notifications | `forum_notifications` | `notifications.alembic_version_notifications` | `notifications_0004` |
| search | `forum_search` | `search.alembic_version_search` | `search_0002` |

#### Offline graph inspection (no database connection)

Inspect the revision files under `migrations/<service>/versions/` and confirm that each service has one head: `users_0003`, `content_0003`, `interactions_0002`, `moderation_0003`, `notifications_0004`, `search_0002`. This repository-level check is safe because it does not contact PostgreSQL.

#### Read-only database verification

After the staging databases exist, use explicit database names and read-only `SELECT` statements:

```bash
docker compose exec -T postgres psql -U forum_admin -d forum_users \
  -c "SELECT version_num FROM users.alembic_version_users;"
docker compose exec -T postgres psql -U forum_admin -d forum_content \
  -c "SELECT version_num FROM content.alembic_version_content;"
docker compose exec -T postgres psql -U forum_admin -d forum_interactions \
  -c "SELECT version_num FROM interactions.alembic_version_interactions;"
docker compose exec -T postgres psql -U forum_admin -d forum_moderation \
  -c "SELECT version_num FROM moderation.alembic_version_moderation;"
docker compose exec -T postgres psql -U forum_admin -d forum_notifications \
  -c "SELECT version_num FROM notifications.alembic_version_notifications;"
docker compose exec -T postgres psql -U forum_admin -d forum_search \
  -c "SELECT version_num FROM search.alembic_version_search;"
```

Do **not** use `scripts/migrate.py current` as a read-only verification command. The shared Alembic environment (`migrations/async_env.py`) executes `CreateSchema(..., if_not_exists=True)` even during `current`, so it may issue DDL. Do not apply migrations across every service in one operation; applying migrations is a separate mutating operation and must target an explicitly named environment, service, and database under an approved migration procedure.

If the staging databases are empty and migrations must be applied, stop here. Treat migration application as a separate approved change with a verified backup and explicit per-database targeting. Never apply migrations to a live/shared database during a verification or restore drill.

### Step 4: Deploy Microservices and API Gateway

Before recreating application containers, apply the ordered internal-authentication
transition and rollback plan in
`docs/audit/task_031_2_10_internal_auth_deployment_readiness_report.md`. Source-only
changes do not update already-running containers.

Build and launch the application containers:

```bash
docker compose build
docker compose up -d
```

### Step 5: Post-Deployment Smoke Verification

Check container health:

```bash
docker compose ps
```

All 10 containers must report `Up ... (healthy)`.

Verify Gateway routing and metrics:

```bash
curl -f http://localhost:8000/health/ready
curl -f http://localhost:8000/metrics
```

---

## 3. Rollback Procedure

In the event of a failed deployment or severe regression:

1. Stop application containers:
   ```bash
   docker compose stop gateway user-service content-service interaction-service moderation-service notification-service search-service
   ```
2. Roll back the application image/commit separately from the database. Checkout the previous stable commit or tag.
3. If a database restore is required, do **not** restore into a live/shared staging database. Use the backup/restore runbook and verify the completed backup and disposable target first:
   ```bash
   ./scripts/backup/restore_databases.sh \
     --backup-dir /secure/backups/20261021_120000Z \
     --target-prefix restore_drill_20261023_ \
     --environment staging \
     --verify-only
   ```
   The restore script verifies `SHA256SUMS` and rejects live database names. Any destructive restore must use the same explicit `--target-prefix`, an exact non-empty `--confirm-target`, and approved disposable databases.
4. Rebuild and launch containers:
   ```bash
   docker compose up -d --build
   ```
