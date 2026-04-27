#!/usr/bin/env bash
# Delete screenshots older than RETAIN_DAYS, then sync DB.
# Pensieve has no built-in retention; this is our policy.
#
# Emergency mode: when ~/.memos exceeds EMERGENCY_GB, retention is
# temporarily reduced to EMERGENCY_RETAIN_DAYS so a runaway day can't
# fill the disk before the next 03:30 tick.
set -euo pipefail

RETAIN_DAYS="${RETAIN_DAYS:-90}"
EMERGENCY_GB="${EMERGENCY_GB:-80}"
EMERGENCY_RETAIN_DAYS="${EMERGENCY_RETAIN_DAYS:-30}"
MEMOS_DIR="${HOME}/.memos"
SHOT_DIR="${MEMOS_DIR}/screenshots"
LOG="${MEMOS_DIR}/prune.log"

# Rotate log at 10MB
[ -f "${LOG}" ] && [ "$(wc -c <"${LOG}" 2>/dev/null || echo 0)" -gt 10485760 ] && mv "${LOG}" "${LOG}.1"

ts() { date +"%Y-%m-%d %H:%M:%S"; }

{
  # Emergency watermark check: du -sm gives megabytes
  used_mb=$(du -sm "${MEMOS_DIR}" 2>/dev/null | awk '{print $1}')
  used_gb=$(( used_mb / 1024 ))
  effective_retain="${RETAIN_DAYS}"
  if [ "${used_gb}" -ge "${EMERGENCY_GB}" ]; then
    effective_retain="${EMERGENCY_RETAIN_DAYS}"
    echo "[$(ts)] EMERGENCY: ~/.memos at ${used_gb}GB (>= ${EMERGENCY_GB}GB), reducing retention to ${effective_retain}d"
  fi

  echo "[$(ts)] prune start (retain=${effective_retain}d, used=${used_gb}GB)"
  before=$(du -sh "${SHOT_DIR}" 2>/dev/null | awk '{print $1}')
  deleted=$(find "${SHOT_DIR}" -type f -mtime "+${effective_retain}" -print -delete 2>/dev/null | wc -l | tr -d ' ')
  find "${SHOT_DIR}" -type d -empty -not -path "${SHOT_DIR}" -delete 2>/dev/null || true
  after=$(du -sh "${SHOT_DIR}" 2>/dev/null | awk '{print $1}')
  echo "[$(ts)] deleted ${deleted} files; size ${before} -> ${after}"

  if [ "${deleted}" -gt 0 ]; then
    echo "[$(ts)] running memos scan to reconcile DB"
    "${HOME}/.local/bin/memos" scan >>"${LOG}" 2>&1 || echo "[$(ts)] scan failed (non-fatal)"
  fi
  echo "[$(ts)] prune done"
} >>"${LOG}" 2>&1
