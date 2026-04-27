#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mcp>=1.2.0",
#   "httpx>=0.27",
#   "psutil>=5.9",
# ]
# ///
"""Pensieve MCP server. Wraps the local memos REST API at :8839.

When COS archive credentials are present at ~/.config/pensieve-mcp/cos.env,
also exposes archive lookups for screenshots that have been pruned locally.

Multi-device: set PENSIEVE_PEERS=http://100.x.y.z:8839,... to aggregate
results from remote machines. Each hit is tagged with source=<hostname>.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from collections import Counter
from mcp.server.fastmcp import FastMCP

BASE = os.environ.get("PENSIEVE_BASE_URL", "http://localhost:8839")
LIBRARY_ID = int(os.environ.get("PENSIEVE_LIBRARY_ID", "1"))

# Comma-separated peer base URLs, e.g. "http://100.x.y.z:8839"
_raw_peers = os.environ.get("PENSIEVE_PEERS", "").strip()
PEERS: list[str] = [p.strip().rstrip("/") for p in _raw_peers.split(",") if p.strip()]
TIMEOUT = 30.0
SHOT_DIR = Path.home() / ".memos" / "screenshots"
COS_ENV_PATH = Path.home() / ".config" / "pensieve-mcp" / "cos.env"
AUTH_ENV_PATH = Path.home() / ".config" / "pensieve-mcp" / "auth.env"


def _load_api_token() -> str | None:
    """Read PENSIEVE_TOKEN from ~/.config/pensieve-mcp/auth.env, if present."""
    if not AUTH_ENV_PATH.is_file():
        return None
    try:
        for line in AUTH_ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith("PENSIEVE_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
                return tok or None
    except Exception:
        pass
    return None


_API_TOKEN = _load_api_token()
AUTH_HEADERS: dict[str, str] = (
    {"Authorization": f"Bearer {_API_TOKEN}"} if _API_TOKEN else {}
)
COSCMD = str(Path.home() / ".local" / "bin" / "coscmd")
MEMOS_BIN = str(Path.home() / ".local" / "bin" / "memos")
SCRIPTS_DIR = Path(__file__).resolve().parent
PAUSE_STATE = Path.home() / ".memos" / "pause.state"
RESUME_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.user.pensieve.resume.plist"
RESUME_TICK = SCRIPTS_DIR / "pensieve-resume-tick.sh"
POWER_MODE_FILE = Path.home() / ".memos" / "power_mode.state"
VALID_POWER_MODES = ("auto", "full_power", "forced_battery")

mcp = FastMCP("pensieve")


# ── COS helpers ───────────────────────────────────────────────────────────────


def _load_cos_env() -> dict[str, str] | None:
    """Parse cos.env if present; returns None if archive isn't configured."""
    if not COS_ENV_PATH.is_file():
        return None
    out: dict[str, str] = {}
    for line in COS_ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out if "COS_BUCKET" in out and "PENSIEVE_DEVICE" in out else None


COS = _load_cos_env()


def _cos_key_for(filepath: str) -> str | None:
    """Map a local screenshot filepath to its expected COS object key."""
    if not COS:
        return None
    try:
        rel = Path(filepath).resolve().relative_to(SHOT_DIR.resolve())
    except (ValueError, OSError):
        return None
    return f"{COS['PENSIEVE_DEVICE']}/{rel.as_posix()}"


def _archive_status(filepath: str | None) -> str:
    """One of: local | archived | unknown"""
    if not filepath:
        return "unknown"
    if Path(filepath).is_file():
        return "local"
    if COS and _cos_key_for(filepath):
        return "archived"
    return "unknown"


# ── time / size helpers ───────────────────────────────────────────────────────


