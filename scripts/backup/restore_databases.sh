#!/usr/bin/env bash
# =============================================================================
# Automated Restore Script for Forum Platform Logical PostgreSQL Databases
#
# This script ONLY restores into disposable databases whose names begin with an
# explicitly supplied "restore_" prefix. It never restores into the live
# application database names. Live/shared names are rejected before any
# Docker, psql, DROP DATABASE, CREATE DATABASE, or pg_restore call is made.
#
# Required for every invocation:
#   --backup-dir <dir>
#   --target-prefix restore_..._
#   --environment <development|test|staging|production>
#
# Required for a destructive restore (all invocations without --verify-only):
#   --confirm-target <exact target prefix>
#
# Optional:
#   --verify-only   Validate the target and backup checksums, then exit without
#                   connecting to PostgreSQL or invoking Docker.
#
# SHA256SUMS is produced by scripts/backup/backup_databases.sh and covers
# exactly the six .dump artifacts. It is verified before any destructive action.
#
# Does NOT hardcode or leak credentials.
# =============================================================================
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

DATABASES=(
    "forum_users"
    "forum_content"
    "forum_interactions"
    "forum_moderation"
    "forum_notifications"
    "forum_search"
)

LIVE_DATABASES=(
    "forum_users"
    "forum_content"
    "forum_interactions"
    "forum_moderation"
    "forum_notifications"
    "forum_search"
    "postgres"
    "template0"
    "template1"
)

ALLOWED_ENVIRONMENTS=(
    "development"
    "test"
    "staging"
    "production"
)

CHECKSUM_FILE_NAME="SHA256SUMS"
MANIFEST_FILE_NAME="backup_manifest.json"
TARGET_PREFIX_PATTERN='^restore_[a-z0-9_]{1,29}_$'

BACKUP_SRC_DIR=""
TARGET_PREFIX=""
TARGET_ENV=""
CONFIRM_TARGET=""
VERIFY_ONLY=0

usage() {
    cat <<'EOF'
Usage:
  restore_databases.sh --backup-dir <dir> --target-prefix <restore_..._> \
      --environment <development|test|staging|production> --verify-only

  restore_databases.sh --backup-dir <dir> --target-prefix <restore_..._> \
      --environment <development|test|staging|production> \
      --confirm-target <same-target-prefix>

Options:
  --backup-dir       Completed backup directory containing six .dump files,
                     backup_manifest.json, and SHA256SUMS.
  --target-prefix    Required disposable database prefix. Must start with
                     "restore_", end with "_", and contain only lowercase
                     letters, digits, and underscores.
  --environment      Explicit target environment. Unknown values are rejected.
  --confirm-target   Exact, non-empty copy of --target-prefix. Required for a
                     destructive restore; not required with --verify-only.
  --verify-only      Validate target, manifest, and SHA-256 checksums only.
  -h, --help         Show this help.

No live/shared database can be selected: every target is prefixed and checked
against the live database denylist before any destructive action.

Examples:
  restore_databases.sh \
    --backup-dir /secure/backups/20261023_120000Z \
    --target-prefix restore_drill_20261023_ \
    --environment test \
    --verify-only

  restore_databases.sh \
    --backup-dir /secure/backups/20261023_120000Z \
    --target-prefix restore_drill_20261023_ \
    --environment test \
    --confirm-target restore_drill_20261023_
EOF
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

need_value() {
    local option="$1"
    local value="${2:-}"
    if [ -z "${value}" ]; then
        fail "${option} requires a non-empty value"
    fi
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --backup-dir)
            need_value "$1" "${2:-}"
            BACKUP_SRC_DIR="$2"
            shift 2
            ;;
        --target-prefix)
            need_value "$1" "${2:-}"
            TARGET_PREFIX="$2"
            shift 2
            ;;
        --environment)
            need_value "$1" "${2:-}"
            TARGET_ENV="$2"
            shift 2
            ;;
        --confirm-target)
            need_value "$1" "${2:-}"
            CONFIRM_TARGET="$2"
            shift 2
            ;;
        --verify-only)
            VERIFY_ONLY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "Unknown argument: $1"
            ;;
    esac
done

[ -n "${BACKUP_SRC_DIR}" ] || fail "Missing required --backup-dir (no default target is permitted)"
[ -n "${TARGET_PREFIX}" ] || fail "Missing required --target-prefix (empty prefix would target live databases)"
[ -n "${TARGET_ENV}" ] || fail "Missing required --environment"

case "${TARGET_ENV}" in
    development|test|staging|production) ;;
    *) fail "Unrecognized environment '${TARGET_ENV}'. Expected one of: ${ALLOWED_ENVIRONMENTS[*]}" ;;
esac

if [[ ! "${TARGET_PREFIX}" =~ ${TARGET_PREFIX_PATTERN} ]]; then
    fail "Unsafe or ambiguous target prefix '${TARGET_PREFIX}'. It must match ${TARGET_PREFIX_PATTERN} (a trailing underscore is required)."
fi

[ -d "${BACKUP_SRC_DIR}" ] || fail "Backup directory does not exist: ${BACKUP_SRC_DIR}"

case "$(basename "${BACKUP_SRC_DIR}")" in
    *.partial) fail "Refusing to restore from a .partial backup directory: ${BACKUP_SRC_DIR}" ;;
esac
[ ! -e "${BACKUP_SRC_DIR}/INCOMPLETE" ] || fail "Refusing to restore from a backup marked INCOMPLETE: ${BACKUP_SRC_DIR}"

