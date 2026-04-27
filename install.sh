#!/usr/bin/env bash
# Pensieve MCP — installer for Apple Silicon Macs.
# Idempotent: safe to re-run.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RETAIN_DAYS="${RETAIN_DAYS:-90}"
PLIST_LABEL="com.user.pensieve.prune"
PLIST_DST="${HOME}/Library/LaunchAgents/${PLIST_LABEL}.plist"
PMCP_ENV_DIR="${HOME}/.config/pensieve-mcp"
COS_ENV_FILE="${PMCP_ENV_DIR}/cos.env"
AUTH_ENV_FILE="${PMCP_ENV_DIR}/auth.env"

c_green()  { printf "\033[32m%s\033[0m\n" "$*"; }
c_yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
c_red()    { printf "\033[31m%s\033[0m\n" "$*" >&2; }
step()     { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }

need() {
  command -v "$1" >/dev/null 2>&1 || { c_red "Missing: $1"; c_red "  Install hint: $2"; exit 1; }
}

# ── 1. preflight ──────────────────────────────────────────────────────────────
step "1/12  Preflight checks"
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
step "2/12  Install Pensieve (memos) via uv, pinned to transformers<5"
# transformers 5.x removed transformers.onnx, which pensieve still imports.
uv tool install memos --with "transformers<5" --force
c_green "ok"

# ── 3. apply patches to memos site-packages ───────────────────────────────────
step "3/12  Apply pensieve-mcp patches (auth middleware, power mode, mirror filter)"
# These three patches live in patches/ and are wiped by `uv tool install
# memos --force`. apply-patches.sh is idempotent; it also detects when the
# upstream files have drifted.
"${REPO_DIR}/scripts/apply-patches.sh"
c_green "ok"

# ── 4. pre-download embedding model (with HF mirror fallback for China) ───────
step "4/12  Pre-download embedding model (jina-embeddings-v2-base-en, ~280MB)"
# Without this, `memos serve` hangs on first startup while sentence-transformers
# pulls the model from huggingface.co. From China the SSL handshake to HF often
# fails (UNEXPECTED_EOF_WHILE_READING) and serve sits there indefinitely with
# the API frozen — see docs/troubleshooting.md ("serve hangs on startup").
MEMOS_PY="${HOME}/.local/share/uv/tools/memos/bin/python"
HF_ENV_FILE="${HOME}/.memos/hf.env"
DOWNLOAD_PY='from huggingface_hub import snapshot_download
snapshot_download("arkohut/jina-embeddings-v2-base-en")
snapshot_download("jinaai/jina-bert-implementation", allow_patterns=["*.py","*.json"])
print("done")'

hf_endpoint=""
if curl -sfI --max-time 6 https://huggingface.co/api/models/arkohut/jina-embeddings-v2-base-en >/dev/null 2>&1; then
  c_green "huggingface.co reachable — using direct"
else
  c_yellow "huggingface.co unreachable — using https://hf-mirror.com (China-friendly mirror)"
  hf_endpoint="https://hf-mirror.com"
fi

if ! HF_ENDPOINT="${hf_endpoint}" "${MEMOS_PY}" -c "${DOWNLOAD_PY}"; then
  if [[ -z "${hf_endpoint}" ]]; then
    c_yellow "Direct download failed — retrying via https://hf-mirror.com"
    hf_endpoint="https://hf-mirror.com"
    HF_ENDPOINT="${hf_endpoint}" "${MEMOS_PY}" -c "${DOWNLOAD_PY}" \
      || { c_red "Embedding model download failed via mirror too. Check network."; exit 1; }
  else
    c_red "Embedding model download failed."; exit 1
  fi
fi

# Persist HF_ENDPOINT so future `memos start` (e.g. after model cache wipe or
# new model) doesn't re-hit the SSL hang. Sourced by `memos start` wrapper if
# present; harmless otherwise.
if [[ -n "${hf_endpoint}" ]]; then
  echo "export HF_ENDPOINT=${hf_endpoint}" > "${HF_ENV_FILE}"
  c_green "wrote ${HF_ENV_FILE}  (HF_ENDPOINT=${hf_endpoint})"
fi
c_green "ok"

# ── 4. init memos ─────────────────────────────────────────────────────────────
step "5/12  Initialize memos config and database"
if [[ -f "${HOME}/.memos/config.yaml" ]]; then
  c_yellow "~/.memos/config.yaml already exists — skipping init"
