#!/usr/bin/env bash
# =============================================================================
# Automated Backup Script for Forum Platform Logical PostgreSQL Databases
# Backs up forum_users, forum_content, forum_interactions,
# forum_moderation, forum_notifications, and forum_search.
#
# Generates timestamped, non-empty custom-format (.dump) archives, a completed
# manifest, and SHA256SUMS covering exactly the six .dump artifacts.
#
# Artifacts are written to a .partial directory first. The completed directory
# is created only after every dump, archive listing, and checksum verification
# succeeds, so a partial run cannot be mistaken for a completed backup.
#
# Does NOT hardcode or leak credentials.
# =============================================================================
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

BACKUP_DIR="${1:-${PROJECT_ROOT}/backups}"
TIMESTAMP="$(date -u +"%Y%m%d_%H%M%SZ")"
TARGET_DIR="${BACKUP_DIR}/${TIMESTAMP}"
WORK_DIR="${TARGET_DIR}.partial"
CHECKSUM_FILE_NAME="SHA256SUMS"
MANIFEST_FILE_NAME="backup_manifest.json"

DATABASES=(
    "forum_users"
    "forum_content"
    "forum_interactions"
    "forum_moderation"
    "forum_notifications"
    "forum_search"
)

if [ -e "${TARGET_DIR}" ] || [ -e "${WORK_DIR}" ]; then
    echo "ERROR: Backup destination already exists: ${TARGET_DIR}" >&2
    exit 1
fi

mkdir -p "${WORK_DIR}"
# This marker remains if the run fails. Completed directories never contain it.
printf 'backup incomplete\n' > "${WORK_DIR}/INCOMPLETE"

echo "=========================================================="
echo "Starting PostgreSQL backup: ${TIMESTAMP}"
echo "Staging directory: ${WORK_DIR}"
echo "=========================================================="

FAILED=0

for db in "${DATABASES[@]}"; do
    dump_file="${WORK_DIR}/${db}.dump"
    tmp_file="${dump_file}.tmp"
    echo -n "Backing up ${db} ... "

    # Run pg_dump within the running postgres container as forum_admin.
    if docker compose -f "${PROJECT_ROOT}/docker-compose.yml" exec -T postgres pg_dump -U forum_admin -Fc -d "${db}" > "${tmp_file}"; then
        size=$(stat -c%s "${tmp_file}" 2>/dev/null || stat -f%z "${tmp_file}" 2>/dev/null || echo 0)
        if [ "${size}" -le 100 ]; then
            echo "FAILED (artifact too small: ${size} bytes)"
            FAILED=1
        elif ! docker compose -f "${PROJECT_ROOT}/docker-compose.yml" exec -T postgres pg_restore --list < "${tmp_file}" >/dev/null; then
            echo "FAILED (archive listing failed)"
            FAILED=1
        else
            mv "${tmp_file}" "${dump_file}"
            echo "OK (${size} bytes)"
        fi
    else
        echo "FAILED (pg_dump error)"
        FAILED=1
    fi
done

if [ "${FAILED}" -ne 0 ]; then
    echo "=========================================================="
    echo "ERROR: One or more database backups failed!" >&2
    echo "Incomplete artifacts remain at: ${WORK_DIR}" >&2
    echo "This directory is not a completed backup and must not be restored." >&2
    exit 1
fi

# SHA256SUMS covers exactly the six .dump files. The manifest and checksum file
# themselves are not included in the checksum list.
(
    cd "${WORK_DIR}"
    sha256sum "${DATABASES[@]/%/.dump}" > "${CHECKSUM_FILE_NAME}"
    sha256sum -c --strict "${CHECKSUM_FILE_NAME}" >/dev/null
)

# Write the completed manifest only after checksum verification succeeds.
cat <<EOF > "${WORK_DIR}/${MANIFEST_FILE_NAME}"
{
  "timestamp": "${TIMESTAMP}",
  "databases": $(printf '%s\n' "${DATABASES[@]}" | jq -R . | jq -s .),
  "backup_format": "pg_dump -Fc (custom)",
  "checksum_algorithm": "sha256",
  "checksum_file": "${CHECKSUM_FILE_NAME}",
  "checksum_scope": "six .dump files only",
  "status": "completed"
}
EOF

rm -f "${WORK_DIR}/INCOMPLETE"
mv "${WORK_DIR}" "${TARGET_DIR}"

echo "=========================================================="
echo "SUCCESS: All 6 databases backed up cleanly to ${TARGET_DIR}"
echo "Checksums: ${TARGET_DIR}/${CHECKSUM_FILE_NAME}"
echo "Manifest:  ${TARGET_DIR}/${MANIFEST_FILE_NAME}"
echo "Verify with: (cd \"${TARGET_DIR}\" && sha256sum -c --strict ${CHECKSUM_FILE_NAME})"
exit 0
