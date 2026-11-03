"""Importing any research module or research tool opens no connection and calls no model."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "src/deployspecter/research"
MODULES = sorted(".".join(p.relative_to(ROOT / "src").with_suffix("").parts) for p in RESEARCH.rglob("*.py")
                 if p.name != "__init__.py")
TOOLS = sorted(p.stem for p in (ROOT / "research_tools").glob("*.py"))

PROBE = """
import os, socket, sys
import psycopg
def refuse(*a, **k):
    raise SystemExit("connection attempted during import")
socket.socket.connect = refuse
socket.create_connection = refuse
psycopg.connect = refuse
sys.path.insert(0, {tools!r})
import importlib
for name in {names!r}:
    importlib.import_module(name)
assert "MODEL_API_KEY" not in os.environ or not os.environ["MODEL_API_KEY"]
print("ok")
"""


def test_every_research_module_and_tool_imports_offline():
    code = PROBE.format(tools=str(ROOT / "research_tools"), names=[*MODULES, *TOOLS])
    env = {"PATH": "/usr/bin:/bin", "HOME": str(ROOT)}
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, env=env)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-500:]
    assert len(MODULES) >= 9 and "o6_confirm_v2" in TOOLS
