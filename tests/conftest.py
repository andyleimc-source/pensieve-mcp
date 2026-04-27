"""Stub-load scripts/pensieve-mcp.py as a regular module for testing.

The script normally runs via `uv run --script` and constructs a FastMCP
instance + decorates real network tools. Tests only exercise pure helpers,
so we stub `mcp.server.fastmcp.FastMCP` with a no-op recorder before
importing the script.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "pensieve-mcp.py"


class _StubMCP:
    def __init__(self, *args, **kwargs):
        self.tools = []

    def tool(self, *dargs, **dkwargs):
        def deco(fn):
            self.tools.append(fn.__name__)
            return fn
        return deco

    def run(self):  # never called in tests
        raise RuntimeError("StubMCP.run should not be invoked from tests")


@pytest.fixture(scope="session")
def mcp_module():
    """Import scripts/pensieve-mcp.py as a module with FastMCP stubbed.

    Cached for the session — module load triggers _load_api_token / _load_cos_env
    file IO once, which is fine since tests don't depend on that state.
    """
    fake_pkg = types.ModuleType("mcp")
    fake_server = types.ModuleType("mcp.server")
    fake_fastmcp = types.ModuleType("mcp.server.fastmcp")
    fake_fastmcp.FastMCP = _StubMCP
    fake_server.fastmcp = fake_fastmcp
    fake_pkg.server = fake_server
    sys.modules.setdefault("mcp", fake_pkg)
    sys.modules.setdefault("mcp.server", fake_server)
    sys.modules.setdefault("mcp.server.fastmcp", fake_fastmcp)

    spec = importlib.util.spec_from_file_location("pensieve_mcp", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
