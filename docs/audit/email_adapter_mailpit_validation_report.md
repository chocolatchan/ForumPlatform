# Email Adapter, Mailpit, and SMTP Isolation Acceptance Report

**Task:** 031.3.G — Authorized Runtime Refresh and Isolated Acceptance

**Date:** 2026-10-24

**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`

**Base commit:** `b38a693a7e140584cdf7eae0e1e3f67945cdb328`

**Overall verdict:** **PASS WITH EXPLICIT RISKS**

The previously blocked runtime acceptance is now complete. Only `user-service`
and `notification-service` were rebuilt and recreated. Both refreshed containers
run the shared email adapter and the per-container SMTP egress guard. All six
User Service email workflows delivered through Mailpit, notification outbox
delivery and idempotency were verified, and all 12 PostgreSQL lease tests passed
against an explicitly disposable migrated database.

No production deployment, external SMTP delivery, destructive database
operation, volume deletion, or unrelated container recreation was performed.

## 1. Executive Summary

| Area | Result |
|---|---|
| Authorized runtime refresh scope | PASS — only two services |
| User Service shared adapter integration | PASS |
| Notification Service shared adapter integration | PASS |
| SMTP guard in running containers | PASS |
| Live User workflows through Mailpit | PASS |
| Live Notification outbox through Mailpit | PASS |
| Notification idempotency | PASS |
| Failed delivery is not recorded as delivered | PASS |
| PostgreSQL lease tests | PASS — 12 of 12 |
| Disposable database isolation | PASS |
| Source and integration regression | PASS |
| Ruff / Pyright / `git diff --check` | PASS |
| External SMTP or production contact | None |

**Explicit risk:** the shared development User database already contained both
allowlisted test identities, so fresh registration and email-change OTPs could
not be generated there without destructive data changes. Those workflows were
executed in the refreshed User container using an isolated, explicitly disposable
User database and a temporary in-container User Service process. The same image,
guard, adapter configuration, and Mailpit sink were used. The final long-running
User container was recreated afterward to leave a clean runtime state.

## 2. Scope and Safety

Preserved rollback images before rebuilding:

| Service | Rollback tag | Image ID |
|---|---|---|
| user-service | `forumplatform-user-service:pre-0313g` | `sha256:4a1c1198a0ed1a33ac3c076016b7cf8aac1a0b9bd12840f519aaaa70396f3b90` |
| notification-service | `forumplatform-notification-service:pre-0313g` | `sha256:429d8167327eec2f316034068f4bc0eee681ba263876b388c8042c86e9de3552` |

No other service image was rebuilt or recreated. Compose reported existing orphan
test/rehearsal PostgreSQL containers; they were left untouched.

Runtime refresh commands:

```bash
docker tag 4a1c1198a0ed1a33ac3c076016b7cf8aac1a0b9bd12840f519aaaa70396f3b90 \
  forumplatform-user-service:pre-0313g
docker tag 429d8167327eec2f316034068f4bc0eee681ba263876b388c8042c86e9de3552 \
  forumplatform-notification-service:pre-0313g

INTERNAL_SERVICE_SECRET=default-dev-internal-key \
  docker compose build user-service notification-service

INTERNAL_SERVICE_SECRET=default-dev-internal-key \
  docker compose up -d --force-recreate --no-deps \
  user-service notification-service
