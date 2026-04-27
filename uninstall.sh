#!/usr/bin/env bash
# Pensieve MCP — uninstaller.
# Removes the MCP registration, LaunchAgent, and (optionally) the memos tool.
# Does NOT delete ~/.memos/ — your screenshots and DB are safe.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_DST="${HOME}/Library/LaunchAgents/com.user.pensieve.prune.plist"
RESUME_PLIST="${HOME}/Library/LaunchAgents/com.user.pensieve.resume.plist"
BACKUP_PLIST="${HOME}/Library/LaunchAgents/com.user.pensieve.backup.plist"
PMCP_ENV_DIR="${HOME}/.config/pensieve-mcp"

c_green() { printf "\033[32m%s\033[0m\n" "$*"; }
c_yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
step() { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }

step "1/6  Unregister MCP server from Claude Code"
claude mcp remove pensieve -s user 2>/dev/null && c_green "removed" || c_yellow "not registered (skipped)"

step "2/6  Unload and remove LaunchAgents (prune + resume + backup)"
for p in "${PLIST_DST}" "${RESUME_PLIST}" "${BACKUP_PLIST}"; do
  if [[ -f "${p}" ]]; then
    launchctl unload "${p}" 2>/dev/null || true
    rm -f "${p}"
    c_green "removed ${p}"
  fi
done

step "3/6  Stop memos services"
memos stop 2>/dev/null && c_green "stopped" || c_yellow "not running (skipped)"

step "4/6  Revert pensieve-mcp patches in memos site-packages"
if [[ -x "${REPO_DIR}/scripts/apply-patches.sh" ]]; then
  "${REPO_DIR}/scripts/apply-patches.sh" --revert || c_yellow "revert reported issues (ok if you'll uninstall memos next)"
else
  c_yellow "apply-patches.sh missing (skipped)"
fi

step "5/6  (Optional) uninstall the memos tool"
read -r -p "Run 'uv tool uninstall memos'? [y/N] " ans
if [[ "${ans:-}" =~ ^[Yy]$ ]]; then
  uv tool uninstall memos || true
  c_green "memos uninstalled"
else
  c_yellow "keeping memos tool installed"
fi

step "6/6  (Optional) remove pensieve-mcp credentials"
for env_file in "${PMCP_ENV_DIR}/auth.env" "${PMCP_ENV_DIR}/cos.env"; do
  if [[ -f "${env_file}" ]]; then
    read -r -p "Delete ${env_file}? [y/N] " ans
    if [[ "${ans:-}" =~ ^[Yy]$ ]]; then
      rm -f "${env_file}"
      c_green "removed ${env_file}"
    else
      c_yellow "kept ${env_file}"
    fi
  fi
done
# coscmd's own config (only relevant if cos.env was deleted)
if [[ ! -f "${PMCP_ENV_DIR}/cos.env" && -f "${HOME}/.cos.conf" ]]; then
  read -r -p "Delete ${HOME}/.cos.conf (coscmd config)? [y/N] " ans
  [[ "${ans:-}" =~ ^[Yy]$ ]] && rm -f "${HOME}/.cos.conf" && c_green "removed"
fi

cat <<EOF

Done. Your local data at ~/.memos/ is untouched.
Your COS bucket (if you used cloud archive) is untouched too — manage it from
the Tencent Cloud console.

To wipe local data:    rm -rf ~/.memos
EOF
