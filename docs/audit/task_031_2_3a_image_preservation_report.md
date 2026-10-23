# Task 031.2.3a – User Service Image Preservation and Reference Source Extraction

**Date:** 2026-10-23 (local +0700)
**Repository:** `/home/chocolat/Documents/Project/ForumPlatform`
**Task type:** Preservation / extraction only — no source edits, no rebuild, no container lifecycle changes
**Predecessor:** `docs/audit/task_031_2_2_discrepancy_report.md`

---

## 1. Initial HEAD and Worktree Summary

| Item | Observed value | Expected | Result |
|---|---|---|---|
| Branch | `master` | `master` | ✅ |
| HEAD | `bf3384c4c3b53b5f826ef119f2ad549997ff1d63` | same | ✅ |
| Staged changes | None | None | ✅ |
| Modified (unstaged) | `.env.example`, `docker-compose.yml`, `src/services/notification/{config,email_worker,models}.py`, `tests/test_email_dispatch_worker.py` | Task 031/031.1 work preserved | ✅ unchanged |
| Untracked | 3 × Task 031.1 audit docs, `task_031_2_1_inventory_report.md`, `task_031_2_2_discrepancy_report.md`, `migrations/notifications/versions/20261022_0004_email_delivery_lease.py`, `tests/test_email_worker_lease_pg.py` | preserved | ✅ unchanged |
| Stashes / extra branches / extra worktrees | None / none / none | none | ✅ |

The pre-existing uncommitted Task 031/031.1 work was present before this task and remains untouched. The only file added by this task is this report.

---

## 2. Image and Container Identity

| Item | Value |
|---|---|
| Container | `forum_user_service` |
| Container state | `running`, `healthy` (uptime ~10 hours) |
| Container image reference | `forumplatform-user-service:latest` |
| **Full image ID** | `sha256:4a1c1198a0ed1a33ac3c076016b7cf8aac1a0b9bd12840f519aaaa70396f3b90` |
| **Image ID prefix** | `4a1c1198a0ed` — ✅ matches baseline |
| Image created | `2026-10-22T16:33:42+07:00` |

The running container was not restarted, recreated, stopped, or removed. Its image reference still resolves to the same full image ID after this task.

---

## 3. Recovery Tag and Image Export

### 3.1 Recovery tag

- Pre-check: `forumplatform-user-service:recovery-4a1c1198a0ed` did **not** exist (`docker image inspect` returned “No such image”). No existing tag was retargeted.
- Tag created against the exact full image ID, not the mutable `latest` name:
  - `docker tag sha256:4a1c1198a0ed1a33ac3c076016b7cf8aac1a0b9bd12840f519aaaa70396f3b90 forumplatform-user-service:recovery-4a1c1198a0ed`
- Post-check: the recovery tag resolves to `sha256:4a1c1198a0ed1a33ac3c076016b7cf8aac1a0b9bd12840f519aaaa70396f3b90` — identical to the original image ID.

### 3.2 Export

| Item | Value |
|---|---|
| Path | `/tmp/opencode/user-service-4a1c1198a0ed.tar` |
| Directory | Existing `/tmp/opencode`, created if absent; outside the repository |
| Pre-check | Target file did **not** exist — no existing export was overwritten |
| Size | **108,246,016 bytes** (103.2 MiB) |
| SHA-256 | `cfbd035a36ee22ac08a2c22056c2925c5fc6fc18459e85b57b308e470d59f6f4` |
| Archive type | POSIX tar archive containing an OCI image layout |

Export command:

```bash
docker save -o /tmp/opencode/user-service-4a1c1198a0ed.tar \
  forumplatform-user-service:recovery-4a1c1198a0ed
```

---

## 4. Archive Readability / Integrity Verification

The archive was **not loaded** into Docker or containerd, and no container was changed. Readability was verified structurally:

| Check | Result |
|---|---|
| Valid POSIX tar readable by `tar` | ✅ PASS |
| OCI layout markers present (`index.json`, `manifest.json`, `oci-layout`) | ✅ PASS |
| Blob files present | 15 |
| Blobs referenced by manifest (config + layers) | 14 |
| Referenced blobs missing from archive | **0** |
| Blob SHA-256 mismatches (file content vs. filename digest) | **0** |
| Manifest `RepoTags` | `["forumplatform-user-service:recovery-4a1c1198a0ed"]` |
| `index.json` media type | `application/vnd.oci.image.index.v1+json` |
| `index.json` manifest digest | `sha256:4a1c1198a0ed1a33ac3c076016b7cf8aac1a0b9bd12840f519aaaa70396f3b90` |
| Index digest equals Docker image ID | ✅ PASS |
| Layer count in `manifest.json` | 13 |
| Archive checksum stable across verification | ✅ PASS |

