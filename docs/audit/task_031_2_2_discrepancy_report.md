# Task 031.2.2 – User Service Source-to-Runtime Discrepancy Report

**Inspection date:** 2026-10-23 (local +0700)
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`
**Task type:** Investigation only — no code, test, migration, Docker, environment, container, or database changes
**Scope:** `POST /auth/recovery/*`, `/profile/change-email/*`, `/profile/avatar`, `GET /internal/users/{user_id}`

> This report separates **committed-source evidence**, **running-container evidence**, and **inference**. No application endpoint was called to prove behavior, and no state-changing operation was performed.

---

## 1. Initial Git and Container Identity Check

### 1.1 Git baseline

| Item | Observed value | Matches Task 031.2.1 baseline |
|---|---|---|
| Branch | `master` | ✅ |
| HEAD | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` | ✅ |
| `origin/master` (existing remote-tracking ref, no fetch) | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` | ✅ |
| Staged changes | None | ✅ |
| Modified (unstaged) | `.env.example`, `docker-compose.yml`, `src/services/notification/{config,email_worker,models}.py`, `tests/test_email_dispatch_worker.py` | ✅ same as inventory |
| Untracked | 3 × Task 031.1 audit docs, `task_031_2_1_inventory_report.md`, migration `20261022_0004_email_delivery_lease.py`, `tests/test_email_worker_lease_pg.py` | ✅ same as inventory |
| Stashes / extra worktrees / extra local branches | None / none / none | ✅ |

No difference from the baseline. Investigation proceeded.

### 1.2 Container identity

| Item | Observed value |
|---|---|
| Container | `forum_user_service` |
| Image | `forumplatform-user-service:latest` |
| Image ID | `sha256:4a1c1198a0ed1a33ac3c076016b7cf8aac1a0b9bd12840f519aaaa70396f3b90` |
| Image prefix | `4a1c1198a0ed` — ✅ matches reported baseline |
| Image created | `2026-10-22T16:33:42+07:00` |
| Container state | `running`, `healthy` |
| Container created | `2026-10-22T09:33:43Z` |

The container is the original image named in the baseline. It was not replaced, restarted, or rebuilt.

**Timing observation (relevant to provenance):** the image was built at **16:33 +0700**, whereas HEAD commit `bf3384c` was created at **22:41 +0700**, about six hours later. The image's `src/services/user/app.py` mtime is `2026-10-22 09:33:23Z` (= 16:33 +0700), consistent with the image being built from a working tree at that moment, before the commit existed.

### 1.3 Stack context (read-only)

All user-visible services are running and healthy: gateway, user, content, notification, moderation, search, interaction, Postgres, Redis, Mailpit.
Relevant counterpart: `forum_content_service` is running and contains both avatar-lifecycle internal endpoints (see §3.3).

---

## 2. Source Route Inventory (Committed HEAD `bf3384c`)

The worktree's `src/services/user/` files are unmodified relative to HEAD (they do not appear in `git status`), so the working-tree content equals committed source.

| Route | Registered at HEAD? | Handler symbol / location | Implementation status |
|---|---|---|---|
| `POST /auth/recovery/request` | Yes | `recovery_request` — `src/services/user/app.py:703–705` | **501 placeholder** (`not_implemented_response`) |
| `POST /auth/recovery/verify` | Yes | `recovery_verify` — `app.py:708–710` | **501 placeholder** |
| `POST /auth/recovery/reset` | Yes | `recovery_reset` — `app.py:713–715` | **501 placeholder** |
| `POST /profile/change-email/request` | Yes | `change_email_request` — `app.py:718–722` | **501 placeholder** |
| `POST /profile/change-email/verify` | Yes | `change_email_verify` — `app.py:725–729` | **501 placeholder** |
| `POST /profile/change-email/resend-otp` | **No** | — | **Absent** (no route, no handler) |
| `PUT /profile/avatar` | **No** | — | **Absent** (no route, no handler) |
| `DELETE /profile/avatar` | **No** | — | **Absent** (no route, no handler) |
| `GET /internal/users/{user_id}` | Yes | `get_internal_user_contact` — `app.py:953–987` | **Real implementation**, guarded by `Depends(verify_internal_service_key)` |

Committed-source totals: **24** `@app.*` route decorators; **6** `not_implemented_response` call sites.

### 2.1 Dependencies that are present in committed source

The schemas, models, and security helpers needed by the missing handlers **already exist**; only the endpoint bodies/registrations are missing.

| Dependency | Location |
|---|---|
| `RecoveryRequest`, `RecoveryVerifyRequest`, `ResetPasswordRequest` | `src/services/user/schemas.py:85, 89, 94` |
| `ChangeEmailRequest`, `ChangeEmailResendRequest`, `ChangeEmailVerifyRequest` | `schemas.py:104, 109, 126` |
| `SetAvatarRequest` | `schemas.py:113` |
| `OneTimeToken`, `PasswordHistory`, `EmailChangeRequest` | `src/services/user/models.py` |
| `Account.avatar_object_key` | `models.py:59` |
| `EmailChangeRequest.resend_count` / `resend_available_at` | `models.py:174–200` |
| `generate_reset_token` | `src/services/user/security.py:65` |
| `verify_internal_service_key` | `src/services/user/auth.py:138` |
| `content_service_url`, `internal_service_secret` | `src/common/config.py:30, 41` (inherited by `UserSettings`) |
| Avatar/email-change migration | `migrations/users/versions/20261022_0003_email_change_resend_columns.py` |

### 2.2 What committed HEAD can and cannot do

- Can: serve public profiles (`GET /users/{id}`), admin management, password change, and the internal contact endpoint.
- Cannot: perform any recovery flow, email-change flow, or avatar flow — the first two return the canonical 501 error; the third is not routed at all.
- The committed test suite already contains modules that exercise these routes:
  - `tests/test_recovery_concurrency_pg.py` (315 lines) — recovery concurrency.
  - `tests/test_avatar_email_change_pg.py` (359 lines) — `/profile/avatar`, `/profile/change-email/*`.
  - `tests/test_user_auth_lifecycle.py::test_password_recovery_and_set_reset_lifecycle` — `/auth/recovery/*`.
  
  These tests therefore assert behavior that the committed application cannot provide.

---

## 3. Runtime Route/Module Inventory (Container `4a1c1198a0ed`)

Method: imported the installed `app` inside the container and enumerated its route table; inspected installed source with `grep`/`sed`. This does not start the server or execute the lifespan handlers, and no HTTP request was made.

### 3.1 Route table

| Route | Methods | Handler | Handler line |
|---|---|---|---|
| `/auth/recovery/request` | POST | `recovery_request` | 869 |
| `/auth/recovery/verify` | POST | `recovery_verify` | 990 |
| `/auth/recovery/reset` | POST | `recovery_reset` | 1114 |
| `/profile/change-email/request` | POST | `change_email_request` | 1228 |
| `/profile/change-email/resend-otp` | POST | `change_email_resend_otp` | 1396 |
| `/profile/change-email/verify` | POST | `change_email_verify` | 1503 |
| `/profile/avatar` | PUT | `set_avatar` | 743 |
| `/profile/avatar` | DELETE | `delete_avatar` | 838 |
| `/internal/users/{user_id}` | — | **Not registered** | — |

Container totals: **1,848** lines in `src/services/user/app.py` (committed source: **985**); **26** `@app.*` decorators (committed: **24**).

The single occurrence of `not_implemented_response` in the container is the **import statement at line 17**, not a route body. The import is unused — consistent with a worktree in which the 501 stubs had been replaced by real handlers but the import line was not cleaned up.

### 3.2 Handler implementations present in the image

| Handler | Behavior evidenced by code inspection |
|---|---|
| `recovery_request` | DB-backed recovery flow; uses `OneTimeToken` records and `generate_reset_token`. (22 references to `OneTimeToken` in the recovery region.) |
| `recovery_verify` | OTP verification with single-use token semantics (code inspection only). |
| `recovery_reset` | Password reset with reset-ticket handling; returns `Response` (likely 204). |
| `change_email_request` | Creates/locks `EmailChangeRequest`, OTP issuance, anti-enumeration behavior. |
| `change_email_resend_otp` | Resend path using `EmailChangeRequest.resend_count` / `resend_available_at`. **No equivalent exists in committed source.** |
| `change_email_verify` | `SELECT … FOR UPDATE` on `EmailChangeRequest` and `Account`; duplicate-email guard returns 409; rate limiting (429); atomic email update. |
| `set_avatar` | Claims an attachment by calling Content Service `POST /internal/attachments/{image_id}/claim-avatar` via `httpx` with `X-Internal-Key`; maps 404 → `ATTACHMENT_NOT_FOUND`, 403 → authorization error, upstream failure → 502; success → 200. |
| `delete_avatar` | Clears `Account.avatar_object_key` under row lock, then best-effort `POST /internal/attachments/{old_id}/release-avatar`; returns 204 even if the release call fails (logs a warning). |

### 3.3 Content Service counterpart

Both the committed source and the running content container register the two internal endpoints the avatar handlers depend on:

| Endpoint | Committed source | Running `forum_content_service` |
|---|---|---|
| `POST /internal/attachments/{attachment_id}/claim-avatar` | `src/services/content/app.py:1520` | present (same line) |
| `POST /internal/attachments/{attachment_id}/release-avatar` | `src/services/content/app.py:1593` | present (same line) |

So the avatar integration is **not** missing on the content side; only the User Service routes are absent from committed source.

### 3.4 Runtime behavior status

The runtime handlers were **not executed**. Their presence and code paths are `CONFIRMED` by direct inspection, but their live HTTP behavior (status codes, email delivery, locking outcomes) is **NOT VERIFIED** in this task by design. The previously observed notification-service 404 on `/internal/users/{id}` (Task 031.1) is consistent with the route table above and is cited as prior evidence, not re-tested here.

---

## 4. Route Discrepancy Matrix

Labels: `CONFIRMED` = direct source/runtime evidence; `PARTIAL` = some dependencies/behavior present but full behavior not established; `NOT VERIFIED` = insufficient evidence; `CONFLICT` = sources disagree.

| Route/group | Committed source | Running container | Supporting evidence | Confidence | Follow-up |
|---|---|---|---|---|---|
| `POST /auth/recovery/request` | `CONFIRMED` — registered, **501 placeholder** (`app.py:703–705`) | `CONFIRMED` — registered, **real handler** (`app.py:869`) | Source body vs. container `def recovery_request`; route tables from both sides | `CONFIRMED` (divergence) | Restore handler from image reference; validate with `test_password_recovery_and_set_reset_lifecycle` |
| `POST /auth/recovery/verify` | `CONFIRMED` — registered, **501 placeholder** (`708–710`) | `CONFIRMED` — real handler (`990`) | Same | `CONFIRMED` (divergence) | Same; also `test_recovery_concurrency_pg.py` |
| `POST /auth/recovery/reset` | `CONFIRMED` — registered, **501 placeholder** (`713–715`) | `CONFIRMED` — real handler (`1114`) | Same | `CONFIRMED` (divergence) | Same |
| `/profile/change-email/request` | `CONFIRMED` — registered, **501 placeholder** (`718–722`) | `CONFIRMED` — real handler (`1228`) | Same | `CONFIRMED` (divergence) | Restore; validate with `test_avatar_email_change_pg.py` |
| `/profile/change-email/verify` | `CONFIRMED` — registered, **501 placeholder** (`725–729`) | `CONFIRMED` — real handler (`1503`) | Same; container logic reads `EmailChangeRequest`/`Account` with `FOR UPDATE`, 409 duplicate guard | `CONFIRMED` (divergence) | Same |
| `/profile/change-email/resend-otp` | `CONFIRMED` — **route and handler absent** | `CONFIRMED` — registered, real handler (`1396`) | `grep` of source finds no route; container route table lists it | `CONFIRMED` (divergence) | Restore; ensure migration `0003` columns exist |
| `PUT /profile/avatar` | `CONFIRMED` — **route and handler absent** | `CONFIRMED` — real handler (`743`) | No `avatar` route in source; container calls Content Service `claim-avatar` | `CONFIRMED` (divergence) | Restore; content endpoint already present |
| `DELETE /profile/avatar` | `CONFIRMED` — **route and handler absent** | `CONFIRMED` — real handler (`838`) | Same; container calls Content Service `release-avatar` | `CONFIRMED` (divergence) | Same |
| `GET /internal/users/{user_id}` | `CONFIRMED` — **real implementation** (`app.py:953–987`), internal-key protected | `CONFIRMED` — **absent** from route table | Source body vs. container route enumeration; no `internal/users` string in container `app.py` | `CONFIRMED` (divergence, opposite direction) | Preserve during restoration; add to image on rebuild |

**Whole-file observation:** the container is not a superset of HEAD. It has 8 target routes that HEAD lacks, and HEAD has 1 route the container lacks. A wholesale container→host file copy would silently delete the committed `/internal/users` endpoint and must not be used.

**Runtime correctness caveat:** "present in container" does not mean "correct." The handlers were read, not exercised; no claim is made about correctness, security, or exact match with the committed tests.

---

## 5. Relevant Local Git-History Findings

### 5.1 Searches performed (all read-only)

| Search | Result |
|---|---|
| `git log --all --oneline -- src/services/user/app.py` | `bf3384c`, `a8ebb02`, `6b1701f`, `524162f` (4 commits) |
| `git log --all -S "def set_avatar" -- src/services/user/` | **No results** |
| `git log --all -S "claim-avatar" -- src/services/user/` | **No results** |
| `git log --all -S "def change_email_resend_otp" -- src/services/user/` | **No results** |
| `git log --all -S "class SetAvatarRequest" -- src/services/user/` | `bf3384c` only (schema added, no handler) |
| `git log --all -S "def recovery_request" -- src/services/user/` | `524162f` (Initial commit) |
| `git log --all -S "recovery_reset" -- src/services/user/app.py` | `524162f` |
| `git log --all -S "not_implemented_response" -- src/services/user/app.py` | `524162f`, `a8ebb02` |
| `git grep -n "claim-avatar\|change_email_resend_otp\|class SetAvatarRequest" HEAD -- src/` | Only `src/services/content/app.py:1520` (`claim-avatar`) and `src/services/user/schemas.py:113` (`SetAvatarRequest`) |
| `git branch -a` | `master`, `origin/master` only |
| `git stash list` | Empty |
| `git fsck --unreachable` | Empty (no unreachable objects) |
| `git reflog --date=iso` | Commits at 07:33, 07:59, 08:13, 13:45, 13:49 (`reset: moving to HEAD~1`), 13:54, 22:41. No checkout/reset between 13:54 and the 22:41 commit. |

### 5.2 Key findings

1. **The handlers were never in local Git history.**
   - The initial commit `524162f` already contained the 501 stubs for recovery request/verify/reset and change-email request/verify. The stubs are not a late regression introduced at HEAD.
   - No commit, branch, stash, or unreachable object contains `set_avatar`, `change_email_resend_otp`, or `claim-avatar` on the User Service side.
   - `git grep HEAD` finds the Content Service's `claim-avatar` endpoint and the `SetAvatarRequest` schema, but not the User Service handlers that use them.

2. **HEAD's app.py change is only the internal endpoint.**
   `git show HEAD -- src/services/user/app.py` adds **36 lines**: the `verify_internal_service_key` import and the `GET /internal/users/{user_id}` handler. No recovery/avatar/email-change implementation was added by the commit.

3. **The commit claims work its code does not contain.**
   The commit message for `bf3384c` says Tasks 029 (recovery) and 030 (avatar + email change) are complete, and it commits `test_recovery_concurrency_pg.py` and `test_avatar_email_change_pg.py`, both of which call the missing routes. The committed application cannot satisfy those tests.

4. **The image predates the commit by ~6 hours.**
   Image build: `2026-10-22 16:33 +0700`. HEAD commit: `2026-10-22 22:41 +0700`. The image's `app.py` mtime is exactly the image build minute. The most defensible inference is: the Task 029/030 handlers existed in the worktree when the image was built, but were absent (replaced by/reverted to the pre-existing stubs) when `bf3384c` was committed. The image is the only surviving copy found by this investigation.

5. **The cause of the loss is not recoverable from available metadata.**
   The reflog shows no branch-moving operation between 13:54 and 22:41, but a path-scoped `git restore`/`git checkout -- src/services/user/app.py`, a manual overwrite, or an editor operation leaves no reflog entry. The image contains no `.git` directory and only the Compose builder label (`com.docker.compose.image.builder=classic`) — no Git SHA or build-revision label. The `tests/` directory is absent from the image (excluded by the build context/`.dockerignore`), so the exact build-context revision cannot be fingerprinted from tests.

### 5.3 What failed searches do and do not prove

The searches above **do not** prove the code never existed anywhere. They prove that, within this local repository's reachable history, reflog-retained history, refs, stashes, and unreachable objects, no User Service implementation of these handlers is present. They also establish that the implementation predates the commit in the working tree and survives in the image.

---

## 6. Exact Read-Only Commands and Inspection Methods Used

All commands were run from `/home/chocolat/Documents/Project/ForumPlatform`. No `git fetch`, checkout, reset, clean, stash, commit, or push was run. No container was started, stopped, restarted, recreated, or rebuilt. No service HTTP endpoint, database write, migration, email send, OTP, or password reset was executed.

### 6.1 Identity and worktree

```bash
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
git status --short
git branch -a
git stash list
docker inspect forum_user_service --format 'NAME={{.Name}} IMAGE={{.Image}} STATE={{.State.Status}} CREATED={{.Created}}'
docker image inspect forumplatform-user-service:latest --format 'ID={{.Id}} CREATED={{.Created}} TAGS={{json .RepoTags}}'
docker image inspect forumplatform-user-service:latest --format '{{json .Config.Labels}}'
docker ps --format '{{.Names}} {{.Image}} {{.Status}}'
```

### 6.2 Committed-source inspection

```bash
grep -n '^@app\.\(get\|post\|put\|patch\|delete\)' src/services/user/app.py
sed -n '695,790p' src/services/user/app.py
sed -n '790,960p' src/services/user/app.py
sed -n '948,1000p' src/services/user/app.py
grep -n 'avatar\|/internal/users\|change-email\|recovery' src/services/user/app.py
grep -n 'RecoveryRequest\|ChangeEmailRequest\|ResetPasswordRequest' src/services/user/schemas.py
grep -n 'email_change\|avatar' src/services/user/models.py
grep -n '^def \|^async def ' src/services/user/security.py
grep -rn 'content_service_url\|internal_service_secret' src/common/ src/services/user/ --include=*.py
grep -n 'claim-avatar\|release-avatar\|def claim_avatar\|def release_avatar' src/services/content/app.py
grep -n '^async def test_\|/auth/recovery\|/profile/change-email\|/profile/avatar\|/internal/users' \
  tests/test_avatar_email_change_pg.py tests/test_recovery_concurrency_pg.py tests/test_user_auth_lifecycle.py
```

### 6.3 Running-container inspection (non-mutating)

```bash
# Route table, by importing the installed app (does not start the server or call endpoints)
docker exec forum_user_service python -c "
from src.services.user.app import app
rows=[]
for r in app.routes:
    p=getattr(r,'path','')
    ep=getattr(r,'endpoint',None)
    methods=','.join(sorted(getattr(r,'methods',[]) or []))
    if 'internal' in p or 'avatar' in p or 'recovery' in p or 'change-email' in p:
        rows.append(f'{methods:22} {p:45} -> {getattr(ep,\"__name__\",\"-\")}')
print('\n'.join(sorted(rows)))
"

# Installed source inspection
docker exec forum_user_service sh -c "grep -n 'def recovery_request\|def recovery_verify\|def recovery_reset\|def change_email\|profile/avatar\|def .*avatar' src/services/user/app.py"
docker exec forum_user_service sh -c "grep -n -B3 -A3 'not_implemented_response' src/services/user/app.py"
docker exec forum_user_service sh -c "grep -n 'internal/users' src/services/user/app.py"
docker exec forum_user_service sh -c "sed -n '743,845p' src/services/user/app.py"
docker exec forum_user_service sh -c "sed -n '838,869p' src/services/user/app.py"
docker exec forum_user_service sh -c "sed -n '1503,1600p' src/services/user/app.py"
docker exec forum_user_service sh -c "wc -l src/services/user/app.py"
docker exec forum_user_service sh -c "stat -c '%y %n' src/services/user/app.py src/services/user/schemas.py src/services/user/models.py"
docker exec forum_user_service sh -c "grep -n 'content_service_url\|internal_service_secret' src/common/config.py src/services/user/app.py"
docker exec forum_user_service sh -c "grep -n 'email_change_resend\|resend_count\|last_resend' src/services/user/models.py"
docker exec forum_content_service sh -c "grep -n 'claim-avatar\|release-avatar' src/services/content/app.py"
```

### 6.4 Git provenance

```bash
git log --all --oneline -- src/services/user/app.py
git log --all --format='%h %ci %s' -- src/services/user/app.py
for s in "def recovery_request" "claim-avatar" "def change_email_resend_otp" "class SetAvatarRequest" "def set_avatar"; do
  git log --all --oneline -S "$s" -- src/services/user/
done
git log --all --oneline -S "recovery_reset" -- src/services/user/app.py
git log --all --oneline -S "not_implemented_response" -- src/services/user/app.py
git grep -n "claim-avatar\|change_email_resend_otp\|class SetAvatarRequest" HEAD -- src/
git show 524162f:src/services/user/app.py | grep -n -A12 "def recovery_request"
git show HEAD -- src/services/user/app.py
git show --stat --oneline HEAD
git reflog --date=iso
git fsck --unreachable
docker history forumplatform-user-service:latest --no-trunc
```

No command output containing secrets, tokens, passwords, OTPs, reset links, or full environment values is reproduced in this report.

---

## 7. Known Limitations and Unresolved Questions

### 7.1 Limitations

1. **Runtime code was read, not executed.** The container's handlers are confirmed to exist and their code paths were inspected, but no call was made to any recovery, change-email, or avatar endpoint. Live status codes, DB effects, email dispatch, and concurrency behavior are `NOT VERIFIED` here.
2. **Presence is not correctness.** The image contains implementations that the source lacks, but this report makes no claim that they are correct, secure, or exactly what the committed tests expect. They may themselves be an intermediate revision.
3. **The provenance cause is not provable with available metadata.** There is no `.git` in the image, no build-SHA label, and no reflog entry for a path-scoped restore/overwrite. The "image built from a pre-commit worktree, handlers lost before commit" conclusion is the best-supported inference, not a proven sequence.
4. **Git searches have scope limits.** They cover reachable history, retained reflog, refs, stashes, and unreachable objects in this clone. They cannot prove the code never existed in a discarded working tree or another machine.
5. **No database inspection.** The report does not verify whether migration `0003` (`email_change_requests.resend_count` / `resend_available_at`) is applied in the running database, nor whether `avatar_object_key` exists there. Committed migration and container models are present, but DB state is out of scope.
6. **Content Service behavior was verified only by route/text presence** in source and container; its claim/release logic was not exercised.
7. **The OpenAPI document was not fetched.** The route table was obtained by importing the installed app, which is read-only but does not prove the externally served schema at `/openapi.json`.
8. **Image build context may differ from HEAD beyond the inspected files.** Tests are excluded from the image, so the build context cannot be fingerprinted exactly.

### 7.2 Unresolved questions

- What operation removed the Task 029/030 handlers from the worktree between image build (16:33) and commit (22:41)? Available metadata cannot distinguish a path-scoped Git restore, a manual overwrite, or an editor/branch operation.
- Are the container's handlers the same revision that the committed tests were written against? Line-level code review and test execution would be needed.
- Was the notification worker's `/internal/users` call ever functional against this container? Prior evidence (Task 031.1) says no (404); this report confirms the route is absent from the image.
- Is the running database already migrated for `email_change_resend_columns`? Not checked.

---

## 8. Recommended Minimal Repair Sequence

Ordered by dependency and risk. The container image is currently the **only** copy of the Task 029/030 handler implementations, so preservation must precede any rebuild. This task stops at recommendation; none of these steps were performed.

### Step 0 — Preserve the only copy (do first, non-destructive)

- Tag the existing image under an immutable recovery name and export it outside the repo, e.g.:
  - `docker tag forumplatform-user-service:latest forumplatform-user-service:recovery-4a1c1198a0ed`
  - `docker save forumplatform-user-service:recovery-4a1c1198a0ed -o /tmp/opencode/user-service-4a1c1198a0ed.tar`
- This adds a tag and writes a tarball; it does not recreate or restart the container. Keep the running container untouched.

### Step 1 — Extract and inventory the reference implementation

- Copy the container's `src/services/user/app.py` to a non-repo reference path, e.g. `/tmp/opencode/user_app_container_4a1c1198a0ed.py`.
- Diff it against HEAD's `src/services/user/app.py` to isolate exactly:
  - the 8 handler blocks (3 recovery, 3 change-email, 2 avatar),
  - the imports they need (`httpx`, `Response`, `SetAvatarRequest`, `ChangeEmailResendRequest`, `OneTimeToken`, `generate_reset_token`, etc.),
  - any container-only helper code or behavioral differences.
- Do **not** copy the file wholesale: it lacks `GET /internal/users/{user_id}` and may differ elsewhere.

### Step 2 — Restore handlers into committed source (smallest justified next task)

- Merge the handler blocks into `src/services/user/app.py`, keeping the committed `/internal/users/{user_id}` handler and its import.
- Reconcile the import block (the container has an unused `not_implemented_response` import; the host still needs it nowhere after restoration, but keep the tree internally consistent).
- No schema/model/migration changes are expected: the required schemas, models, security helpers, config fields, and migration `0003` are already committed.
- **Smallest justified next repair task: Task 031.2.3 — source restoration only, validated locally, without rebuilding the image or touching the running container.**

### Step 3 — Validate source before building anything

- Static analysis: Ruff and Pyright on the touched module.
- Run the already-committed tests that define the expected behavior:
  - `tests/test_recovery_concurrency_pg.py`
  - `tests/test_avatar_email_change_pg.py`
  - `tests/test_user_auth_lifecycle.py::test_password_recovery_and_set_reset_lifecycle`
  - plus any related email-dispatch tests that exercise recipient resolution.
- This is the gate that distinguishes "matches the image" from "matches the committed acceptance criteria." If the tests and the image disagree, the tests win and the handler is corrected.

### Step 4 — Verify dependency readiness (read-only)

- Confirm migration `0003` is applied to the running users database (or apply it under a separately approved migration task).
- Confirm the Content Service's `claim-avatar` / `release-avatar` endpoints are reachable and key-protected (they exist in both source and the running content container).
- Confirm `CONTENT_SERVICE_URL` and `INTERNAL_SERVICE_SECRET` are configured for the User Service (names only; do not print values).

### Step 5 — Controlled rebuild and verification (separate task)

- Only after Steps 0–4 pass, build a new User Service image with an explicit tag, recreate the User Service container, and verify:
  - all 8 restored routes plus `/internal/users/{user_id}` are registered,
  - the notification worker no longer receives 404 when resolving recipients,
  - live recovery/email-change/avatar flows work against Mailpit and the allowlist,
  - the preserved `4a1c1198a0ed` image remains available for rollback until verification completes.
- Do not combine this with Step 2; mixing source restoration and image replacement makes rollback and attribution harder.

### Ordering rationale

| Step | Risk if skipped/reordered |
|---|---|
| Preserve image | Rebuilding first destroys the only known copy of the implementations |
| Merge, don't copy | Wholesale copy silently removes `/internal/users` and any other HEAD-only changes |
| Validate before build | A build would bake in unverified code and obscure whether failures are source or image related |
| Rebuild last | Container replacement is the irreversible, user-visible step; keep it isolated and reversible |

---

## Summary

The reported discrepancy is **real and material**, established independently from committed source and the running container:

- Committed HEAD: recovery and change-email endpoints are **501 placeholders**, `resend-otp` and avatar routes are **absent**, and `/internal/users/{user_id}` is **real**.
- Running `4a1c1198a0ed`: recovery, full change-email (including resend), and avatar set/delete are **real**, but `/internal/users/{user_id}` is **absent**.
- The implementations exist in **no local commit, branch, stash, or unreachable object**; they survive only in the container image, which was built ~6 hours before the commit that claims to deliver them.
- The container is **not a superset** of HEAD: each side has something the other lacks.
- The smallest justified next repair is **source restoration from the preserved image, validated by the already-committed tests**, with the image rebuild deferred to a separate, rollback-safe task.