```

The existing development internal key was used so authentication remained
compatible with the other running services. No `.env` file was modified.

After all workflow testing, only `user-service` was recreated once more from the
same new image to remove the temporary test process and leave a clean container.

## 3. Runtime Refresh Evidence

### 3.1 Image and entrypoint before refresh

| Container | Image ID | Entrypoint | Adapter | Guard |
|---|---|---|---|---|
| `forum_user_service` | `sha256:4a1c1198a0ed...` | `null` | absent | absent |
| `forum_notification_service` | `sha256:429d8167327e...` | `null` | absent | absent |

Both ran stale images with direct `smtplib` senders.

### 3.2 Image and entrypoint after refresh

| Container | Image ID | Entrypoint |
|---|---|---|
| `forum_user_service` | `sha256:490c33f5a7b35d7cf13be2f6b8ae2ef0ced203e34be940aa09aad1cdc8d3f4eb` | `/app/scripts/network/smtp_egress_guard.sh` |
| `forum_notification_service` | `sha256:628b02c37fb6377d2949aea5d55085ddd657a6c154cb384092c18a220ad5bd1d` | `/app/scripts/network/smtp_egress_guard.sh` |

Both containers reported healthy and logged:

```text
smtp-egress-guard: active for environment=development; allowing mailpit:1025; blocking ports 25 465 587 1025 2525
```

Read-only checks inside both refreshed containers confirmed:

```text
adapter=present
guard=present
iptables=present
otp_adapter=present
notification_adapter=present
```

The running commands are the normal User and Notification Uvicorn applications;
the guard is their entrypoint.

## 4. In-Container SMTP Guard Acceptance

### 4.1 Installed rules

Both containers enforce the same policy. Representative User Service rules:

```text
-N SMTP_EGRESS_GUARD
-A SMTP_EGRESS_GUARD -o lo -j RETURN
-A SMTP_EGRESS_GUARD -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
-A SMTP_EGRESS_GUARD -p udp -m udp --dport 53 -j RETURN
-A SMTP_EGRESS_GUARD -p tcp -m tcp --dport 53 -j RETURN
-A SMTP_EGRESS_GUARD -d 172.18.0.6/32 -p tcp -m tcp --dport 1025 -j RETURN
-A SMTP_EGRESS_GUARD -p tcp -m tcp --dport 25 -j REJECT --reject-with tcp-reset
-A SMTP_EGRESS_GUARD -p tcp -m tcp --dport 465 -j REJECT --reject-with tcp-reset
-A SMTP_EGRESS_GUARD -p tcp -m tcp --dport 587 -j REJECT --reject-with tcp-reset
-A SMTP_EGRESS_GUARD -p tcp -m tcp --dport 1025 -j REJECT --reject-with tcp-reset
-A SMTP_EGRESS_GUARD -p tcp -m tcp --dport 2525 -j REJECT --reject-with tcp-reset
```

`172.18.0.6` is Mailpit. The only SMTP allow rule is the Mailpit IPv4 address on
port `1025`.

### 4.2 Final probes from the actual containers

A temporary controlled listener at `172.18.0.12` was used. Connection-only
probes were run from both refreshed application containers:

```text
--- forum_user_service final probes ---
PASS: mailpit SMTP (actual=True, expected=True)
PASS: mailpit UI/API (actual=True, expected=True)
PASS: mailpit API 200 (actual=True, expected=True)
PASS: DNS (actual=True, expected=True)
PASS: controlled 172.18.0.12:25 blocked (actual=False, expected=False)
PASS: controlled 172.18.0.12:465 blocked (actual=False, expected=False)
PASS: controlled 172.18.0.12:587 blocked (actual=False, expected=False)
PASS: controlled 172.18.0.12:1025 blocked (actual=False, expected=False)
PASS: controlled 172.18.0.12:2525 blocked (actual=False, expected=False)
PASS: non-SMTP 8080 (actual=True, expected=True)
PASS: no IPv6 route (actual=False, expected=False)