def _today_midnight() -> datetime:
    return datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_time(s: str | int | None) -> int | None:
    """Parse ISO 8601 / unix int / a few relative shortcuts → unix ts.

    Returns None if s is None/empty. Raises ValueError on bad input.
    Shortcuts (case-insensitive): "today", "yesterday",
    "today morning|afternoon|evening", "yesterday morning|afternoon|evening",
    "this week", "last week", "last 24h", "last 7d", "last 30d", "now".
    For ranges, this returns the *start* of the named period; pair with
    _parse_range to get end semantics.
    """
    if s is None or s == "":
        return None
    if isinstance(s, int):
        return s
    s = str(s).strip()
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return int(s)
    try:
        return int(datetime.fromisoformat(s).astimezone().timestamp())
    except ValueError:
        pass
    raise ValueError(f"Unparseable time string: {s!r}")


# Period definitions: (start_offset_days, hour_start, hour_end_exclusive)
# Used by _parse_range for symbolic ranges.
_PERIODS = {
    "morning":   (0, 12),
    "afternoon": (12, 18),
    "evening":   (18, 24),
}


def _parse_range(start: str | int | None, end: str | int | None) -> tuple[int | None, int | None]:
    """Resolve (start, end) — supports symbolic ranges via the start arg alone.

    If `start` is a symbolic range ("today", "yesterday afternoon",
    "last 7d", etc.) and `end` is None, both ends are derived. Otherwise
    each is parsed independently via _parse_time.
    """
    if isinstance(start, str) and end is None:
        key = start.strip().lower()
        midnight = _today_midnight()
        now = datetime.now().astimezone()

        if key == "now":
            return int(now.timestamp()), int(now.timestamp())
        if key == "today":
            return int(midnight.timestamp()), int(now.timestamp())
        if key == "yesterday":
            y = midnight - timedelta(days=1)
            return int(y.timestamp()), int(midnight.timestamp())
        if key == "this week":
            wk_start = midnight - timedelta(days=midnight.weekday())
            return int(wk_start.timestamp()), int(now.timestamp())
        if key == "last week":
            wk_start = midnight - timedelta(days=midnight.weekday() + 7)
            wk_end = wk_start + timedelta(days=7)
            return int(wk_start.timestamp()), int(wk_end.timestamp())
        if key.startswith("last "):
            tail = key[5:].strip()
            if tail.endswith("h") and tail[:-1].isdigit():
                hours = int(tail[:-1])
                return int((now - timedelta(hours=hours)).timestamp()), int(now.timestamp())
            if tail.endswith("d") and tail[:-1].isdigit():
                days = int(tail[:-1])
                return int((now - timedelta(days=days)).timestamp()), int(now.timestamp())
        for prefix, base in (("today ", midnight), ("yesterday ", midnight - timedelta(days=1))):
            if key.startswith(prefix):
                period = key[len(prefix):].strip()
                if period in _PERIODS:
                    h0, h1 = _PERIODS[period]
                    return (
                        int((base + timedelta(hours=h0)).timestamp()),
                        int((base + timedelta(hours=h1)).timestamp()),
                    )
    return _parse_time(start), _parse_time(end)


def _maybe_warning(payload: dict[str, Any], threshold: int = 5000) -> dict[str, Any]:
    """Add `_warning` if the JSON payload exceeds `threshold` chars."""
    n = len(json.dumps(payload, ensure_ascii=False, default=str))
    if n > threshold:
        payload["_warning"] = (
            f"result is large (~{n // 1000}KB). For overviews use activity_summary; "
            "for narrower searches add a time range or smaller limit."
        )
    return payload


# ── peer helpers ──────────────────────────────────────────────────────────────


def _peer_hostname(base_url: str) -> str:
    """Extract a short label from a peer URL for tagging results."""
    host = base_url.split("//")[-1].split(":")[0]
    return host


def _peer_get(peer: str, params: dict[str, Any], retries: int = 2) -> dict | None:
    """GET /api/search on a peer with simple retry on 5xx (Tailscale relay flakes).

    `trust_env=False` skips httpx's auto-proxy detection — macOS system proxies
    (Clash/V2Ray) don't know how to route Tailscale CGNAT (100.64.0.0/10) and
    return 502.
    """
    for attempt in range(retries + 1):
        try:
            r = httpx.get(f"{peer}/api/search", params=params,
                          timeout=TIMEOUT, trust_env=False,
                          headers=AUTH_HEADERS)
            if r.status_code >= 500 and attempt < retries:
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt < retries:
                continue
            return None
    return None


