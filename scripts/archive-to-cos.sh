#!/usr/bin/env bash
# Pensieve 90-day archive to Tencent COS.
# Replaces the old prune-screenshots.sh on this machine.
#
# Behavior:
#   1. find files older than RETAIN_DAYS in ~/.memos/screenshots/
#   2. upload each to cos://<bucket>/<DEVICE>/<rel-path>
#   3. verify presence in COS, then rm local file
#   4. DO NOT touch SQLite. DO NOT run `memos scan`.
#      → DB rows stay intact so MCP search keeps working on archived content.
#      → Web UI thumbnails for archived items will be broken (acceptable trade-off).
set -euo pipefail

CRED_FILE="${CRED_FILE:-${HOME}/.config/pensieve-mcp/cos.env}"
[[ -r "${CRED_FILE}" ]] || { echo "Missing ${CRED_FILE}" >&2; exit 1; }
# shellcheck disable=SC1090
set -a; source "${CRED_FILE}"; set +a

RETAIN_DAYS="${RETAIN_DAYS:-90}"
SHOT_DIR="${HOME}/.memos/screenshots"
LOG="${HOME}/.memos/archive.log"
DEVICE="${PENSIEVE_DEVICE:?PENSIEVE_DEVICE must be set in cos.env}"
COSCMD="${COSCMD:-${HOME}/.local/bin/coscmd}"

ts() { date +"%Y-%m-%d %H:%M:%S"; }

{
  echo "[$(ts)] archive start (retain=${RETAIN_DAYS}d, device=${DEVICE}, bucket=${COS_BUCKET})"
  before=$(du -sh "${SHOT_DIR}" 2>/dev/null | awk '{print $1}')

  uploaded=0; failed=0
  while IFS= read -r -d '' f; do
    rel="${f#${SHOT_DIR}/}"
    cos_key="${DEVICE}/${rel}"

    if "${COSCMD}" upload "${f}" "/${cos_key}" >/tmp/coscmd.out 2>&1; then
      # Verify HEAD (info) before deleting local
      if "${COSCMD}" info "/${cos_key}" >/dev/null 2>&1; then
        rm -f "${f}"
        uploaded=$((uploaded + 1))
      else
        echo "[$(ts)] VERIFY-FAIL: ${cos_key}"; cat /tmp/coscmd.out
        failed=$((failed + 1))
      fi
    else
      echo "[$(ts)] UPLOAD-FAIL: ${f}"; cat /tmp/coscmd.out
      failed=$((failed + 1))
    fi
  done < <(find "${SHOT_DIR}" -type f -name "*.webp" -mtime "+${RETAIN_DAYS}" -print0 2>/dev/null)

  # Clean empty date subdirs (but not SHOT_DIR itself)
  find "${SHOT_DIR}" -mindepth 1 -type d -empty -delete 2>/dev/null || true

  after=$(du -sh "${SHOT_DIR}" 2>/dev/null | awk '{print $1}')
  echo "[$(ts)] archive done: uploaded=${uploaded} failed=${failed} size ${before} -> ${after}"
  echo "[$(ts)] reminder: do NOT run 'memos scan' — would delete DB rows for archived items"
} >>"${LOG}" 2>&1
