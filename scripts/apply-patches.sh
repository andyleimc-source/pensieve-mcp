#!/usr/bin/env bash
# Apply pensieve-setup patches to the installed `memos` site-packages.
#
# Why: pensieve-mcp depends on three small modifications to upstream memos:
#   - server.py        Bearer-token auth middleware (only enforced if
#                      ~/.config/pensieve-mcp/auth.env exists)
#   - cmds/library.py  honor ~/.memos/power_mode.state to override
#                      battery throttling (used by set_power_mode tool)
#   - record.py        skip mirrored displays so screencapture doesn't
#                      ERROR every iteration on dual-monitor setups
#
# These get wiped by `uv tool install memos --force` / `uv tool upgrade memos`.
# Run this script after any such upgrade. Idempotent: safe to re-run.
#
# Usage:
#   ./scripts/apply-patches.sh           apply (skip if already applied)
#   ./scripts/apply-patches.sh --check   exit 0 if all applied, 1 otherwise
#   ./scripts/apply-patches.sh --revert  remove the patches
#
# Compatible with macOS-default bash 3.2.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATCHES_DIR="${REPO_DIR}/patches"

c_green()  { printf "\033[32m%s\033[0m\n" "$*"; }
c_yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
c_red()    { printf "\033[31m%s\033[0m\n" "$*" >&2; }

# Locate the memos site-packages (single python version expected under uv).
MEMOS_PKG="$(ls -d "${HOME}"/.local/share/uv/tools/memos/lib/python*/site-packages/memos 2>/dev/null | head -1 || true)"
if [[ -z "${MEMOS_PKG}" || ! -d "${MEMOS_PKG}" ]]; then
  c_red "Could not find memos site-packages under ~/.local/share/uv/tools/memos/lib/python*/site-packages/memos"
  c_red "Is memos installed? Try: uv tool install memos --with 'transformers<5'"
  exit 1
fi

# Parallel arrays (bash 3 has no assoc arrays):
#   PATCH_REL[i]    relative path inside memos/ package
#   PATCH_MARKER[i] string unique to the patched file (idempotency check)
#   PATCH_FILE[i]   filename under patches/
PATCH_REL=(   "server.py"               "cmds/library.py"   "record.py" )
PATCH_MARKER=("_pensieve_bearer_auth"   "_POWER_MODE_FILE"  "spdisplays_hardware_mirror" )
PATCH_FILE=(  "server.py.patch"         "library.py.patch"  "record.py.patch" )

is_applied() {
  grep -q "$2" "${MEMOS_PKG}/$1" 2>/dev/null
}

cmd_check() {
  local missing=0 i rel marker
  for i in "${!PATCH_REL[@]}"; do
    rel="${PATCH_REL[$i]}"; marker="${PATCH_MARKER[$i]}"
    if is_applied "${rel}" "${marker}"; then
      c_green "  ✓ ${rel}"
    else
      c_yellow "  ✗ ${rel}  (patch missing)"
      missing=$((missing + 1))
    fi
  done
  if (( missing == 0 )); then
    c_green "All patches applied."
    return 0
  else
    c_yellow "${missing} patch(es) missing — run ./scripts/apply-patches.sh"
    return 1
  fi
}

cmd_apply() {
  local applied=0 skipped=0 failed=0 i rel marker pfile
  for i in "${!PATCH_REL[@]}"; do
    rel="${PATCH_REL[$i]}"; marker="${PATCH_MARKER[$i]}"
    pfile="${PATCHES_DIR}/${PATCH_FILE[$i]}"
    if [[ ! -f "${pfile}" ]]; then
      c_red "  ! ${rel}: patch file missing: ${pfile}"
      failed=$((failed + 1)); continue
    fi
    if is_applied "${rel}" "${marker}"; then
      c_yellow "  = ${rel} already patched, skipping"
      skipped=$((skipped + 1)); continue
    fi
    # Patch headers use `memos/<rel>`; apply with -p0 from MEMOS_PKG's parent.
    if ( cd "$(dirname "${MEMOS_PKG}")" && patch --no-backup-if-mismatch -p0 -s < "${pfile}" ); then
      c_green "  + ${rel} patched"
      applied=$((applied + 1))
    else
      c_red "  ! ${rel}: patch failed (memos version drift?)"
      failed=$((failed + 1))
    fi
  done
  echo
  c_green "applied=${applied} skipped=${skipped} failed=${failed}"
  if (( failed > 0 )); then
    c_red "Some patches failed. memos may have changed upstream — open an issue:"
    c_red "  https://github.com/andyleimc-source/pensieve-mcp/issues"
    exit 2
  fi
  if (( applied > 0 )); then
    c_yellow "Restart memos for changes to take effect: memos stop && memos start"
  fi
}

cmd_revert() {
  local reverted=0 skipped=0 failed=0 i rel marker pfile
  for i in "${!PATCH_REL[@]}"; do
    rel="${PATCH_REL[$i]}"; marker="${PATCH_MARKER[$i]}"
    pfile="${PATCHES_DIR}/${PATCH_FILE[$i]}"
    if ! is_applied "${rel}" "${marker}"; then
      c_yellow "  = ${rel} not patched, skipping"
      skipped=$((skipped + 1)); continue
    fi
    if ( cd "$(dirname "${MEMOS_PKG}")" && patch --no-backup-if-mismatch -R -p0 -s < "${pfile}" ); then
      c_green "  − ${rel} reverted"
      reverted=$((reverted + 1))
    else
      c_red "  ! ${rel}: revert failed"
      failed=$((failed + 1))
    fi
  done
  echo
  c_green "reverted=${reverted} skipped=${skipped} failed=${failed}"
  (( failed == 0 )) || exit 2
}

case "${1:-apply}" in
  --check|check) cmd_check ;;
  --revert|revert) cmd_revert ;;
  apply|"") cmd_apply ;;
  *) c_red "Unknown arg: $1"; echo "Usage: $0 [apply|--check|--revert]"; exit 1 ;;
esac
