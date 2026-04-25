#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mcp>=1.2.0",
#   "httpx>=0.27",
# ]
# ///
"""Pensieve MCP server. Wraps the local memos REST API at :8839.

Exposes three tools for Claude Code to search and fetch screen history.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

BASE = os.environ.get("PENSIEVE_BASE_URL", "http://localhost:8839")
LIBRARY_ID = int(os.environ.get("PENSIEVE_LIBRARY_ID", "1"))
TIMEOUT = 30.0

mcp = FastMCP("pensieve")


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
    return {
        "id": doc.get("id"),
        "filepath": doc.get("filepath"),
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

    Returns a list of summarized hits with id, timestamp, app, window,
    and OCR text snippet. Use get_screenshot(id) for full details.
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
    """Fetch full details for one screenshot by id (including full OCR text)."""
    r = httpx.get(f"{BASE}/api/entities/{int(entity_id)}", timeout=TIMEOUT)
    r.raise_for_status()
    doc = r.json()
    meta = doc.get("metadata_entries") or []
    return {
        "id": doc.get("id"),
        "filepath": doc.get("filepath"),
        "created_at": doc.get("file_created_at"),
        "app": _meta_get(meta, "active_app"),
        "window": _meta_get(meta, "active_window"),
        "screen": _meta_get(meta, "screen_name"),
        "url": _meta_get(meta, "url"),
        "tags": doc.get("tags") or [],
        "ocr_text_full": _extract_ocr_text(meta),
    }


@mcp.tool()
def health() -> dict[str, Any]:
    """Check that the Pensieve REST API is up."""
    r = httpx.get(f"{BASE}/api/health", timeout=5.0)
    return {"status_code": r.status_code, "body": r.json()}


if __name__ == "__main__":
    mcp.run()