--- forum_notification_service final probes ---
PASS: mailpit SMTP (actual=True, expected=True)
PASS: mailpit UI/API (actual=True, expected=True)
PASS: mailpit API 200 (actual=True, expected=True)
PASS: DNS (actual=True, expected=True)
PASS: controlled 172.18.0.12:25 blocked (actual=False, expected=False)
PASS: controlled 172.18.0.12:465 blocked (actual=False, expected=False)
PASS: controlled 172.18.0.12:587 blocked (actual=False, expected=False)
PASS: controlled 172.18.0.12:1025 blocked (actual=False, expected=False)
PASS: controlled 172.18.0.12:2525 blocked (actual=False, expected=False)
PASS: non-SMTP 8080 (actual=True, expected=True)
PASS: no IPv6 route (actual=False, expected=False)
```

### 4.3 Configuration bypass resistance

The guard's allowed route is fixed by `SMTP_GUARD_ALLOWED_HOST=mailpit` and
`SMTP_GUARD_ALLOWED_PORT=1025`; it does not use the application's `SMTP_HOST` or
`SMTP_PORT`. The live rule permits only Mailpit's IPv4 address on `1025`, and the
actual containers successfully rejected a non-Mailpit destination on `1025`.
Changing application SMTP host/port cannot widen this rule.

### 4.4 IPv6

The application network is IPv4-only: both refreshed containers reported
`no-ipv6-addresses`. `ip6tables` chains are installed and reject SMTP ports
`25`, `465`, `587`, `1025`, and `2525`; live IPv6 probes failed due to no IPv6
route. The earlier controlled IPv6 acceptance also passed.

## 5. Mailpit Health

- Container: `forum_mailpit`
- Image: `axllent/mailpit:v1.31.6`
- State: healthy
- Internal SMTP listener: `1025/tcp`
- Host UI/API: `127.0.0.1:8025`
- SMTP port `1025` is not host-published
- Final stored message count: `8`
- All stored messages were produced by controlled local acceptance tests
- No external recipient or provider was contacted

## 6. Live User Service Workflow Acceptance

### 6.1 Test data isolation

The development User database already contained `developer@example.test` and
`tester@example.test`, both allowlisted. To avoid deleting or mutating existing
accounts, the live User workflows used:

- Disposable database: `forum_users_t031_3_g` in
  `forum_test_postgres_t031_2_15` at `127.0.0.1:5433`
- Migration state: `users_0003`
- Temporary User Service process inside the refreshed `forum_user_service`
  container, bound only to `127.0.0.1:8011`
- Same image, guard, SMTP adapter, allowlist, and Mailpit sink as the running
  service
- Controlled identities: `developer@example.test`, then `tester@example.test`

The temporary process was stopped and the final User container was recreated.
The disposable databases were not dropped.

### 6.2 Results

| Workflow | Result | Mailpit evidence | State evidence |
|---|---|---|---|
| Registration OTP | PASS | 1 message, correct subject and recipient | Registration accepted `202` |
| Registration resend | PASS | New distinct message, correct subject and recipient | Resend accepted `202` |
| Registration verification | PASS | Used latest resent OTP | `201 Created` |
| Duplicate registration | PASS | No new message | Neutral `202` |
| Password recovery request | PASS | 1 message, correct subject and recipient | `202` |
| Password recovery resend | PASS | New distinct message, correct subject and recipient | `202` |
| Recovery verification | PASS | Used latest resent OTP | `200` |
| Email-change request | PASS | 1 message to new address, correct subject | `202` |
| Email-change resend | PASS | New distinct message to new address | `200` |
| Email-change verification | PASS | Used latest resent OTP | `200`, email updated |
| Unlisted recipient registration | PASS | No Mailpit message | Pending row `delivery_failed` |

Observed message groups in Mailpit:

```text
Your Forum Verification Code [Registration]  -> developer@example.test  count=2
Your Forum Verification Code [Recovery]      -> developer@example.test  count=2
Your Forum Verification Code [Email_change]  -> tester@example.test     count=2
```

The two messages per workflow are the initial request and the explicitly tested
resend. No third or duplicate message was produced. OTP values and reset tokens
were never printed or written to the report.

## 7. Notification Outbox Acceptance

The actual refreshed Notification Service accepted a controlled `UserRegistered`
event through `/internal/events` using the internal service key.

### 7.1 Successful delivery

```text
event_status=200
notifications_created=1
Mailpit subject=Welcome to ForumPlatform!
recipient=tester@example.test
```

Database row:

```text
event_id=268f1bfb-c67f-4a30-8ff7-2c74c6649f53
status=delivered
attempts=1
delivered_at set=true
```

The User registration outbox also produced a real integration welcome message:
the temporary User Service outbox event was delivered to the Notification Service,
which queued and delivered the welcome email to `developer@example.test`.
Its User outbox row was `UserRegistered`, status `delivered`, attempts `1`.

### 7.2 Duplicate event idempotency

The same event ID was submitted again:

```text
duplicate_event_status=200
notifications_created=0
new_email=False
```

Mailpit still contained exactly one welcome message for `tester@example.test`.

### 7.3 Failed delivery state

A controlled event with an unlisted recipient was accepted for processing:

```text
failed_recipient_event_status=200
new_mailpit_message=False
```

Its delivery row ended as:

```text
event_id=7895412e-8fcf-409a-912c-c64e6eccf67b
recipient=unlisted@external.com
status=dead_letter
attempts=1
delivered_at set=false
last_error=Recipient 'unlisted@external.com' not permitted by allowlist.
```

This confirms failed adapter delivery is not recorded as successful delivery.

## 8. PostgreSQL Lease Acceptance

Disposable Notification database:

```text
container: forum_test_postgres_t031_2_15
host port: 127.0.0.1:5433
database: forum_notifications_t031_3_g
migration head: notifications_0004
```

The database was explicitly created for this task, verified before migration, and
migrated with the project migration runner:

```bash
DATABASE_NOTIFICATIONS_URL='postgresql+asyncpg://<test-user>:<test-password>@127.0.0.1:5433/forum_notifications_t031_3_g' \
  ./venv/bin/python scripts/migrate.py upgrade notifications notifications_0004