else
  memos init
fi
c_green "ok"

# ── 6. screen recording permission ────────────────────────────────────────────
step "6/12  Screen recording permission"
cat <<EOF
macOS needs Screen Recording permission for the terminal running \`memos record\`.
Open System Settings → Privacy & Security → Screen Recording, and enable your terminal
(Terminal.app / iTerm / Warp / etc.).

Opening the settings pane for you…
EOF
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture" || true
read -r -p "Press Enter after granting permission (or Ctrl-C to abort)… " _
c_green "ok"

# ── 7. (optional) API auth onboarding ─────────────────────────────────────────
step "7/12  Optional: API authentication (required if exposing :8839 to LAN/Tailnet)"
if [[ -f "${AUTH_ENV_FILE}" ]]; then
  c_green "found existing ${AUTH_ENV_FILE} — using it"
else
  cat <<EOF

Pensieve's REST API on :8839 is unauthenticated by default. Localhost-only is
fine. If you plan to:
  - expose it to a Tailnet (PENSIEVE_PEERS multi-device aggregation), or
  - bind 0.0.0.0 / let your LAN reach it
…you should enable Bearer-token auth. The patch in step 3 already wired the
middleware; it activates as soon as ${AUTH_ENV_FILE} exists.

Localhost (127.0.0.1) is always allowed regardless — Web UI keeps working.
EOF
  read -r -p "Generate a token and enable auth now? [y/N] " ans
  if [[ "${ans:-}" =~ ^[Yy]$ ]]; then
    mkdir -p "${PMCP_ENV_DIR}"; chmod 700 "${PMCP_ENV_DIR}"
    if command -v openssl >/dev/null 2>&1; then
      tok="$(openssl rand -hex 32)"
    else
      tok="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    fi
    printf "PENSIEVE_TOKEN=%s\n" "${tok}" > "${AUTH_ENV_FILE}"
    chmod 600 "${AUTH_ENV_FILE}"
    c_green "wrote ${AUTH_ENV_FILE}  (chmod 600)"
    echo
    c_yellow "Token (also stored in ${AUTH_ENV_FILE}):"
    printf "  \033[1m%s\033[0m\n" "${tok}"
    echo
    c_yellow "On any OTHER machine that uses PENSIEVE_PEERS to query this one,"
    c_yellow "drop the same token into its ~/.config/pensieve-mcp/auth.env."
  else
    c_yellow "skipped — :8839 will remain unauthenticated"
  fi
fi
c_green "ok"

# ── 8. start memos ────────────────────────────────────────────────────────────
step "8/12  Start memos services (serve + record + watch)"
# Source HF_ENDPOINT if installer set one — protects re-runs / future restarts
# in shells where the user hasn't exported it.
[[ -f "${HF_ENV_FILE}" ]] && source "${HF_ENV_FILE}"
memos start || true
printf "Waiting for REST API"
for _ in $(seq 1 15); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8839/api/health" || true)
  if [[ "$code" == "200" ]]; then printf " ok\n"; break; fi
  printf "."; sleep 2
done
if [[ "${code:-}" != "200" ]]; then
  c_red "memos REST API did not come up at http://localhost:8839. Check: memos ps"
  exit 1
fi
c_green "ok"

# ── 9. (optional) configure COS cloud archive ─────────────────────────────────
step "9/12  Optional: cloud archive of screenshots older than ${RETAIN_DAYS} days"
ARCHIVE_ENABLED="no"
if [[ -f "${COS_ENV_FILE}" ]]; then
  c_green "found existing ${COS_ENV_FILE} — using it"
  ARCHIVE_ENABLED="yes"
else
  cat <<EOF

Without cloud archive: screenshots older than ${RETAIN_DAYS} days are deleted locally.
With cloud archive:    they are uploaded to a Tencent Cloud COS bucket first,
                       and stay searchable through the MCP. ~¥1-3/month for 100GB.

Setup requires: a private COS bucket + a CAM sub-account with PutObject/GetObject/
DeleteObject/HeadObject/GetBucket scoped to that bucket only.
See: ${REPO_DIR}/config/cos.env.example  and  ${REPO_DIR}/docs/cos-archive.md
EOF
  read -r -p "Enable cloud archive now? [y/N] " ans
  if [[ "${ans:-}" =~ ^[Yy]$ ]]; then
    mkdir -p "${PMCP_ENV_DIR}"; chmod 700 "${PMCP_ENV_DIR}" 2>/dev/null || true
    read -r -p "  COS_REGION [ap-shanghai]: " region; region="${region:-ap-shanghai}"
    read -r -p "  COS_BUCKET (e.g. pensieve-archive-1234567890): " bucket
    read -r -p "  COS_APPID (the trailing number in the bucket name): " appid
    read -r -p "  PENSIEVE_DEVICE (unique per machine, e.g. personal-mac): " device
    read -r -p "  COS_SECRET_ID: " secret_id
    read -r -s -p "  COS_SECRET_KEY (input hidden): " secret_key; echo
    cat > "${COS_ENV_FILE}" <<ENV
COS_SECRET_ID=${secret_id}
COS_SECRET_KEY=${secret_key}
COS_REGION=${region}
COS_BUCKET=${bucket}
COS_APPID=${appid}
PENSIEVE_DEVICE=${device}
ENV
    chmod 600 "${COS_ENV_FILE}"
    c_green "wrote ${COS_ENV_FILE}  (chmod 600)"

    # install + configure coscmd
    uv tool install coscmd >/dev/null 2>&1 || uv tool install coscmd --force
    "${HOME}/.local/bin/coscmd" config -a "${secret_id}" -s "${secret_key}" -b "${bucket}" -r "${region}" >/dev/null
    chmod 600 "${HOME}/.cos.conf" 2>/dev/null || true

    # smoke test: head the bucket
    if "${HOME}/.local/bin/coscmd" list / >/dev/null 2>&1; then
      c_green "COS credentials verified (list bucket succeeded)"
      ARCHIVE_ENABLED="yes"
    else
      c_red "COS smoke test failed — check creds and bucket name. Aborting archive setup."
      rm -f "${COS_ENV_FILE}"
      ARCHIVE_ENABLED="no"
    fi
  else
    c_yellow "skipped — local prune only"
  fi
fi

# ── 10. install LaunchAgent ───────────────────────────────────────────────────
if [[ "${ARCHIVE_ENABLED}" == "yes" ]]; then
  CRON_SCRIPT="${REPO_DIR}/scripts/archive-to-cos.sh"
  step "10/12  LaunchAgent (daily 03:30, archive to COS, retain=${RETAIN_DAYS}d)"
else
  CRON_SCRIPT="${REPO_DIR}/scripts/prune-screenshots.sh"
  step "10/12  LaunchAgent (daily 03:30, local prune, retain=${RETAIN_DAYS}d)"
fi
mkdir -p "${HOME}/Library/LaunchAgents"
sed \
  -e "s|__HOME__|${HOME}|g" \
  -e "s|__REPO__|${REPO_DIR}|g" \
  -e "s|__RETAIN_DAYS__|${RETAIN_DAYS}|g" \
  -e "s|__CRON_SCRIPT__|${CRON_SCRIPT}|g" \
  "${REPO_DIR}/templates/com.user.pensieve.prune.plist" > "${PLIST_DST}"

launchctl unload "${PLIST_DST}" 2>/dev/null || true
launchctl load "${PLIST_DST}"
c_green "ok  → ${PLIST_DST}"

# ── 11. register MCP server with Claude Code ──────────────────────────────────
step "11/12  Register MCP server with Claude Code (user scope)"
claude mcp remove pensieve -s user >/dev/null 2>&1 || true
claude mcp add pensieve -s user -- "${REPO_DIR}/scripts/pensieve-mcp.py"
c_green "ok"

# ── 12. done ──────────────────────────────────────────────────────────────────
step "12/12  All set"
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
Retention:      ${RETAIN_DAYS} days  (override: RETAIN_DAYS=30 ./install.sh)
Cloud archive:  ${ARCHIVE_ENABLED}
Uninstall:      ${REPO_DIR}/uninstall.sh
EOF

if [[ "${ARCHIVE_ENABLED}" == "yes" ]]; then
  cat <<'EOF'

⚠️  Important: with cloud archive ON, do NOT run `memos scan`.
    It will delete DB rows for archived files, breaking MCP search on archived data.
EOF
fi
