#!/usr/bin/env bash
# Pensieve MCP — installer for Apple Silicon Macs.
# Idempotent: safe to re-run.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RETAIN_DAYS="${RETAIN_DAYS:-90}"
PLIST_LABEL="com.user.pensieve.prune"
PLIST_DST="${HOME}/Library/LaunchAgents/${PLIST_LABEL}.plist"

c_green() { printf "\033[32m%s\033[0m\n" "$*"; }
c_yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
c_red() { printf "\033[31m%s\033[0m\n" "$*" >&2; }
step() { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }

need() {
  command -v "$1" >/dev/null 2>&1 || { c_red "Missing: $1"; c_red "  Install hint: $2"; exit 1; }
}

# ── 1. preflight ──────────────────────────────────────────────────────────────
step "1/8  Preflight checks"
if [[ "$(uname -s)" != "Darwin" ]]; then
  c_red "This installer only supports macOS."; exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  c_yellow "Warning: not Apple Silicon (arm64). Proceeding anyway, but untested."
fi

need uv     "brew install uv    — https://docs.astral.sh/uv/"
need claude "https://docs.anthropic.com/claude-code  (install Claude Code CLI)"
command -v curl >/dev/null 2>&1 || { c_red "curl missing"; exit 1; }
c_green "ok"

# ── 2. install memos with transformers<5 pin (critical) ───────────────────────
step "2/8  Install Pensieve (memos) via uv, pinned to transformers<5"
# transformers 5.x removed transformers.onnx, which pensieve still imports.
uv tool install memos --with "transformers<5" --force
c_green "ok"

# ── 3. init memos ─────────────────────────────────────────────────────────────
step "3/8  Initialize memos config and database"
if [[ -f "${HOME}/.memos/config.yaml" ]]; then
  c_yellow "~/.memos/config.yaml already exists — skipping init"
else
  memos init
fi
c_green "ok"

# ── 4. screen recording permission ────────────────────────────────────────────
step "4/8  Screen recording permission"
cat <<EOF
macOS needs Screen Recording permission for the terminal running \`memos record\`.
Open System Settings → Privacy & Security → Screen Recording, and enable your terminal
(Terminal.app / iTerm / Warp / etc.).

Opening the settings pane for you…
EOF
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture" || true
read -r -p "Press Enter after granting permission (or Ctrl-C to abort)… " _
c_green "ok"

# ── 5. start memos ────────────────────────────────────────────────────────────
step "5/8  Start memos services (serve + record + watch)"
memos start || true
printf "Waiting for REST API"
for i in $(seq 1 15); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8839/api/health" || true)
  if [[ "$code" == "200" ]]; then printf " ok\n"; break; fi
  printf "."; sleep 2
done
if [[ "${code:-}" != "200" ]]; then
  c_red "memos REST API did not come up at http://localhost:8839. Check: memos ps"
  exit 1
fi
c_green "ok"

# ── 6. install LaunchAgent for 90-day retention ───────────────────────────────
step "6/8  Install LaunchAgent (daily 03:30 retention prune, ${RETAIN_DAYS}d)"
mkdir -p "${HOME}/Library/LaunchAgents"
sed \
  -e "s|__HOME__|${HOME}|g" \
  -e "s|__REPO__|${REPO_DIR}|g" \
  -e "s|__RETAIN_DAYS__|${RETAIN_DAYS}|g" \
  "${REPO_DIR}/templates/com.user.pensieve.prune.plist" > "${PLIST_DST}"

launchctl unload "${PLIST_DST}" 2>/dev/null || true
launchctl load "${PLIST_DST}"
c_green "ok  → ${PLIST_DST}"

# ── 7. register MCP server with Claude Code ───────────────────────────────────
step "7/8  Register MCP server with Claude Code (user scope)"
# Remove any prior registration so -- install is idempotent.
claude mcp remove pensieve -s user >/dev/null 2>&1 || true
claude mcp add pensieve -s user -- "${REPO_DIR}/scripts/pensieve-mcp.py"
c_green "ok"

# ── 8. done ───────────────────────────────────────────────────────────────────
step "8/8  All set 🎉"
cat <<EOF

Next steps:
  1. Use your Mac normally for a few minutes so screenshots accumulate.
  2. In a NEW Claude Code session (so the MCP tools load), ask:
        "最近 10 分钟我在干嘛？用 pensieve 工具查一下"
        "what did I work on in the last 10 minutes?"
  3. Verify:
        claude mcp list           # expect: pensieve ✓ Connected
        memos ps                  # serve + record + watch all Running
        launchctl list | grep pensieve

Data lives in:  ~/.memos/   (screenshots + SQLite)
Retention:      ${RETAIN_DAYS} days (change RETAIN_DAYS=... and rerun install.sh)
Uninstall:      ${REPO_DIR}/uninstall.sh
EOF
