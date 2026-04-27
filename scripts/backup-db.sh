#!/usr/bin/env bash
# Weekly backup of the Pensieve SQLite DB + config to the COS archive bucket.
#
# Why: ~/.memos/screenshots/ either lives locally or in COS already (via
# archive-to-cos.sh). But ~/.memos/database.db (OCR text + embeddings + entity
# rows) and ~/.memos/config.yaml are 100% local. Lose them and your screen
# history is unsearchable even if the .webp files survive.
#
# What this does: tar.gz {database.db, config.yaml} to a temp file, upload to
# cos://<bucket>/_backup/<DEVICE>/pensieve-YYYY-MM-DD.tar.gz, keep last 4
# weeks (delete older). Skips silently if cos.env not configured.
set -euo pipefail

CRED_FILE="${CRED_FILE:-${HOME}/.config/pensieve-mcp/cos.env}"
LOG="${HOME}/.memos/backup.log"
COSCMD="${COSCMD:-${HOME}/.local/bin/coscmd}"
RETAIN_BACKUPS="${RETAIN_BACKUPS:-4}"

# Rotate log at 10MB
[ -f "${LOG}" ] && [ "$(wc -c <"${LOG}" 2>/dev/null || echo 0)" -gt 10485760 ] && mv "${LOG}" "${LOG}.1"

ts() { date +"%Y-%m-%d %H:%M:%S"; }

if [[ ! -r "${CRED_FILE}" ]]; then
  echo "[$(ts)] backup skipped: ${CRED_FILE} missing (cloud archive not configured)" >>"${LOG}"
  exit 0
fi
# shellcheck disable=SC1090
set -a; source "${CRED_FILE}"; set +a
DEVICE="${PENSIEVE_DEVICE:?PENSIEVE_DEVICE must be set in cos.env}"

DB="${HOME}/.memos/database.db"
CFG="${HOME}/.memos/config.yaml"

{
  echo "[$(ts)] backup start (device=${DEVICE}, bucket=${COS_BUCKET})"
  if [[ ! -f "${DB}" ]]; then
    echo "[$(ts)] SKIP: ${DB} missing"; exit 0
  fi

  stamp="$(date +%Y-%m-%d)"
  tmpdir="$(mktemp -d -t pensieve-backup)"
  trap "rm -rf '${tmpdir}'" EXIT
  staging="${tmpdir}/pensieve-${stamp}"
  mkdir -p "${staging}"

  # SQLite-safe copy: use the .backup pragma rather than cp (cp can race a write
  # transaction and produce a corrupt file). sqlite3 ships with macOS.
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "${DB}" ".backup '${staging}/database.db'" \
      || { echo "[$(ts)] sqlite3 .backup failed, falling back to cp"; cp "${DB}" "${staging}/database.db"; }
  else
    cp "${DB}" "${staging}/database.db"
  fi
  [[ -f "${CFG}" ]] && cp "${CFG}" "${staging}/config.yaml"

  archive="${tmpdir}/pensieve-${stamp}.tar.gz"
  ( cd "${tmpdir}" && tar czf "${archive}" "pensieve-${stamp}" )
  size=$(du -h "${archive}" | awk '{print $1}')
  cos_key="_backup/${DEVICE}/pensieve-${stamp}.tar.gz"

  if "${COSCMD}" upload "${archive}" "/${cos_key}" >/tmp/coscmd-backup.out 2>&1; then
    echo "[$(ts)] uploaded ${cos_key} (${size})"
  else
    echo "[$(ts)] UPLOAD-FAIL: ${cos_key}"; cat /tmp/coscmd-backup.out
    exit 1
  fi

  # Retention: keep newest RETAIN_BACKUPS, delete older.
  # `coscmd list` prints lines like "  pensieve-2026-04-20.tar.gz   1.2M".
  prefix="_backup/${DEVICE}/"
  keep_list="$("${COSCMD}" list "/${prefix}" 2>/dev/null \
    | awk '/pensieve-.*\.tar\.gz/ {print $1}' \
    | sort -r \
    | head -n "${RETAIN_BACKUPS}")"
  all_list="$("${COSCMD}" list "/${prefix}" 2>/dev/null \
    | awk '/pensieve-.*\.tar\.gz/ {print $1}')"
  for f in ${all_list}; do
    if ! grep -qx "${f}" <<<"${keep_list}"; then
      key="${prefix}${f}"
      if "${COSCMD}" delete -f "/${key}" >/dev/null 2>&1; then
        echo "[$(ts)] pruned old backup: ${key}"
      fi
    fi
  done

  echo "[$(ts)] backup done"
} >>"${LOG}" 2>&1