def _peer_search(peer: str, params: dict[str, Any], include_ocr: bool) -> list[dict]:
    """Query one peer's /api/search; returns summarized hits tagged with source."""
    data = _peer_get(peer, params)
    if data is None:
        return []
    label = _peer_hostname(peer)
    hits = []
    for h in data.get("hits", []):
        doc = h.get("document", {})
        summary = _summarize_hit(doc, include_ocr=include_ocr)
        summary["source"] = label
        hits.append(summary)
    return hits


def _peer_scan(peer: str, s_ts: int, e_ts: int) -> list[dict]:
    """Scan all entities from a peer in a time range (mirrors _scan_entities)."""
    docs: list[dict] = []
    seen: set[int] = set()
    stack: list[tuple[int, int]] = [(s_ts, e_ts)]
    label = _peer_hostname(peer)
    for _ in range(2000):
        if not stack:
            break
        a, b = stack.pop()
        if b <= a:
            continue
        data = _peer_get(peer, {"q": "", "limit": _SCAN_PAGE_LIMIT, "library_ids": 1,
                                 "start": a, "end": b})
        if data is None:
            continue
        hits = data.get("hits", []) or []
        if len(hits) >= _SCAN_PAGE_LIMIT and (b - a) > 1:
            mid = (a + b) // 2
            if mid > a and mid < b:
                stack.append((a, mid))
                stack.append((mid + 1, b))
                continue
        for h in hits:
            doc = h.get("document") or {}
            did = doc.get("id")
            if did is None or did in seen:
                continue
            seen.add(did)
            doc["_source"] = label
            docs.append(doc)
    return docs


# ── tools ─────────────────────────────────────────────────────────────────────


def _extract_ocr_text(metadata_entries: list[dict]) -> str:
    for m in metadata_entries or []:
        if m.get("key") == "ocr_result":
            boxes = m.get("value") or []
            if isinstance(boxes, list):
                return " ".join(
                    b.get("rec_txt", "")
                    for b in boxes
                    if isinstance(b, dict) and b.get("rec_txt")
                ).strip()
    return ""


def _meta_get(metadata_entries: list[dict], key: str) -> str:
    for m in metadata_entries or []:
        if m.get("key") == key:
            v = m.get("value")
            return "" if v in (None, "None") else str(v)
    return ""


def _summarize_hit(doc: dict, include_ocr: bool = True) -> dict[str, Any]:
    meta = doc.get("metadata_entries") or []
    fp = doc.get("filepath")
    out = {
        "id": doc.get("id"),
        "filepath": fp,
        "archive_status": _archive_status(fp),
        "created_at": doc.get("file_created_at"),
        "app": _meta_get(meta, "active_app"),
        "window": _meta_get(meta, "active_window"),
        "screen": _meta_get(meta, "screen_name"),
        "url": _meta_get(meta, "url"),
    }
    if include_ocr:
        ocr = _extract_ocr_text(meta)
        if len(ocr) > 800:
            ocr = ocr[:800] + "…"
        out["ocr_text"] = ocr
    return out


