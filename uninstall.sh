#!/usr/bin/env bash
# Pensieve MCP — uninstaller.
# Removes the MCP registration, LaunchAgent, and (optionally) the memos tool.
# Does NOT delete ~/.memos/ — your screenshots and DB are safe.
set -euo pipefail

PLIST_DST="${HOME}/Library/LaunchAgents/com.user.pensieve.prune.plist"

c_green() { printf "\033[32m%s\033[0m\n" "$*"; }
c_yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
step() { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }

step "1/4  Unregister MCP server from Claude Code"
claude mcp remove pensieve -s user 2>/dev/null && c_green "removed" || c_yellow "not registered (skipped)"

step "2/4  Unload and remove LaunchAgent"
if [[ -f "${PLIST_DST}" ]]; then
  launchctl unload "${PLIST_DST}" 2>/dev/null || true
  rm -f "${PLIST_DST}"
  c_green "removed ${PLIST_DST}"
else
  c_yellow "no LaunchAgent found (skipped)"
fi

step "3/4  Stop memos services"
memos stop 2>/dev/null && c_green "stopped" || c_yellow "not running (skipped)"

step "4/4  (Optional) uninstall the memos tool"
read -r -p "Run 'uv tool uninstall memos'? [y/N] " ans
if [[ "${ans:-}" =~ ^[Yy]$ ]]; then
  uv tool uninstall memos || true
  c_green "memos uninstalled"
else
  c_yellow "keeping memos tool installed"
fi

cat <<EOF

Done. Your data at ~/.memos/ is untouched.
To also remove your screenshots and SQLite DB:
    rm -rf ~/.memos
EOF
