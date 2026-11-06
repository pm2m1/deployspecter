"""EXPERIMENTAL, live and isolated: Chancy 0.26.0's unmodified worker on a Phase 9B-constructed initial state
(ds-p9x/1), via ``research_tools/run_chancy_phase9b.py``. It needs the pinned sdists and the disposable MVP
PostgreSQL. The runner's own exit status and SUMMARY are the verdict."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SDISTS = Path(os.environ.get("DS_THIRDPARTY_SDIST_DIR", "/nonexistent"))
SECRET = Path(os.path.expanduser("~/.ds_mvp_pg_password"))
pytestmark = pytest.mark.skipif(not (SDISTS.is_dir() and SECRET.exists()),
                                reason="set DS_THIRDPARTY_SDIST_DIR and start the disposable MVP PostgreSQL")


def test_chancy_runs_on_a_phase9b_constructed_state(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("MODEL_API_KEY", "DATABASE_URL")}
    out = tmp_path / "run"
    proc = subprocess.run([sys.executable, str(REPO / "research_tools/run_chancy_phase9b.py"), "--out", str(out),
                           "--sdists", str(SDISTS)], capture_output=True, text=True, timeout=1200, env=env)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    s = json.loads((out / "SUMMARY.json").read_text())
    assert s["verdict"] == "as expected" and s["comparison"]["equal"]
    for label in ("single", "multi"):
        r = s["results"][label]
        assert r["status"] == "ran" and r["classification"] == "ORIGINAL_VALID" and r["oracle_violations"] == []
        assert r["corroboration"]["params_mismatched"] == 0 and r["corroboration"]["params_unchecked"] == 0
        d = json.loads((out / f"p9x_{label}.json").read_text())
        assert d["construction"]["trace"]["required"][
            "deployspecter._phase9b_ext_v1.state_constructor.construct_state"] == 1
        assert d["construction"]["readback"]["verified"] and d["bound_to_verified_state"]
    assert s["results"]["unregistered_handler"]["status"] == "refused_workload"
