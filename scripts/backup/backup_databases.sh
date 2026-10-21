#!/usr/bin/env bash
# =============================================================================
# Automated Backup Script for Forum Platform Logical PostgreSQL Databases
# Backs up forum_users, forum_content, forum_interactions,
# forum_moderation, forum_notifications, and forum_search.
#
# Generates timestamped, non-empty custom-format (.dump) archives.
# Does NOT hardcode or leak credentials.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

BACKUP_DIR="${1:-${PROJECT_ROOT}/backups}"
TIMESTAMP="$(date -u +"%Y%m%d_%H%M%SZ")"
TARGET_DIR="${BACKUP_DIR}/${TIMESTAMP}"

mkdir -p "${TARGET_DIR}"

DATABASES=(
    "forum_users"
    "forum_content"
    "forum_interactions"
    "forum_moderation"
    "forum_notifications"
    "forum_search"
)

echo "=========================================================="
echo "Starting PostgreSQL backup: ${TIMESTAMP}"
echo "Destination directory: ${TARGET_DIR}"
echo "=========================================================="

FAILED=0

for db in "${DATABASES[@]}"; do
    dump_file="${TARGET_DIR}/${db}.dump"
    echo -n "Backing up ${db} ... "
    
    # Run pg_dump within the running postgres container as forum_admin
    if docker compose -f "${PROJECT_ROOT}/docker-compose.yml" exec -T postgres pg_dump -U forum_admin -Fc -d "${db}" > "${dump_file}"; then
        size=$(stat -c%s "${dump_file}" 2>/dev/null || stat -f%z "${dump_file}" 2>/dev/null || echo 0)
        if [ "${size}" -gt 100 ]; then
            echo "OK (${size} bytes)"
        else
            echo "FAILED (artifact too small: ${size} bytes)"
            FAILED=1
        fi
    else
        echo "FAILED (pg_dump error)"
        FAILED=1
    fi
done

echo "=========================================================="
if [ "${FAILED}" -eq 0 ]; then
    echo "SUCCESS: All 6 databases backed up cleanly to ${TARGET_DIR}"
    # Write metadata manifest
    cat <<EOF > "${TARGET_DIR}/backup_manifest.json"
{
  "timestamp": "${TIMESTAMP}",
  "databases": $(printf '%s\n' "${DATABASES[@]}" | jq -R . | jq -s .),
  "backup_format": "pg_dump -Fc (custom)",
  "status": "completed"
}
EOF
    exit 0
else
    echo "ERROR: One or more database backups failed!" >&2
    exit 1
fi