@mcp.tool()
def search_screenshots(
    query: str,
    limit: int = 10,
    app: str | None = None,
    start: str | int | None = None,
    end: str | int | None = None,
    include_ocr: bool | None = None,
) -> dict[str, Any]:
    """Search the user's screen history by semantic + keyword query.

    Prefer this over raw REST whenever filtering by time. For "what did I do
    on X" overview questions use activity_summary instead — it returns a
    compact aggregate, not raw hits.

    Args:
        query: Semantic + keyword query. Pass "" to list newest entities in
               the time range without scoring.
        limit: Max results (default 10, max 200).
        app: Optional app filter (e.g. "iTerm2", "Chrome", "微信").
        start: Time-range start. Accepts ISO 8601 ("2026-04-25",
               "2026-04-25T13:00"), a unix-ts int, or a symbolic range when
               `end` is omitted: "today", "yesterday",
               "today afternoon" / "yesterday morning|afternoon|evening",
               "this week", "last week", "last 24h", "last 7d", "last 30d".
        end: Time-range end (ISO 8601 / unix int). Ignored when `start` is a
             symbolic range.
        include_ocr: If False, omit ocr_text from each hit (saves bandwidth).
                     Defaults to True for limit ≤ 20, False otherwise.

    Hits include `archive_status`: "local" (image on disk),
    "archived" (image in COS — call download_archived to fetch),
    or "unknown".
    """
    limit = max(1, min(int(limit), 200))
    if include_ocr is None:
        include_ocr = limit <= 20

    s_ts, e_ts = _parse_range(start, end)
    params: dict[str, Any] = {"q": query, "limit": limit, "library_ids": LIBRARY_ID}
    if app:
        params["app_names"] = app
    if s_ts is not None:
        params["start"] = s_ts
    if e_ts is not None:
        params["end"] = e_ts
    r = httpx.get(f"{BASE}/api/search", params=params, timeout=TIMEOUT,
                  headers=AUTH_HEADERS)
    r.raise_for_status()
    data = r.json()
    local_hits = [
        {**_summarize_hit(h.get("document", {}), include_ocr=include_ocr), "source": "local"}
        for h in data.get("hits", [])
    ]

    peer_hits: list[dict] = []
    if PEERS:
        with ThreadPoolExecutor(max_workers=len(PEERS)) as ex:
            futures = {ex.submit(_peer_search, p, params, include_ocr): p for p in PEERS}
            for fut in as_completed(futures):
                peer_hits.extend(fut.result())

    all_hits = local_hits + peer_hits
    out: dict[str, Any] = {
        "found": data.get("found", 0) + len(peer_hits),
        "returned": len(all_hits),
        "include_ocr": include_ocr,
        "sources": ["local"] + [_peer_hostname(p) for p in PEERS],
        "hits": all_hits,
    }
    if s_ts is not None or e_ts is not None:
        out["time_range"] = {"start": s_ts, "end": e_ts}
    return _maybe_warning(out)


@mcp.tool()
def get_screenshot(entity_id: int, max_chars: int = 2000) -> dict[str, Any]:
    """Fetch full details for one screenshot by id.

    Args:
        entity_id: Pensieve entity id.
        max_chars: Truncate `ocr_text_full` to this many characters
                   (default 2000). Pass 0 for no truncation. When
                   truncated, `ocr_text_truncated` is set to True and
                   `ocr_text_full_chars` holds the original length.

    If the image has been archived to COS (local file gone),
    `archive_status` will be "archived" and `cos_key` indicates where
    it lives.
    """
    r = httpx.get(f"{BASE}/api/entities/{int(entity_id)}", timeout=TIMEOUT,
                  headers=AUTH_HEADERS)
    r.raise_for_status()
    doc = r.json()
    meta = doc.get("metadata_entries") or []
    fp = doc.get("filepath")
    status = _archive_status(fp)
    full_ocr = _extract_ocr_text(meta)
    truncated = False
    full_len = len(full_ocr)
    if max_chars and full_len > max_chars:
        full_ocr = full_ocr[:max_chars] + "…"
        truncated = True
    out: dict[str, Any] = {
        "id": doc.get("id"),
        "filepath": fp,
        "archive_status": status,
        "created_at": doc.get("file_created_at"),
        "app": _meta_get(meta, "active_app"),
        "window": _meta_get(meta, "active_window"),
        "screen": _meta_get(meta, "screen_name"),
        "url": _meta_get(meta, "url"),
        "tags": doc.get("tags") or [],
        "ocr_text_full": full_ocr,
        "ocr_text_truncated": truncated,
        "ocr_text_full_chars": full_len,
    }
    if status == "archived":
        out["cos_bucket"] = COS["COS_BUCKET"] if COS else None
        out["cos_key"] = _cos_key_for(fp)
        out["hint"] = "Call download_archived(entity_id) to retrieve the image."
    return out


