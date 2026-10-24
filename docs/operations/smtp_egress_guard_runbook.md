# Operational Runbook: Per-Container Outbound SMTP Guard

**Scope:** Development and test Docker Compose workloads  
**Protected services:** `user-service`, `notification-service`  
**Mechanism:** Per-container Linux netfilter rules inside each container's own network namespace  
**Host firewall changes:** None

## 1. Policy

The guard applies only when `ENVIRONMENT` is exactly `development` or `test`.
It is skipped for `staging` and `production`; an unknown environment causes the
container to refuse to start rather than guess a policy.

Within each protected container, outbound TCP is blocked to these SMTP ports:

- `25` (SMTP)
- `465` (SMTP over implicit TLS)
- `587` (SMTP submission / STARTTLS)
- `1025` (Mailpit's SMTP listener)
- `2525` (common alternate SMTP port)

The only allowed SMTP route is:

```text
mailpit:1025
```

The allowed destination is resolved at container startup to Mailpit's IPv4
and IPv6 addresses. It is intentionally independent of the application's
`SMTP_HOST` / `SMTP_PORT` settings, so changing application email configuration
cannot widen the network allowlist.

The following remain allowed:

- Loopback traffic (`lo`).
- Established/related return traffic.
- DNS over UDP/TCP port `53`.
- All non-SMTP ports, including service-to-service traffic and Mailpit's UI/API
  on `mailpit:8025`.
- Mailpit UI/API access from the host through the existing `8025` mapping.

The policy is applied as a custom `SMTP_EGRESS_GUARD` chain inserted into the
container's `OUTPUT` chain. The container starts with `CAP_NET_ADMIN` only to
install the rules; the capability is removed from the application process with
`setpriv` before the application command runs.

## 2. Why This Mechanism

Docker Compose does not provide native per-destination egress filtering. Setting
the whole Compose network to `internal: true` would block all external traffic,
not just SMTP, and would be too broad. Host `DOCKER-USER` rules would require root
and would change host-wide networking, which is outside this task's authorization.

The per-container network namespace is therefore the narrowest available
enforcement point:

- No host-wide firewall changes.
- No changes to database, DNS, health-check, or service-to-service connectivity.
- No publication of Mailpit SMTP or additional SMTP ports.
- Rules disappear when the protected container is removed/recreated.

## 3. Applying the Guard

The guard files are:

- `scripts/network/smtp_egress_guard.sh`
- `Dockerfile` (installs `iptables` and `util-linux`)
- `docker-compose.yml` (entrypoint and `NET_ADMIN` for the two protected services)

After changing these files, rebuild only the protected services:

```bash
docker compose build user-service notification-service
```

Then recreate only those services when authorized:

```bash
docker compose up -d --force-recreate user-service notification-service
```

Confirm the guard startup message:

```bash
docker logs forum_user_service 2>&1 | grep smtp-egress-guard
docker logs forum_notification_service 2>&1 | grep smtp-egress-guard
```

Expected in development/test:

```text
smtp-egress-guard: active for environment=development; allowing mailpit:1025; blocking ports 25 465 587 1025 2525
```

Expected in staging/production:

```text
smtp-egress-guard: environment=staging is not protected; skipping SMTP egress rules
```

Inspect the namespace rules:

```bash
docker exec forum_user_service iptables -S OUTPUT
docker exec forum_user_service iptables -S SMTP_EGRESS_GUARD
docker exec forum_user_service ip6tables -S SMTP_EGRESS_GUARD
```

## 4. Verification

The automated verification script creates temporary networks/containers and a
controlled TCP listener. It does not send email and does not restart application
containers:

```bash
./scripts/network/verify_smtp_egress_guard.sh
```

It verifies:

- Mailpit SMTP `mailpit:1025` is reachable.
- Mailpit UI/API `mailpit:8025` is reachable.
- DNS resolution works.
- Non-Mailpit SMTP ports `25`, `465`, `587`, `1025`, and `2525` are rejected.
- A non-SMTP control port remains reachable.
- Mailpit port `25` is not allowed.

Static configuration tests:

```bash
./venv/bin/pytest tests/network -q
docker compose config --quiet
```

IPv6 verification: when the Docker daemon supports IPv6 networks, create a
temporary IPv6 network and run the same probe against an IPv6 listener. The guard
installs equivalent `ip6tables` rules. If IPv6 addresses are present but
`ip6tables` is unavailable, the guard refuses to start.

## 5. Limitations

- Mailpit's IP address is resolved at container startup. If Mailpit is recreated
  with a different address while the protected container remains running, SMTP to
  Mailpit will fail closed until the protected container is recreated. There is
  no automatic widening or fallback.
- SMTP-like traffic on arbitrary non-listed TCP ports is not blocked. The listed
  ports are the SMTP destinations identified in the repository. Add ports via
  `SMTP_GUARD_PORTS` only after review; blocking every TCP port would violate the
  narrow-policy requirement.
- The guard is per-container, not a host-wide or network-wide firewall. It protects
  only containers that use the guard entrypoint.
- Existing running application containers are not protected until they are
  rebuilt and recreated with the new image/entrypoint.
- This mechanism does not replace application-level allowlisting or Mailpit's own
  recipient restrictions.

## 6. Rollback

No host firewall rules were added, so no host-level rollback is required.

To remove the guard from a protected development/test environment:

1. Stop the protected services first so they do not run without the guard:
   ```bash
   docker compose stop user-service notification-service
   ```
2. Restore the prior `docker-compose.yml`, `Dockerfile`, and guard scripts from
   the repository history or the pre-change copy.
3. As a compensating control, keep development/test SMTP pointed at Mailpit and
   do not enable an external SMTP host. If the guard must be removed entirely,
   set `EMAIL_DELIVERY_BACKEND=memory` before starting the services or leave them
   stopped.
4. Rebuild and recreate only the affected services:
   ```bash
   docker compose build user-service notification-service
   docker compose up -d --force-recreate user-service notification-service
   ```

Removing a protected container also removes its network namespace and all guard
rules. Do not remove unrelated containers, networks, volumes, or database
resources.

The temporary resources created by `verify_smtp_egress_guard.sh` are removed by
its cleanup trap on exit, interrupt, or termination.

## 7. Verification Status

Static Compose/tests and controlled container network probes can verify the guard
mechanism without rebuilding the running application containers. Runtime
application egress is only protected after the two protected services are
recreated from the updated image.
