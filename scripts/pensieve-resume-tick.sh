#!/usr/bin/env bash
# Tick script for the pause-resume LaunchAgent.
# Fires every minute (and once at load). If ~/.memos/pause.state has a
# resume_at <= now, runs `memos start record` and uninstalls itself.
set -euo pipefail

STATE="${HOME}/.memos/pause.state"
PLIST="${HOME}/Library/LaunchAgents/com.user.pensieve.resume.plist"
MEMOS="${HOME}/.local/bin/memos"

[[ -f "${STATE}" ]] || { launchctl unload "${PLIST}" 2>/dev/null || true; rm -f "${PLIST}"; exit 0; }

action=$(/usr/bin/python3 - "${STATE}" <<'PY'
import json, sys
from datetime import datetime
try:
    s = json.load(open(sys.argv[1]))
except Exception:
    print("clear"); sys.exit(0)
ra = s.get("resume_at")
if not ra:
    print("wait"); sys.exit(0)  # indefinite pause
try:
    dt = datetime.fromisoformat(ra)
except Exception:
    print("clear"); sys.exit(0)
print("resume" if datetime.now().astimezone() >= dt.astimezone() else "wait")
PY
)

case "${action}" in
  resume)
    "${MEMOS}" start record >/dev/null 2>&1 || true
    rm -f "${STATE}"
    # Uninstall self so we don't keep ticking forever
    launchctl unload "${PLIST}" 2>/dev/null || true
    rm -f "${PLIST}"
    ;;
  clear)
    rm -f "${STATE}"
    launchctl unload "${PLIST}" 2>/dev/null || true
    rm -f "${PLIST}"
    ;;
  *) ;;  # wait
esac
