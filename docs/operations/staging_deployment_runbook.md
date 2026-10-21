# Operational Runbook: Staging Deployment

**Document Version:** 1.0.0  
**Scope:** Staging Environment Deployment, Upgrades, and Health Verification  
**Target Environment:** Docker Compose Staging Stack  

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

### Step 3: Run Database Migrations
Execute Alembic migrations across all 6 logical databases before deploying application containers:
```bash
./venv/bin/python3 scripts/migrate.py upgrade all
./venv/bin/python3 scripts/migrate.py current all
```
Verify that all 6 services report their expected revision heads (`users_0002`, `content_0003`, `interactions_0002`, `moderation_0003`, `notifications_0002`, `search_0002`).

### Step 4: Deploy Microservices and API Gateway
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
2. Restore database state from the pre-deployment backup:
   ```bash
   ./scripts/backup/restore_databases.sh /path/to/pre_deployment_backup/
   ```
3. Checkout previous stable commit / tag.
4. Rebuild and launch containers:
   ```bash
   docker compose up -d --build
   ```