@mcp.tool()
def download_archived(entity_id: int) -> dict[str, Any]:
    """Download an archived screenshot from COS to a local temp file.

    Use this only when get_screenshot says archive_status='archived'.
    Returns the local temp path; the caller can read/render the file.
    """
    if not COS:
        return {"error": "COS archive not configured (no ~/.config/pensieve-mcp/cos.env)"}
    r = httpx.get(f"{BASE}/api/entities/{int(entity_id)}", timeout=TIMEOUT,
                  headers=AUTH_HEADERS)
    r.raise_for_status()
    fp = r.json().get("filepath")
    key = _cos_key_for(fp)
    if not key:
        return {"error": "Could not derive COS key from filepath", "filepath": fp}
    if Path(fp).is_file():
        return {"local_path": fp, "note": "image is still local; no download needed"}

    suffix = Path(fp).suffix or ".webp"
    tmp = tempfile.NamedTemporaryFile(prefix="pensieve-archived-", suffix=suffix, delete=False)
    tmp.close()
    cmd = [COSCMD, "download", "-f", f"/{key}", tmp.name]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        return {"error": "coscmd download failed", "stderr": proc.stderr.strip(), "key": key}
    return {
        "local_path": tmp.name,
        "cos_key": key,
        "size_bytes": Path(tmp.name).stat().st_size,
    }


# ── activity summary ──────────────────────────────────────────────────────────


_SCAN_PAGE_LIMIT = 200  # /api/search caps limit at 200, no offset.


def _scan_entities(s_ts: int, e_ts: int) -> list[dict]:
    """Return all entities in [s_ts, e_ts]. Subdivides time when a window
    saturates the 200-row API cap. Returns raw `document` dicts.
    """
    docs: list[dict] = []
    seen: set[int] = set()
    stack: list[tuple[int, int]] = [(s_ts, e_ts)]
    # Bound iterations defensively.
    for _ in range(2000):
        if not stack:
            break
        a, b = stack.pop()
        if b <= a:
            continue
        r = httpx.get(
            f"{BASE}/api/search",
            params={"q": "", "limit": _SCAN_PAGE_LIMIT, "library_ids": LIBRARY_ID,
                    "start": a, "end": b},
            timeout=TIMEOUT,
            headers=AUTH_HEADERS,
        )
        r.raise_for_status()
        hits = r.json().get("hits", []) or []
        if len(hits) >= _SCAN_PAGE_LIMIT and (b - a) > 1:
            mid = (a + b) // 2
            # Avoid an infinite split at degenerate ranges.
            if mid > a and mid < b:
                stack.append((a, mid))
                stack.append((mid + 1, b))
                continue
        for h in hits:
            doc = h.get("document") or {}
            did = doc.get("id")
            if did is None or did in seen:
                continue
            seen.add(did)
            docs.append(doc)
    return docs