This confirms the archive is a complete, internally consistent Docker-save/OCI image archive whose top-level manifest digest matches the preserved image ID.

**Deliberately skipped:** an actual `docker image load` / `ctr images import` was **not** executed, because loading would mutate the local image store and was excluded by the task. No `skopeo` or `crane` binary is installed on this host, so an independent archive reader could not be used. The structural checks above are the strongest non-loading verification available here.

---

## 5. Extracted Reference Source

| Item | Value |
|---|---|
| Source (in container) | `/app/src/services/user/app.py` |
| Extraction method | `docker cp forum_user_service:/app/src/services/user/app.py …` (read-only, file copied out) |
| Local reference path | `/tmp/opencode/user_app_container_4a1c1198a0ed.py` |
| Pre-check | Destination did **not** exist; nothing was overwritten |
| Size | 69,391 bytes |
| Line count | 1,848 |
| SHA-256 (local reference) | `7a1d3c6f4222703af6dcdb2bfde7ced1b7da10eb31f73b1e3efc46bbe51ea098` |
| SHA-256 (inside container) | `7a1d3c6f4222703af6dcdb2bfde7ced1b7da10eb31f73b1e3efc46bbe51ea098` |
| Match | ✅ identical |
| File non-empty | ✅ PASS |
| File edited after extraction | No |

The extracted file is a reference artifact only. No repository source file was created, replaced, or modified.

---

## 6. Handler and Route Presence/Absence Checks

### 6.1 Expected handlers present

| Handler | Definition line in reference file |
|---|---|
| `recovery_request` | 869 |
| `recovery_verify` | 990 |
| `recovery_reset` | 1114 |
| `change_email_request` | 1228 |
| `change_email_resend_otp` | 1396 |
| `change_email_verify` | 1503 |
| `set_avatar` | 744 (decorator at 743) |
| `delete_avatar` | 839 (decorator at 838) |

### 6.2 Expected route registrations present

| Route | Decorator line |
|---|---|
| `PUT /profile/avatar` | 743 |
| `DELETE /profile/avatar` | 838 |
| `POST /auth/recovery/request` | 868 |
| `POST /auth/recovery/verify` | 989 |
| `POST /auth/recovery/reset` | 1113 |
| `POST /profile/change-email/request` | 1224 |
| `POST /profile/change-email/resend-otp` | 1392 |
| `POST /profile/change-email/verify` | 1499 |

Additional confirmations:

- Total `@app.*` decorators: **26** (matches Task 031.2.2).
- `not_implemented_response` appears **only at the import line (17)**, not in any route body.
- Content Service integration references present: `claim-avatar` (line 758) and `release-avatar` (lines 816, 858).

### 6.3 Expected absence confirmed

| Check | Result |
|---|---|
| `GET /internal/users/{user_id}` string in reference file | **Absent** |
| `get_internal_user_contact` symbol in reference file | **Absent** |

Both absences match the Task 031.2.2 finding. The reference file therefore must be merged selectively, not copied wholesale.

---

## 7. Failed, Skipped, and Not-Applicable Checks

| Check | Status | Detail |
|---|---|---|
| Recovery tag pre-existence | ✅ Checked — absent | No conflict; tag created |
| Export overwrite protection | ✅ Checked — target absent | No overwrite |
| Archive tar readability | ✅ Passed | `tar` listing + OCI markers |
| Blob digest verification | ✅ Passed | 15/15 blobs match their filenames |
| Referenced blob completeness | ✅ Passed | 0 missing |
| Manifest/index identity | ✅ Passed | Index digest = full image ID |
| **Live `docker load` parse test** | ⚠️ **Skipped by design** | Would mutate the local image store; excluded by task |
| **Independent external archive reader** | ⚠️ **Skipped — unavailable** | `skopeo` and `crane` not installed |
| Reference extraction checksum match | ✅ Passed | Local SHA-256 equals container SHA-256 |
| Handler presence checks | ✅ Passed | All 8 definitions found |
| Internal endpoint absence check | ✅ Passed | No match |
| Repository source modification | ✅ None | `git status` unchanged apart from this report |
| Container lifecycle change | ✅ None | Same container, same image, still running/healthy |

No failures occurred. The two skipped checks are above; neither affects the preservation outcome.

---

## 8. Confirmation of No Modifications

