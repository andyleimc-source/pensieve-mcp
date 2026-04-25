#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mcp>=1.2.0",
#   "httpx>=0.27",
# ]
# ///
"""Pensieve MCP server. Wraps the local memos REST API at :8839.

When COS archive credentials are present at ~/.config/pensieve-mcp/cos.env,
also exposes archive lookups for screenshots that have been pruned locally.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

BASE = os.environ.get("PENSIEVE_BASE_URL", "http://localhost:8839")
LIBRARY_ID = int(os.environ.get("PENSIEVE_LIBRARY_ID", "1"))
TIMEOUT = 30.0
SHOT_DIR = Path.home() / ".memos" / "screenshots"
COS_ENV_PATH = Path.home() / ".config" / "pensieve-mcp" / "cos.env"
COSCMD = str(Path.home() / ".local" / "bin" / "coscmd")
MEMOS_BIN = str(Path.home() / ".local" / "bin" / "memos")
SCRIPTS_DIR = Path(__file__).resolve().parent
PAUSE_STATE = Path.home() / ".memos" / "pause.state"
RESUME_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.user.pensieve.resume.plist"
RESUME_TICK = SCRIPTS_DIR / "pensieve-resume-tick.sh"

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


def _summarize_hit(doc: dict) -> dict[str, Any]:
    meta = doc.get("metadata_entries") or []
    ocr = _extract_ocr_text(meta)
    if len(ocr) > 800:
        ocr = ocr[:800] + "…"
    fp = doc.get("filepath")
    return {
        "id": doc.get("id"),
        "filepath": fp,
        "archive_status": _archive_status(fp),
        "created_at": doc.get("file_created_at"),
        "app": _meta_get(meta, "active_app"),
        "window": _meta_get(meta, "active_window"),
        "screen": _meta_get(meta, "screen_name"),
        "url": _meta_get(meta, "url"),
        "ocr_text": ocr,
    }


@mcp.tool()
def search_screenshots(
    query: str,
    limit: int = 10,
    app: str | None = None,
) -> dict[str, Any]:
    """Search the user's screen history by semantic + keyword query.

    Args:
        query: What to search for (e.g. "supabase billing", "上周的账单讨论").
        limit: Max results to return (default 10, max 50).
        app: Optional app name filter (e.g. "iTerm2", "微信", "Chrome").

    Hits include `archive_status`: "local" (image on disk),
    "archived" (image in COS — call download_archived to fetch),
    or "unknown".
    """
    limit = max(1, min(int(limit), 50))
    params = {"q": query, "limit": limit, "library_ids": LIBRARY_ID}
    if app:
        params["app_names"] = app
    r = httpx.get(f"{BASE}/api/search", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return {
        "found": data.get("found", 0),
        "returned": len(data.get("hits", [])),
        "hits": [_summarize_hit(h.get("document", {})) for h in data.get("hits", [])],
    }


@mcp.tool()
def get_screenshot(entity_id: int) -> dict[str, Any]:
    """Fetch full details for one screenshot by id (including full OCR text).

    If the image has been archived to COS (local file gone), `archive_status`
    will be "archived" and `cos_key` indicates where it lives.
    """
    r = httpx.get(f"{BASE}/api/entities/{int(entity_id)}", timeout=TIMEOUT)
    r.raise_for_status()
    doc = r.json()
    meta = doc.get("metadata_entries") or []
    fp = doc.get("filepath")
    status = _archive_status(fp)
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
        "ocr_text_full": _extract_ocr_text(meta),
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
    r = httpx.get(f"{BASE}/api/entities/{int(entity_id)}", timeout=TIMEOUT)
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
        <key>PATH</key><string>{Path.home()}/.local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
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
    r = httpx.get(f"{BASE}/api/health", timeout=5.0)
    return {
        "status_code": r.status_code,
        "body": r.json(),
        "archive_configured": COS is not None,
        "archive_device": COS.get("PENSIEVE_DEVICE") if COS else None,
        "archive_bucket": COS.get("COS_BUCKET") if COS else None,
    }


if __name__ == "__main__":
    mcp.run()