@mcp.tool()
def activity_summary(
    start: str | int | None = "today",
    end: str | int | None = None,
    group_by: str = "app",
    top_n: int = 10,
) -> dict[str, Any]:
    """Compact aggregate of screen activity over a time range.

    Use this — not search_screenshots — for "what was I doing on X" style
    questions. Returns counts only; no OCR text, no per-shot detail.

    Args:
        start: Range start. Same syntax as search_screenshots.start; defaults
               to "today". Symbolic ranges ("today afternoon", "yesterday",
               "last 7d") fill in the end automatically.
        end: Range end. Ignored if `start` is symbolic.
        group_by: "app" | "window" | "hour" | "all"
                  - "app":    top apps by screenshot count
                  - "window": top window titles
                  - "hour":   per-hour bucket with top app
                  - "all":    apps + windows + hourly
        top_n: How many top apps/windows to return (default 10, max 50).
    """
    top_n = max(1, min(int(top_n), 50))
    if group_by not in ("app", "window", "hour", "all"):
        return {"error": f"invalid group_by: {group_by!r}",
                "valid": ["app", "window", "hour", "all"]}

    s_ts, e_ts = _parse_range(start, end)
    if s_ts is None or e_ts is None:
        return {"error": "could not resolve time range", "start": start, "end": end}
    if e_ts <= s_ts:
        return {"error": "end must be after start", "start": s_ts, "end": e_ts}

    docs = _scan_entities(s_ts, e_ts)
    for doc in docs:
        doc.setdefault("_source", "local")

    if PEERS:
        with ThreadPoolExecutor(max_workers=len(PEERS)) as ex:
            futures = {ex.submit(_peer_scan, p, s_ts, e_ts): p for p in PEERS}
            for fut in as_completed(futures):
                docs.extend(fut.result())

    total = len(docs)

    app_counts: Counter[str] = Counter()
    window_counts: Counter[str] = Counter()
    hourly: dict[str, Counter[str]] = {}

    for doc in docs:
        meta = doc.get("metadata_entries") or []
        app = _meta_get(meta, "active_app") or "(unknown)"
        win = _meta_get(meta, "active_window") or "(unknown)"
        app_counts[app] += 1
        window_counts[win] += 1
        ts = doc.get("file_created_at")
        if ts:
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone()
                bucket = dt.strftime("%Y-%m-%dT%H:00")
                hourly.setdefault(bucket, Counter())[app] += 1
            except ValueError:
                pass

    out: dict[str, Any] = {
        "total_shots": total,
        "time_range": {
            "start": s_ts,
            "end": e_ts,
            "start_iso": datetime.fromtimestamp(s_ts).astimezone().isoformat(timespec="seconds"),
            "end_iso": datetime.fromtimestamp(e_ts).astimezone().isoformat(timespec="seconds"),
        },
        "group_by": group_by,
    }
    if group_by in ("app", "all"):
        out["top_apps"] = [{"app": a, "count": c}
                           for a, c in app_counts.most_common(top_n)]
    if group_by in ("window", "all"):
        out["top_windows"] = [{"window": w, "count": c}
                              for w, c in window_counts.most_common(top_n)]
    if group_by in ("hour", "all"):
        out["hourly"] = [
            {
                "hour": h,
                "count": sum(c.values()),
                "top_app": c.most_common(1)[0][0] if c else None,
            }
            for h, c in sorted(hourly.items())
        ]
    return out


# ── recording pause / resume ──────────────────────────────────────────────────