- **Application code:** not modified. No file under `src/` was written.
- **Tests:** not modified.
- **Migrations:** not modified.
- **Docker Compose / environment files:** not modified.
- **Containers:** none rebuilt, restarted, recreated, stopped, or removed. `forum_user_service` remains `running`/`healthy` on the original image ID.
- **Repository Git state:** HEAD unchanged at `bf3384c4c3b53b5f826ef119f2ad549997ff1d63`; the pre-existing 6 modified and 7 untracked paths are unchanged; the only new repository file is this report.
- **Database:** not accessed or changed.
- **Secrets:** no environment variables, credentials, tokens, OTPs, or configuration values were printed or copied into this report.

Changes made by this task are limited to:

1. A Docker image tag: `forumplatform-user-service:recovery-4a1c1198a0ed`.
2. An exported archive: `/tmp/opencode/user-service-4a1c1198a0ed.tar`.
3. A reference source file: `/tmp/opencode/user_app_container_4a1c1198a0ed.py`.
4. This report.

---

## 9. Commands Used (Summary)

```bash
# Baseline
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
git status --short
docker inspect forum_user_service --format '{{.Image}} {{.State.Status}} {{.Created}}'
docker image inspect forumplatform-user-service:latest --format '{{.Id}}'
docker image inspect forumplatform-user-service:recovery-4a1c1198a0ed --format '{{.Id}}'   # pre-check

# Preservation
docker tag sha256:4a1c1198a0ed… forumplatform-user-service:recovery-4a1c1198a0ed
test -e /tmp/opencode/user-service-4a1c1198a0ed.tar                          # pre-check
docker save -o /tmp/opencode/user-service-4a1c1198a0ed.tar forumplatform-user-service:recovery-4a1c1198a0ed
sha256sum /tmp/opencode/user-service-4a1c1198a0ed.tar
file /tmp/opencode/user-service-4a1c1198a0ed.tar
tar -tf /tmp/opencode/user-service-4a1c1198a0ed.tar
tar -xOf /tmp/opencode/user-service-4a1c1198a0ed.tar manifest.json
tar -xOf /tmp/opencode/user-service-4a1c1198a0ed.tar index.json
# Python: read archive, SHA-256 every blob, compare to filename, parse manifest,
# cross-check referenced blobs, index digest, RepoTags

# Reference extraction
test -e /tmp/opencode/user_app_container_4a1c1198a0ed.py                    # pre-check
docker cp forum_user_service:/app/src/services/user/app.py /tmp/opencode/user_app_container_4a1c1198a0ed.py
sha256sum /tmp/opencode/user_app_container_4a1c1198a0ed.py
docker exec forum_user_service sha256sum src/services/user/app.py
wc -l /tmp/opencode/user_app_container_4a1c1198a0ed.py
grep -n 'async def recovery_request\|…\|async def delete_avatar' /tmp/opencode/user_app_container_4a1c1198a0ed.py
grep -n 'internal/users\|get_internal_user_contact' /tmp/opencode/user_app_container_4a1c1198a0ed.py  # expect no match
grep -n 'not_implemented_response' /tmp/opencode/user_app_container_4a1c1198a0ed.py
grep -c '^@app\.' /tmp/opencode/user_app_container_4a1c1198a0ed.py
grep -n 'claim-avatar\|release-avatar' /tmp/opencode/user_app_container_4a1c1198a0ed.py

# Final state
docker image inspect forumplatform-user-service:{latest,recovery-4a1c1198a0ed} --format '{{.Id}}'
docker ps --filter name=forum_user_service --format '{{.Names}} {{.Status}}'
sha256sum /tmp/opencode/user-service-4a1c1198a0ed.tar /tmp/opencode/user_app_container_4a1c1198a0ed.py
```

No secret values appear in any recorded output.

---

## Summary

Preservation succeeded:

- The original image is still identified by `sha256:4a1c1198a0ed1a33ac3c076016b7cf8aac1a0b9bd12840f519aaaa70396f3b90` and is additionally referenced by `forumplatform-user-service:recovery-4a1c1198a0ed`.
- A structurally verified 108,246,016-byte export exists at `/tmp/opencode/user-service-4a1c1198a0ed.tar` with SHA-256 `cfbd035a…f6f4`.
- A checksum-verified reference copy of `src/services/user/app.py` (1,848 lines) exists at `/tmp/opencode/user_app_container_4a1c1198a0ed.py` with SHA-256 `7a1d3c6f…a098`, containing all 8 target handlers and correctly lacking `GET /internal/users/{user_id}`.
- No repository application code, tests, migrations, or configuration were modified, and no container was rebuilt, restarted, or stopped.

Stopping here. Task 031.2.3b has not been started.
