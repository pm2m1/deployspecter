"""Regression (offline): the stale-build-context hazard. Normalized archives carry mtime 0, and Docker's context
transfer can then reuse an earlier, same-size file. build_image now stamps fresh mtimes and verifies the image's
/app tree file by file. The live path is exercised by tests/thirdparty_isolated (every image is built through it)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_tools" / "isolated"))
sys.path.insert(0, str(ROOT / "src"))
import run_isolated as ri  # noqa: E402


def test_fresh_mtimes_replace_the_normalized_zero_mtime(tmp_path):
    f = tmp_path / "app" / "src" / "pkg" / "__init__.py"
    f.parent.mkdir(parents=True)
    f.write_text("VERSION = '0.26.4'\n")
    os.utime(f, (0, 0))
    ri.stamp_fresh_mtimes(tmp_path)
    assert f.stat().st_mtime > 1_000_000_000


def test_a_same_size_stale_file_in_the_image_is_caught():
    staged = {"src/pkg/__init__.py": "a" * 64, "src/pkg/worker.py": "b" * 64}
    assert ri.image_tree_problems(staged, dict(staged)) == []
    stale = {**staged, "src/pkg/__init__.py": "c" * 64}  # same path, same size, other content (saq erratum E1)
    assert ri.image_tree_problems(staged, stale) == ["differs src/pkg/__init__.py"]
    assert ri.image_tree_problems(staged, {"src/pkg/worker.py": "b" * 64}) == ["missing src/pkg/__init__.py"]
    assert ri.image_tree_problems(staged, {**staged, "src/extra.py": "d" * 64}) == ["extra src/extra.py"]


def test_the_context_tree_hashes_every_staged_file_except_bytecode(tmp_path):
    (tmp_path / "src" / "__pycache__").mkdir(parents=True)
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "src" / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\0")
    t = ri.context_tree(tmp_path)
    assert list(t) == ["src/a.py"] and len(t["src/a.py"]) == 64


def test_the_in_image_script_is_network_free_python_only():
    assert "import hashlib,json,os" in ri.IMAGE_TREE_SCRIPT and "socket" not in ri.IMAGE_TREE_SCRIPT
