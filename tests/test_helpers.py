"""Tests for pure helpers in scripts/pensieve-mcp.py.

These don't talk to memos. Touching the real REST API would make tests
flaky and slow; we exercise time parsing, OCR extraction, archive-status
classification, and the http-error translator.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import httpx
import pytest


def test_parse_time_unix_int(mcp_module):
    assert mcp_module._parse_time(1700000000) == 1700000000
    assert mcp_module._parse_time("1700000000") == 1700000000


def test_parse_time_iso(mcp_module):
    out = mcp_module._parse_time("2026-04-25T13:00")
    expected = int(datetime(2026, 4, 25, 13, 0).astimezone().timestamp())
    assert out == expected


def test_parse_time_none(mcp_module):
    assert mcp_module._parse_time(None) is None
    assert mcp_module._parse_time("") is None


def test_parse_time_bad_input_raises(mcp_module):
    with pytest.raises(ValueError):
        mcp_module._parse_time("not a date")


def test_parse_range_today(mcp_module):
    s, e = mcp_module._parse_range("today", None)
    assert s is not None and e is not None
    assert s <= e
    assert e - s <= 86400  # at most one day


def test_parse_range_yesterday(mcp_module):
    s, e = mcp_module._parse_range("yesterday", None)
    assert e - s == 86400  # full day


def test_parse_range_last_24h(mcp_module):
    s, e = mcp_module._parse_range("last 24h", None)
    assert 86390 <= e - s <= 86410  # ~24h, allow second-of-clock skew


def test_parse_range_last_7d(mcp_module):
    s, e = mcp_module._parse_range("last 7d", None)
    assert 7 * 86400 - 10 <= e - s <= 7 * 86400 + 10


def test_parse_range_period_today_afternoon(mcp_module):
    s, e = mcp_module._parse_range("today afternoon", None)
    # 12:00 → 18:00 = 6h window
    assert e - s == 6 * 3600


def test_parse_range_explicit_pair(mcp_module):
    s, e = mcp_module._parse_range("2026-04-01", "2026-04-02")
    assert e > s


def test_extract_ocr_text_concatenates(mcp_module):
    meta = [{
        "key": "ocr_result",
        "value": [
            {"rec_txt": "hello"},
            {"rec_txt": "world"},
            {"rec_txt": ""},  # empty boxes filtered
            {"not_text": True},  # non-dict-like skipped
        ],
    }]
    assert mcp_module._extract_ocr_text(meta) == "hello world"


def test_extract_ocr_text_missing(mcp_module):
    assert mcp_module._extract_ocr_text([]) == ""
    assert mcp_module._extract_ocr_text([{"key": "other"}]) == ""


def test_meta_get(mcp_module):
    meta = [
        {"key": "active_app", "value": "Safari"},
        {"key": "active_window", "value": "None"},
        {"key": "url", "value": None},
    ]
    assert mcp_module._meta_get(meta, "active_app") == "Safari"
    assert mcp_module._meta_get(meta, "active_window") == ""  # "None" string filtered
    assert mcp_module._meta_get(meta, "url") == ""
    assert mcp_module._meta_get(meta, "missing") == ""


def test_summarize_hit_truncates_ocr(mcp_module):
    long_text = "a" * 1500
    doc = {
        "id": 42,
        "filepath": "/tmp/missing.webp",
        "file_created_at": "2026-04-25T13:00:00",
        "metadata_entries": [
            {"key": "active_app", "value": "Safari"},
            {"key": "ocr_result", "value": [{"rec_txt": long_text}]},
        ],
    }
    out = mcp_module._summarize_hit(doc, include_ocr=True)
    assert out["id"] == 42
    assert out["app"] == "Safari"
    assert len(out["ocr_text"]) == 801  # 800 + ellipsis
    assert out["ocr_text"].endswith("…")


def test_summarize_hit_skip_ocr(mcp_module):
    doc = {"id": 1, "filepath": None, "metadata_entries": []}
    out = mcp_module._summarize_hit(doc, include_ocr=False)
    assert "ocr_text" not in out


def test_archive_status_local(mcp_module, tmp_path):
    f = tmp_path / "shot.webp"
    f.write_bytes(b"x")
    assert mcp_module._archive_status(str(f)) == "local"


def test_archive_status_unknown_when_no_path(mcp_module):
    assert mcp_module._archive_status(None) == "unknown"
    assert mcp_module._archive_status("") == "unknown"


def test_archive_status_unknown_when_no_cos(mcp_module, tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_module, "COS", None)
    assert mcp_module._archive_status(str(tmp_path / "missing.webp")) == "unknown"


def test_cos_key_for_relative_to_shot_dir(mcp_module, tmp_path, monkeypatch):
    shot_dir = tmp_path / "screenshots"
    shot_dir.mkdir()
    monkeypatch.setattr(mcp_module, "SHOT_DIR", shot_dir)
    monkeypatch.setattr(mcp_module, "COS", {"PENSIEVE_DEVICE": "test-mac"})
    f = shot_dir / "2026-04-25" / "shot-001.webp"
    f.parent.mkdir()
    f.touch()
    assert mcp_module._cos_key_for(str(f)) == "test-mac/2026-04-25/shot-001.webp"


def test_cos_key_for_outside_shot_dir(mcp_module, tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_module, "SHOT_DIR", tmp_path / "screenshots")
    monkeypatch.setattr(mcp_module, "COS", {"PENSIEVE_DEVICE": "test"})
    # file is outside SHOT_DIR — should reject (path traversal guard)
    assert mcp_module._cos_key_for("/etc/passwd") is None


def test_translate_http_error_401(mcp_module):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 401
    resp.text = "unauthorized"
    exc = httpx.HTTPStatusError("401", request=MagicMock(), response=resp)
    out = mcp_module._translate_http_error(exc)
    assert out["error"] == "http_status"
    assert out["status"] == 401
    assert "PENSIEVE_TOKEN" in out["hint"]


def test_translate_http_error_500(mcp_module):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 502
    resp.text = "bad gateway"
    exc = httpx.HTTPStatusError("502", request=MagicMock(), response=resp)
    out = mcp_module._translate_http_error(exc)
    assert out["status"] == 502
    assert "memos" in out["hint"].lower()


def test_translate_http_error_connect(mcp_module):
    exc = httpx.ConnectError("refused")
    out = mcp_module._translate_http_error(exc)
    assert out["error"] == "connect_failed"
    assert "memos serve" in out["hint"]


def test_peer_hostname(mcp_module):
    assert mcp_module._peer_hostname("http://100.1.2.3:8839") == "100.1.2.3"
    assert mcp_module._peer_hostname("https://example.tail.ts.net") == "example.tail.ts.net"


def test_maybe_warning_below_threshold(mcp_module):
    payload = {"hits": ["a", "b"]}
    out = mcp_module._maybe_warning(payload, threshold=10000)
    assert "_warning" not in out


def test_maybe_warning_above_threshold(mcp_module):
    payload = {"hits": ["x" * 1000 for _ in range(20)]}
    out = mcp_module._maybe_warning(payload, threshold=1000)
    assert "_warning" in out
    assert "activity_summary" in out["_warning"]