```

Verification before tests:

```text
current_database=forum_notifications_t031_3_g
version_num=notifications_0004
```

Test command:

```bash
TEST_NOTIFICATIONS_DATABASE_URL='postgresql+asyncpg://<test-user>:<test-password>@127.0.0.1:5433/forum_notifications_t031_3_g' \
  ./venv/bin/pytest tests/test_email_worker_lease_pg.py -q
```

Actual result:

```text
12 passed in 3.48s
```

The tests cover stale lease recovery, active-lease preservation, concurrent
recovery, dead-letter transition, startup recovery, `sending_since` set/clear,
and concurrent claim behavior. The test database was not the development
`forum_notifications` database.

The isolated User database used for workflow testing was separately migrated to
`users_0003`; it was not used for the lease suite.

## 9. Regression Verification

```bash
./venv/bin/pytest \
  tests/common/email \
  tests/network \
  tests/test_config.py \
  tests/test_staging_operational_readiness.py \
  tests/test_email_delivery.py \
  tests/test_email_dispatch_worker.py \
  tests/test_user_auth_lifecycle.py \
  tests/test_security_hardening.py \
  -q
```

Actual result:

```text
112 passed in 19.42s
```

```bash
./venv/bin/pytest tests/test_e2e_integration.py tests/test_frontend_e2e.py -q
```

Actual result:

```text
10 passed in 10.18s
```

```bash
./venv/bin/ruff check \
  src/common/email \
  src/services/user/otp_delivery.py \
  src/services/user/config.py \
  src/services/notification/email_worker.py \
  src/services/notification/config.py \
  tests/common/email \
  tests/network \
  tests/test_email_delivery.py \
  tests/test_email_dispatch_worker.py \
  tests/test_user_auth_lifecycle.py \
  tests/test_security_hardening.py
```

Actual result:

```text
All checks passed!
```

```bash
./venv/bin/pyright --pythonpath ./venv/bin/python
git diff --check
```

Actual result:

```text
0 errors, 0 warnings, 0 informations
git diff --check: OK
```

## 10. Remaining Risks

1. **At-least-once notification delivery.** A crash after SMTP acceptance but
   before the `delivered` commit can cause redelivery. This is existing outbox
   semantics, not a regression.
2. **Isolated live User workflow database.** Registration and email-change live
   tests used a temporary in-container User Service process with a disposable
   database because the shared development database already contained both
   allowlisted accounts. The same refreshed image, guard, adapter, configuration,
   and Mailpit sink were used, but the long-running User process was not the
   process that generated those OTPs.
3. **IPv6 is not present on the application network.** Actual IPv6 SMTP traffic
   cannot occur on this IPv4-only network. `ip6tables` rejection rules are
   installed, and controlled IPv6 acceptance passed.
4. **Mailpit contains controlled test messages.** Eight messages remain from
   acceptance testing. They contain only test OTPs/welcome content and no
   external recipients.
5. **Orphan test/rehearsal PostgreSQL containers remain.** Compose reported them;
   they were intentionally left untouched because they are outside this task's
   scope.

## 11. Rollback Procedure

If rollback is required:

```bash
INTERNAL_SERVICE_SECRET=default-dev-internal-key \
  docker compose stop user-service notification-service

docker tag forumplatform-user-service:pre-0313g \
  forumplatform-user-service:latest
docker tag forumplatform-notification-service:pre-0313g \
  forumplatform-notification-service:latest

INTERNAL_SERVICE_SECRET=default-dev-internal-key \
  docker compose up -d --force-recreate --no-deps \
  user-service notification-service
```

The rollback images predate the shared adapter and guard. Do not use them with
real external SMTP; restoring them reduces the development/test SMTP isolation
guarantee.

The isolated databases `forum_users_t031_3_g` and
`forum_notifications_t031_3_g` remain in the disposable test PostgreSQL
container. They can be removed later only with explicit approval.

## 12. Tests Not Performed

None of the required Task 031.3.G tests were skipped. Production external-SMTP
acceptance remains out of scope and was intentionally not performed.
