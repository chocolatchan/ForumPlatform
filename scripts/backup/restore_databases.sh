#!/usr/bin/env bash
# =============================================================================
# Automated Restore Script for Forum Platform Logical PostgreSQL Databases
# Restores into designated database names (e.g. isolated test databases).
# Supports prefixing (e.g. restore into test_restore_forum_users).
#
# Does NOT hardcode or leak credentials.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

BACKUP_SRC_DIR="${1:-}"
TARGET_PREFIX="${2:-}" # Optional prefix e.g. "isolated_restore_"

if [ -z "${BACKUP_SRC_DIR}" ] || [ ! -d "${BACKUP_SRC_DIR}" ]; then
    echo "Usage: $0 <path_to_backup_directory> [target_prefix]" >&2
    echo "Example: $0 ./backups/20261021_120000Z isolated_" >&2
    exit 1
fi

DATABASES=(
    "forum_users"
    "forum_content"
    "forum_interactions"
    "forum_moderation"
    "forum_notifications"
    "forum_search"
)

echo "=========================================================="
echo "Starting PostgreSQL restore from: ${BACKUP_SRC_DIR}"
echo "Target DB prefix: '${TARGET_PREFIX}'"
echo "=========================================================="

FAILED=0

for base_db in "${DATABASES[@]}"; do
    target_db="${TARGET_PREFIX}${base_db}"
    dump_file="${BACKUP_SRC_DIR}/${base_db}.dump"

    if [ ! -f "${dump_file}" ]; then
        echo "ERROR: Backup file ${dump_file} not found!" >&2
        FAILED=1
        continue
    fi

    echo -n "Restoring ${base_db} -> ${target_db} ... "

    # 1. Ensure target database exists (drop and create if prefixed to ensure clean state)
    if [ -n "${TARGET_PREFIX}" ]; then
        docker compose -f "${PROJECT_ROOT}/docker-compose.yml" exec -T postgres psql -U forum_admin -d postgres -c "DROP DATABASE IF EXISTS ${target_db};" >/dev/null 2>&1 || true
        docker compose -f "${PROJECT_ROOT}/docker-compose.yml" exec -T postgres psql -U forum_admin -d postgres -c "CREATE DATABASE ${target_db} OWNER forum_admin;" >/dev/null
    fi

    # 2. Run pg_restore streaming dump file into container
    if docker compose -f "${PROJECT_ROOT}/docker-compose.yml" exec -T postgres pg_restore -U forum_admin -d "${target_db}" --no-owner --clean --if-exists < "${dump_file}" 2>/dev/null || \
       docker compose -f "${PROJECT_ROOT}/docker-compose.yml" exec -T postgres pg_restore -U forum_admin -d "${target_db}" --no-owner < "${dump_file}"; then
        echo "OK"
    else
        echo "FAILED (pg_restore error)"
        FAILED=1
    fi
done

echo "=========================================================="
if [ "${FAILED}" -eq 0 ]; then
    echo "SUCCESS: All 6 databases restored successfully."
    exit 0
else
    echo "ERROR: One or more database restores failed!" >&2
    exit 1
fi
