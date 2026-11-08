"""Offline tests of the ds-nbc/1 tooling: the RA-NONBLIND orchestrator, its summary, and the sprint's Meta ledger.
The integration here is a SIMULATION fake (no database, no container, no model)."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_tools" / "nbc"))

FAKE = '''# SIMULATION integration for the ds-nbc/1 offline tests
WORKLOAD_JSON_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["n"],
                        "properties": {"n": {"type": "integer"}}}
MUSE_CONTEXT = {"registry": ["op(n)"]}
def validate(w):
    return [] if isinstance(w, dict) and w.get("n") in (0, 1, 2) else ["bad"]
def workloads(arm):
    if arm != "RA-DET":
        raise ValueError(arm)
    return [{"n": 0}, {"n": 1}, {"n": 2}]
def prepare(archive, sha256, workdir):
    return {"bug": b"BUG" in archive.read_bytes()}
def run(handle, workload, seed, out, *, oracle_code, oracle_sha256):
    i = int(str(out)[-2:])
    v = "VIOLATED" if handle["bug"] and workload["n"] == 1 and i == 3 else "HOLDS"
    return {"verdict": "SOUND", "oracle": v, "seed": seed,
            "corroboration": {"ok": True, "matched": 5, "params_compared": 2, "params_mismatched": 0,
                              "params_unchecked": 0}}
'''


def spec(tmp: Path) -> Path:
    (tmp / "r1.tar").write_bytes(b"SIMULATION BUG")
    (tmp / "r2.tar").write_bytes(b"SIMULATION fixed")
    integ = tmp / "fake_integ.py"
    integ.write_text(FAKE)
    oracle = tmp / "oracle.py"
    oracle.write_text("def check(final_state, raw_results):\n    return 'HOLDS'\n")
    h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
    s = {"case_id": "sim-nbc", "application": "fake", "integration": str(integ),
         "R1": {"archive": str(tmp / "r1.tar"), "sha256": h(tmp / "r1.tar")},
         "R2": {"archive": str(tmp / "r2.tar"), "sha256": h(tmp / "r2.tar")}, "buggy": "R1",
         "oracle": {"path": str(oracle), "sha256": h(oracle)}}
    p = tmp / "spec.json"
    p.write_text(json.dumps(s))
    return p


def test_reproduction_run_and_case_level_summary(tmp_path):
    import run_nbc
    out = tmp_path / "out"
    a = run_nbc.argparse.Namespace(cases=[str(spec(tmp_path))], arms=["RA-DET"], out=out, replicates=[0, 1, 2],
                                   env_file=tmp_path / "none")
    assert run_nbc.cmd_run(a) == 0
    s = run_nbc.summarize(out)
    (row,) = s["rows"]
    # replicate 0 (n=0) uses 4 schedules without a violation; replicate 1 (n=1) violates at schedule 3
    assert row["detected"] and row["verified_detection"] and not row["false_positive"]
    assert row["schedules_to_detection"] == 4 + 3
    assert row["fixed_schedules"] == 12 and row["runs"] == {"HOLDS": 5, "VIOLATED": 1}
    assert row["params_mismatched"] == 0 and row["statements_matched"] > 0
    assert all(e["run"]["RA-NONBLIND"] for e in run_nbc.Ledger(out / "RESULTS_LEDGER.jsonl").entries())
    # a rerun resumes: nothing is executed twice
    assert run_nbc.cmd_run(a) == 0
    assert len(run_nbc.Ledger(out / "RESULTS_LEDGER.jsonl").entries()) == 6


def test_a_tampered_ledger_is_refused(tmp_path):
    import run_nbc
    out = tmp_path / "out"
    a = run_nbc.argparse.Namespace(cases=[str(spec(tmp_path))], arms=["RA-DET"], out=out, replicates=[0],
                                   env_file=tmp_path / "none")
    run_nbc.cmd_run(a)
    p = out / "RESULTS_LEDGER.jsonl"
    p.write_text(p.read_text().replace('"HOLDS"', '"VIOLATED"', 1))
    with pytest.raises(SystemExit, match="broken"):
        run_nbc.summarize(out)


def test_a_changed_archive_is_refused(tmp_path):
    import run_nbc
    sp = spec(tmp_path)
    (tmp_path / "r1.tar").write_bytes(b"SIMULATION other")
    a = run_nbc.argparse.Namespace(cases=[str(sp)], arms=["RA-DET"], out=tmp_path / "out", replicates=[0],
                                   env_file=tmp_path / "none")
    with pytest.raises(SystemExit, match="sha256"):
        run_nbc.cmd_run(a)


def test_the_sprint_ledger_refuses_past_the_cap_and_counts_open_reservations(tmp_path, monkeypatch):
    monkeypatch.setenv("DS_NBC_META_LEDGER", str(tmp_path / "ledger.jsonl"))
    import budget
    importlib.reload(budget)
    r1 = budget.reserve("a", 2.0)
    budget.settle(r1, est_usd=0.5, tokens_in=10, tokens_out=20, ok=True)
    budget.reserve("b", 3.0)  # open: its worst case stays counted
    assert budget.committed_usd() == 3.5
    with pytest.raises(budget.BudgetRefused):
        budget.reserve("c", 1.6)
    budget.reserve("d", 1.5)
    assert budget.summary()["refusals"] == 1 and budget.committed_usd() == 5.0


def test_a_failed_request_keeps_its_worst_case(tmp_path, monkeypatch):
    monkeypatch.setenv("DS_NBC_META_LEDGER", str(tmp_path / "ledger.jsonl"))
    import budget
    importlib.reload(budget)
    rid = budget.reserve("x", 0.07)
    budget.settle(rid, est_usd=None, tokens_in=None, tokens_out=None, ok=False, detail="timeout")
    assert budget.committed_usd() == 0.07


def test_the_proposer_never_sends_when_the_ledger_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("DS_NBC_META_LEDGER", str(tmp_path / "ledger.jsonl"))
    import budget
    importlib.reload(budget)
    import muse_workloads
    importlib.reload(muse_workloads)
    budget.reserve("fill", 4.99)
    integ = type("I", (), {"MUSE_CONTEXT": {"registry": []}, "WORKLOAD_JSON_SCHEMA": {"type": "object"}})
    p = muse_workloads.Proposer(integ, tmp_path / "j", p=6, env_file=tmp_path / "no.env")
    with pytest.raises(budget.BudgetRefused):
        p({"case_id": "c"}, "RA-MD", 0)
    assert not (tmp_path / "j").exists()  # nothing was sent or journaled