MANIFEST_FILE="${BACKUP_SRC_DIR}/${MANIFEST_FILE_NAME}"
CHECKSUM_FILE="${BACKUP_SRC_DIR}/${CHECKSUM_FILE_NAME}"

[ -f "${MANIFEST_FILE}" ] || fail "Missing ${MANIFEST_FILE_NAME} in backup directory"
grep -Eq '"status"[[:space:]]*:[[:space:]]*"completed"' "${MANIFEST_FILE}" \
    || fail "Backup manifest does not declare status=completed"
grep -Eq '"checksum_algorithm"[[:space:]]*:[[:space:]]*"sha256"' "${MANIFEST_FILE}" \
    || fail "Backup manifest does not declare sha256 checksums"

[ -f "${CHECKSUM_FILE}" ] || fail "Missing ${CHECKSUM_FILE_NAME} in backup directory"
[ -s "${CHECKSUM_FILE}" ] || fail "${CHECKSUM_FILE_NAME} is empty"

# Validate every target name before any destructive action. The prefixed names
# must never equal a live/shared database name.
for base_db in "${DATABASES[@]}"; do
    target_db="${TARGET_PREFIX}${base_db}"
    if [ "${#target_db}" -gt 63 ]; then
        fail "Target database name exceeds PostgreSQL's 63-byte limit: ${target_db}"
    fi
    for live_db in "${LIVE_DATABASES[@]}"; do
        if [ "${target_db}" = "${live_db}" ]; then
            fail "Refusing live/shared database target: ${target_db}"
        fi
    done
done

# Confirmation cannot be satisfied by an empty/default value, and it must be the
# exact prefix that is about to be used. --verify-only performs no mutation and
# therefore does not require confirmation.
if [ -n "${CONFIRM_TARGET}" ] && [ "${CONFIRM_TARGET}" != "${TARGET_PREFIX}" ]; then
    fail "--confirm-target must exactly match --target-prefix"
fi
if [ "${VERIFY_ONLY}" -eq 0 ] && [ -z "${CONFIRM_TARGET}" ]; then
    fail "Destructive restore requires a non-empty --confirm-target matching --target-prefix"
fi

# Validate checksum file shape before invoking sha256sum. The file must contain
# exactly one standard "hash  filename" line for each of the six .dump files.
entry_count=$(wc -l < "${CHECKSUM_FILE}" | tr -d '[:space:]')
[ "${entry_count}" = "${#DATABASES[@]}" ] \
    || fail "${CHECKSUM_FILE_NAME} must contain exactly ${#DATABASES[@]} entries (found ${entry_count})"

for base_db in "${DATABASES[@]}"; do
    dump_file="${BACKUP_SRC_DIR}/${base_db}.dump"
    [ -f "${dump_file}" ] || fail "Missing required dump artifact: ${base_db}.dump"
    count=$(grep -Ec "^[0-9a-f]{64}  ${base_db}\\.dump$" "${CHECKSUM_FILE}" || true)
    [ "${count}" = "1" ] \
        || fail "${CHECKSUM_FILE_NAME} must contain exactly one well-formed entry for ${base_db}.dump"
done

# Fail closed before any Docker/psql action if any checksum is malformed or does
# not match. sha256sum output is suppressed to avoid noisy content; the generic
# error is sufficient and does not expose backup contents.
if ! (cd "${BACKUP_SRC_DIR}" && sha256sum -c --strict "${CHECKSUM_FILE_NAME}" >/dev/null 2>&1); then
    fail "Backup checksum verification failed; refusing to restore"
fi

echo "=========================================================="
echo "Backup source:  ${BACKUP_SRC_DIR}"
echo "Environment:    ${TARGET_ENV}"
echo "Target prefix:  ${TARGET_PREFIX}"
echo "Checksums:      verified (${CHECKSUM_FILE_NAME}, six .dump files)"
echo "=========================================================="

if [ "${VERIFY_ONLY}" -eq 1 ]; then
    for base_db in "${DATABASES[@]}"; do
        echo "VERIFIED target candidate: ${TARGET_PREFIX}${base_db}"
    done
    echo "VERIFICATION COMPLETE: no database or container was contacted or modified."
    exit 0
fi

for base_db in "${DATABASES[@]}"; do
    target_db="${TARGET_PREFIX}${base_db}"
    dump_file="${BACKUP_SRC_DIR}/${base_db}.dump"

    # Re-check immediately before the destructive statement. The target was
    # already validated above; this keeps the guard local to the mutation.
    for live_db in "${LIVE_DATABASES[@]}"; do
        if [ "${target_db}" = "${live_db}" ]; then
            fail "Refusing live/shared database target: ${target_db}"
        fi
    done

    echo "Restoring ${base_db}.dump -> ${target_db}"
    docker compose -f "${PROJECT_ROOT}/docker-compose.yml" exec -T postgres \
        psql -v ON_ERROR_STOP=1 -U forum_admin -d postgres \
        -c "DROP DATABASE IF EXISTS \"${target_db}\";"
    docker compose -f "${PROJECT_ROOT}/docker-compose.yml" exec -T postgres \
        psql -v ON_ERROR_STOP=1 -U forum_admin -d postgres \
        -c "CREATE DATABASE \"${target_db}\" OWNER forum_admin;"
    docker compose -f "${PROJECT_ROOT}/docker-compose.yml" exec -T postgres \
        pg_restore -U forum_admin -d "${target_db}" --no-owner --single-transaction --exit-on-error < "${dump_file}"
    echo "Restored ${target_db}"
done

echo "=========================================================="
echo "SUCCESS: Disposable restore complete for prefix '${TARGET_PREFIX}'"
echo "Live/shared databases were not targeted."
exit 0
