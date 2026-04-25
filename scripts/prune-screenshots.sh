#!/usr/bin/env bash
# Delete screenshots older than RETAIN_DAYS, then sync DB.
# Pensieve has no built-in retention; this is our policy.
set -euo pipefail

RETAIN_DAYS="${RETAIN_DAYS:-90}"
SHOT_DIR="${HOME}/.memos/screenshots"
LOG="${HOME}/.memos/prune.log"

ts() { date +"%Y-%m-%d %H:%M:%S"; }

{
  echo "[$(ts)] prune start (retain=${RETAIN_DAYS}d)"
  before=$(du -sh "${SHOT_DIR}" 2>/dev/null | awk '{print $1}')
  deleted=$(find "${SHOT_DIR}" -type f -mtime "+${RETAIN_DAYS}" -print -delete 2>/dev/null | wc -l | tr -d ' ')
  find "${SHOT_DIR}" -type d -empty -not -path "${SHOT_DIR}" -delete 2>/dev/null || true
  after=$(du -sh "${SHOT_DIR}" 2>/dev/null | awk '{print $1}')
  echo "[$(ts)] deleted ${deleted} files; size ${before} -> ${after}"

  if [ "${deleted}" -gt 0 ]; then
    echo "[$(ts)] running memos scan to reconcile DB"
    "${HOME}/.local/bin/memos" scan >>"${LOG}" 2>&1 || echo "[$(ts)] scan failed (non-fatal)"
  fi
  echo "[$(ts)] prune done"
} >>"${LOG}" 2>&1
