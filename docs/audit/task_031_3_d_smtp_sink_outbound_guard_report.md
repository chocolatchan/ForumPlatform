# Task 031.3.D — SMTP Sink and Outbound SMTP Guard Report

**Date:** 2026-10-24  
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`  
**Branch:** `master`  
**Base commit:** `b38a693a7e140584cdf7eae0e1e3f67945cdb328`  
**Scope:** Per-container outbound SMTP guard for development/test workloads

## 1. Summary

A narrow, per-container outbound SMTP guard is implemented for the two
email-sending application services:

- `user-service`
- `notification-service`

The guard installs `iptables`/`ip6tables` rules inside each container's own
network namespace. It allows the intended `mailpit:1025` route and blocks direct
SMTP connections to non-Mailpit destinations on ports `25`, `465`, `587`, `1025`,
and `2525`. DNS, loopback, established traffic, Mailpit UI/API, and normal
non-SMTP connectivity remain available.

No host-wide firewall or host network policy was changed. No application
container was restarted or recreated.

## 2. Inspected Topology

- All application services, PostgreSQL, Redis, and Mailpit currently share one
  Docker bridge network: `forumplatform_forum_network`.
- Network subnet: `172.18.0.0/16`.
- Mailpit address at inspection time: `172.18.0.6`.
- Mailpit SMTP listens on internal `1025`; only UI/API `8025` is published to the
  host.
- User and Notification services each had one network attachment at inspection
  time. The guard does not depend on a fixed container IP; it blocks by
  destination port and allows only Mailpit's resolved address.
- The Docker daemon supports IPv6 networks, but the project network is IPv4-only
  by default. IPv6 behavior was tested separately on a temporary IPv6 network.
- The host user cannot read or modify host iptables without root. No `sudo`
  action was taken. Host-wide `DOCKER-USER` changes were therefore not used.

## 3. Enforcement Mechanism

### Protected environments

- `ENVIRONMENT=development`
- `ENVIRONMENT=test`

### Unprotected environments

- `ENVIRONMENT=staging`
- `ENVIRONMENT=production`

The guard is skipped for staging/production. An unknown environment causes the
guard to refuse startup rather than guess a policy.

### Allowed route

- `mailpit:1025` only, resolved to Mailpit's IPv4 and IPv6 addresses at startup.

### Blocked destinations

Outbound TCP to these ports is rejected for every other destination:

- `25`
- `465`
- `587`
- `1025`
- `2525`

The blocked list includes Mailpit's own port so changing the application SMTP
hostname to an external endpoint on `1025` cannot bypass the guard. The allowed
Mailpit target is independent of `SMTP_HOST` / `SMTP_PORT`, so application
configuration changes cannot widen the network allowlist.

### Preserved connectivity

- Loopback.
- Established/related traffic.
- DNS over UDP/TCP `53`.
- Mailpit UI/API on `8025`.
- All non-SMTP ports.
- Database, Redis, health checks, and service-to-service traffic.

### Capability handling

The protected service container starts with `CAP_NET_ADMIN`, installs the rules,
then uses `setpriv` to remove `NET_ADMIN` from its own bounding, inheritable, and
ambient capability sets before executing the application. Rules persist after the
capability is dropped, preventing the application process from removing its own
guard.

## 4. Changed Files

| File | Change |
|---|---|
| `scripts/network/smtp_egress_guard.sh` | New per-container guard entrypoint. |
| `scripts/network/verify_smtp_egress_guard.sh` | New reproducible controlled-network verification script. |
| `Dockerfile` | Installs `iptables` and `util-linux` (`setpriv`). |
| `docker-compose.yml` | Adds guard entrypoint and `NET_ADMIN` only to `user-service` and `notification-service`; exposes guard defaults through the shared email environment anchor. |
| `.env.example` | Documents guard defaults and scope. |
| `.env.staging.example` | Documents that staging/production skip the guard. |
| `docs/operations/smtp_egress_guard_runbook.md` | New apply/verify/limitations/rollback runbook. |
| `docs/operations/staging_deployment_runbook.md` | Links the guard runbook from email configuration guidance. |
| `tests/network/test_smtp_egress_guard_config.py` | Static Compose/script/image checks. |

No changes were made to database, DNS, health-check, Mailpit port mappings, or
other services' network configuration.

## 5. Verification Evidence

### 5.1 Static configuration tests

Command:

```bash
./venv/bin/pytest tests/network tests/common/email -q
```

Result: `26 passed`.

Command:

```bash
INTERNAL_SERVICE_SECRET=<validation placeholder> docker compose config --quiet
```

Result: exit 0.

Command:

```bash
./venv/bin/ruff check tests/network src/common/email src/services/user/config.py src/services/notification/config.py
sh -n scripts/network/smtp_egress_guard.sh
sh -n scripts/network/verify_smtp_egress_guard.sh
git diff --check
```

Result: Ruff clean; shell syntax valid; whitespace check clean.

### 5.2 Actual controlled network test — IPv4

A controlled TCP listener was created on a temporary Docker network. The
protected probe container used the rebuilt application image and the real
`mailpit` service on `forumplatform_forum_network`. No email was sent.

Reproduction:

```bash
./scripts/network/verify_smtp_egress_guard.sh
```

Observed:

```text
smtp-egress-guard: active for environment=development; allowing mailpit:1025; blocking ports 25 465 587 1025 2525
-P OUTPUT ACCEPT
-A OUTPUT -j SMTP_EGRESS_GUARD
PASS: Mailpit SMTP mailpit:1025 reachable
PASS: DNS resolution for mailpit
PASS: Mailpit UI/API mailpit:8025 reachable
PASS: Non-Mailpit SMTP 172.21.0.2:25 blocked
PASS: Non-Mailpit SMTP 172.21.0.2:465 blocked
PASS: Non-Mailpit SMTP 172.21.0.2:587 blocked
PASS: Non-Mailpit SMTP 172.21.0.2:1025 blocked
PASS: Non-Mailpit SMTP 172.21.0.2:2525 blocked
PASS: Non-SMTP control port 8080 remains reachable
PASS: Mailpit port 25 not allowed
All guard probes passed.
```

The `172.21.0.2` address was a temporary controlled listener, not a real mail
provider.

### 5.3 Actual controlled network test — IPv6

A temporary Docker IPv6 network and IPv6 listener were used. The protected
container was attached to the IPv6 network after startup. `ip6tables` rules
blocked every tested SMTP port, while non-SMTP port `8080` remained reachable:

```text
PASS: IPv6 non-Mailpit SMTP [fd00:dead:beef:1::2]:25 blocked
PASS: IPv6 non-Mailpit SMTP [fd00:dead:beef:1::2]:465 blocked
PASS: IPv6 non-Mailpit SMTP [fd00:dead:beef:1::2]:587 blocked
PASS: IPv6 non-Mailpit SMTP [fd00:dead:beef:1::2]:1025 blocked
PASS: IPv6 non-Mailpit SMTP [fd00:dead:beef:1::2]:2525 blocked
PASS: IPv6 non-SMTP control port 8080 remains reachable
All IPv6 guard probes passed.
```

### 5.4 Capability and environment checks

- After startup, PID 1's effective capability set no longer included
  `CAP_NET_ADMIN`.
- `ENVIRONMENT=production` logged that the guard was skipped and left `OUTPUT`
  with only its default policy.
- Production startup also removed `NET_ADMIN` before executing the command.

## 6. Runtime Deployment Status

- The `forumplatform-user-service:latest` image was rebuilt for verification.
- The `forumplatform-notification-service` image was **not** rebuilt.
- Running application containers were **not** recreated or restarted.
- Existing containers remained `Up` and `healthy`.
- Temporary verification networks/containers were removed by cleanup traps.
- No email was sent to Mailpit or any external provider.
- No host firewall or host-wide network policy was changed.

Therefore, the guard is implemented and verified in controlled containers, but
the currently running `forum_user_service` and `forum_notification_service`
containers are still running their previous images/entrypoints and are not yet
protected at runtime. Runtime protection requires an authorized rebuild/recreate
of those two services.

## 7. Limitations

1. Mailpit's IP is resolved at startup. If Mailpit is recreated with a different
   IP while a protected container stays running, Mailpit SMTP fails closed until
   the protected container is recreated. There is no fallback or automatic
   widening.
2. The guard blocks the listed SMTP ports, not arbitrary non-standard ports.
   Blocking every TCP port would break the requirement to preserve non-SMTP
   connectivity. Additional ports must be reviewed and added explicitly.
3. The guard is per-container and applies only to services using the guard
   entrypoint. Other containers are unaffected.
4. The guard does not replace the application allowlist or Mailpit recipient
   restrictions.

## 8. Rollback Procedure

No host-level rules exist to roll back.

1. Stop the protected services first:
   ```bash
   docker compose stop user-service notification-service
   ```
2. Restore the previous `docker-compose.yml`, `Dockerfile`, and guard scripts.
3. Do not start a protected test environment with unprotected external SMTP.
   Keep SMTP pointed at Mailpit, or set `EMAIL_DELIVERY_BACKEND=memory` before
   starting without the guard.
4. Rebuild and recreate only the affected services:
   ```bash
   docker compose build user-service notification-service
   docker compose up -d --force-recreate user-service notification-service
   ```

Removing a container removes its network namespace and all guard rules. Do not
delete unrelated containers, networks, volumes, or database resources.

## 9. Static vs Runtime Distinction

- **Static configuration checks:** Compose parsing, script syntax, test suite.
- **Actual network tests:** Kernel netfilter enforcement was exercised in
  controlled IPv4 and IPv6 containers against Mailpit and a controlled non-Mailpit
  listener.
- **Not yet tested:** The currently running application containers, because they
  have not been recreated with the guard image/entrypoint.