def _memos_ps_record_running() -> bool:
    try:
        out = subprocess.run([MEMOS_BIN, "ps"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return False
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0] == "record":
            return len(parts) > 1 and parts[1].lower() == "running"
    return False


def _install_resume_agent() -> None:
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.user.pensieve.resume</string>
    <key>ProgramArguments</key>
    <array><string>{RESUME_TICK}</string></array>
    <key>StartInterval</key><integer>60</integer>
    <key>RunAtLoad</key><true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key><string>{Path.home()}</string>
        <key>PATH</key><string>{Path.home()}/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>StandardOutPath</key><string>{Path.home()}/.memos/resume.stdout.log</string>
    <key>StandardErrorPath</key><string>{Path.home()}/.memos/resume.stderr.log</string>
</dict>
</plist>
"""
    RESUME_PLIST.parent.mkdir(parents=True, exist_ok=True)
    RESUME_PLIST.write_text(plist)
    subprocess.run(["launchctl", "unload", str(RESUME_PLIST)], capture_output=True)
    subprocess.run(["launchctl", "load", str(RESUME_PLIST)], capture_output=True)


def _uninstall_resume_agent() -> None:
    if RESUME_PLIST.exists():
        subprocess.run(["launchctl", "unload", str(RESUME_PLIST)], capture_output=True)
        RESUME_PLIST.unlink(missing_ok=True)


def _read_pause_state() -> dict[str, Any] | None:
    if not PAUSE_STATE.is_file():
        return None
    try:
        return json.loads(PAUSE_STATE.read_text())
    except Exception:
        return None


@mcp.tool()
def pause_recording(
    duration_seconds: int | None = None,
    resume_at: str | None = None,
) -> dict[str, Any]:
    """Pause screen recording. OCR/search/Web UI stay running.

    Args:
        duration_seconds: Pause for this many seconds, then auto-resume.
        resume_at: Auto-resume at this absolute time (ISO 8601, e.g.
                   "2026-04-25T18:00:00" or "2026-04-25T18:00:00+08:00").
        Pass neither for an indefinite pause (manual resume_recording).
        Pass at most one of the two.

    A LaunchAgent ticks every minute and runs `memos start record` once
    the resume time is reached. Survives sleep and reboot.
    """
    if duration_seconds is not None and resume_at is not None:
        return {"error": "Pass at most one of duration_seconds or resume_at."}

    now = datetime.now().astimezone()
    resume_dt: datetime | None = None
    if duration_seconds is not None:
        if duration_seconds <= 0:
            return {"error": "duration_seconds must be positive."}
        resume_dt = now + timedelta(seconds=int(duration_seconds))
    elif resume_at is not None:
        try:
            resume_dt = datetime.fromisoformat(resume_at)
        except ValueError:
            return {"error": f"Could not parse resume_at: {resume_at!r}. Use ISO 8601."}
        if resume_dt.tzinfo is None:
            resume_dt = resume_dt.astimezone()
        if resume_dt <= now:
            return {"error": "resume_at must be in the future."}

    proc = subprocess.run(
        [MEMOS_BIN, "stop", "record"], capture_output=True, text=True, timeout=15
    )
    state = {
        "paused_at": now.isoformat(timespec="seconds"),
        "resume_at": resume_dt.isoformat(timespec="seconds") if resume_dt else None,
        "stop_output": (proc.stdout + proc.stderr).strip(),
    }
    PAUSE_STATE.parent.mkdir(parents=True, exist_ok=True)
    PAUSE_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

    if resume_dt:
        _install_resume_agent()
        state["auto_resume"] = "scheduled"
    else:
        _uninstall_resume_agent()
        state["auto_resume"] = "indefinite (call resume_recording to restart)"
    return state


@mcp.tool()
def resume_recording() -> dict[str, Any]:
    """Resume screen recording immediately. Cancels any scheduled auto-resume."""
    _uninstall_resume_agent()
    PAUSE_STATE.unlink(missing_ok=True)
    proc = subprocess.run(
        [MEMOS_BIN, "start", "record"], capture_output=True, text=True, timeout=15
    )
    return {
        "ok": proc.returncode == 0,
        "output": (proc.stdout + proc.stderr).strip(),
        "record_running": _memos_ps_record_running(),
    }


@mcp.tool()
def recording_status() -> dict[str, Any]:
    """Report whether record is running, and any active pause/resume schedule."""
    state = _read_pause_state()
    running = _memos_ps_record_running()
    out: dict[str, Any] = {
        "record_running": running,
        "paused": state is not None,
    }
    if state:
        out["paused_at"] = state.get("paused_at")
        out["resume_at"] = state.get("resume_at") or "indefinite"
        if state.get("resume_at"):
            try:
                resume_dt = datetime.fromisoformat(state["resume_at"])
                remaining = resume_dt - datetime.now().astimezone()
                out["seconds_until_resume"] = max(0, int(remaining.total_seconds()))
            except Exception:
                pass
    return out


@mcp.tool()
def health() -> dict[str, Any]:
    """Check that the Pensieve REST API is up; report archive availability too."""
    r = httpx.get(f"{BASE}/api/health", timeout=5.0, headers=AUTH_HEADERS)
    return {
        "status_code": r.status_code,
        "body": r.json(),
        "archive_configured": COS is not None,
        "archive_device": COS.get("PENSIEVE_DEVICE") if COS else None,
        "archive_bucket": COS.get("COS_BUCKET") if COS else None,
    }


# ── power mode (battery throttle override) ───────────────────────────────────


def _read_power_mode() -> str:
    """Return the current override mode: auto / full_power / forced_battery."""
    try:
        if POWER_MODE_FILE.is_file():
            mode = POWER_MODE_FILE.read_text().strip()
            if mode in VALID_POWER_MODES:
                return mode
    except Exception:
        pass
    return "auto"


def _battery_snapshot() -> dict[str, Any]:
    """psutil battery info, normalized."""
    try:
        import psutil
        b = psutil.sensors_battery()
    except Exception:
        return {"available": False}
    if b is None:
        return {"available": False}
    secs = b.secsleft
    return {
        "available": True,
        "battery_percent": round(b.percent, 1),
        "power_plugged": bool(b.power_plugged),
        "secs_left": None if secs in (-1, -2) else int(secs),
        "actual_on_battery": not b.power_plugged,
    }


@mcp.tool()
def set_power_mode(mode: str) -> dict[str, Any]:
    """Override Pensieve's battery-throttle behavior.

    Pensieve normally slows OCR/embedding ingestion when on battery (doubles
    processing interval, blocks idle catch-up, stops background scans). This
    tool lets you override that.

    Args:
        mode: One of:
          - "auto"           — default; honor real battery state via psutil
          - "full_power"     — pretend always plugged in; full-speed ingest
                               regardless of battery (drains battery faster)
          - "forced_battery" — pretend always on battery; max throttle (mostly
                               for debugging the throttle logic)

    Takes effect within ~60s (watch loop's battery-cache TTL). Survives memos
    restart (state lives in ~/.memos/power_mode.state).
    """
    if mode not in VALID_POWER_MODES:
        return {"error": "invalid mode", "given": mode, "valid": list(VALID_POWER_MODES)}
    try:
        if mode == "auto":
            POWER_MODE_FILE.unlink(missing_ok=True)
        else:
            POWER_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
            POWER_MODE_FILE.write_text(mode)
    except Exception as e:
        return {"error": f"failed to update power mode file: {e}"}

    snap = _battery_snapshot()
    return {
        "mode": mode,
        "state_file": str(POWER_MODE_FILE),
        "effective_within_seconds": 60,
        "actual_battery": snap,
        "note": (
            "watch loop refreshes battery state every 60s; new mode takes effect "
            "within that window without needing memos restart."
        ),
    }


@mcp.tool()
def get_power_mode() -> dict[str, Any]:
    """Report the current power-mode override + actual battery state.

    Use this to diagnose whether Pensieve is throttling ingest. If
    `throttling_active=true` and `mode=auto`, you're on battery and watch
    is doubling its processing interval — set mode=full_power to override.
    """
    mode = _read_power_mode()
    snap = _battery_snapshot()

    actual = snap.get("actual_on_battery") if snap.get("available") else False
    if mode == "full_power":
        effective = False
    elif mode == "forced_battery":
        effective = True
    else:
        effective = bool(actual)

    return {
        "mode": mode,
        "state_file_exists": POWER_MODE_FILE.is_file(),
        "actual_on_battery": actual if snap.get("available") else None,
        "effective_on_battery": effective,
        "throttling_active": effective,
        "battery": snap,
    }


@mcp.tool()
def toggle_full_power() -> dict[str, Any]:
    """Convenience two-state toggle: full_power ↔ auto.

    If currently in full_power → switch to auto.
    Otherwise (auto or forced_battery) → switch to full_power.
    Returns the same shape as set_power_mode.
    """
    new_mode = "auto" if _read_power_mode() == "full_power" else "full_power"
    return set_power_mode(new_mode)


if __name__ == "__main__":
    mcp.run()
