"""Offline tests of ds-nbc/2's tooling: the outcome-blind D2 generator, allocation, refusals and case-level cells.
SIMULATION integrations and synthetic ledgers only (no database, container or model)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_tools" / "nbc2"))
import ds_nbc2 as d  # noqa: E402


class FakeInteg:
    WORKLOAD_JSON_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["jobs", "flag"],
                            "properties": {"jobs": {"type": "array", "minItems": 1, "maxItems": 2, "items": {
                                "type": "object", "additionalProperties": False, "required": ["n"],
                                "properties": {"n": {"type": "integer", "minimum": 0, "maximum": 3}}}},
                                "flag": {"type": "boolean"}}}

    @staticmethod
    def validate(w):
        ns = [j["n"] for j in w["jobs"]]
        return [] if len(set(ns)) == len(ns) else ["distinct n"]


def test_d2_is_deterministic_valid_distinct_and_schema_only():
    a = d.d2_workloads(FakeInteg, "case-x")
    b = d.d2_workloads(FakeInteg, "case-x")
    assert a == b and len(a["workloads"]) == d.K
    assert all(not FakeInteg.validate(w) for w in a["workloads"])
    assert len({d.wsha(w) for w in a["workloads"]}) == d.K
    assert d.d2_workloads(FakeInteg, "case-y")["workloads"] != a["workloads"]  # seeded by case id only


def test_seeds_and_arm_order_are_frozen_functions_of_the_case():
    assert d.seed("c", 0, 1) == d.seed("c", 0, 1) != d.seed("c", 0, 2)
    assert sorted(d.arm_order("c")) == ["D1", "D2", "M"] and d.arm_order("c") == d.arm_order("c")


def run(case, arm, k, j, slot, verdict="SOUND", oracle="HOLDS", w=None, secs=1.0):
    return {"case_id": case, "arm": arm, "k": k, "j": j, "slot": slot, "verdict": verdict, "oracle": oracle,
            "workload_sha256": d.wsha(w if w is not None else {"k": k}), "wall_seconds": secs,
            "corroboration": {"ok": True, "params_compared": 2, "params_mismatched": 0, "params_unchecked": 0,
                              "matched": 3}}


def test_cell_counts_first_detection_in_slot_order_and_false_positives():
    rs = [run("c", "M", 0, 1, "R1"), run("c", "M", 0, 2, "R1", verdict="INCOMPLETE", oracle=None),
          run("c", "M", 1, 1, "R1", oracle="VIOLATED"), run("c", "M", 1, 2, "R1", oracle="VIOLATED"),
          run("c", "M", 0, 1, "R2"), run("c", "M", 1, 1, "R2", oracle="VIOLATED")]
    c = d.cell(rs, "c", "M", "R1")
    assert c["detected"] and c["schedules_to_detection"] == 3 and c["seconds_to_detection"] == 3.0
    assert c["false_positive"] and c["R1"]["INCOMPLETE"] == 1 and c["workloads_that_violated"] == 1
    assert c["params_mismatched"] == 0


def test_a_violation_on_a_non_sound_run_is_never_a_detection():
    rs = [run("c", "D2", 0, 1, "R1", verdict="REFUSED", oracle="VIOLATED")]
    assert not d.cell(rs, "c", "D2", "R1")["detected"]


def test_paired_rows_carry_equal_budgets_for_every_arm():
    rs = [run("c", a, k, j, s) for a in d.ARMS for k in range(d.K) for j in (1, 2) for s in ("R1", "R2")]
    rows = d.paired_rows(rs, {"c": "R1"}, {"c": {"tokens_in": 5, "tokens_out": 7, "est_usd": 0.01}}, {"c": 1})
    assert {r["schedule_budget"] for r in rows} == {12}
    assert sum(r["schedules_used"] for r in rows if r["arm"] == "D2") == 12
    m0 = [r for r in rows if r["arm"] == "M" and r["seq"] == 0][0]
    assert m0["est_usd"] == 0.01 and m0["invalid_proposals"] == 1


def test_run_refuses_without_journaled_muse_proposals(tmp_path):
    man = tmp_path / "MANIFEST.json"
    spec = json.loads((ROOT / d.CASES["chancy-2895c4a"]).read_text())
    man.write_text(json.dumps({"cases": {"chancy-2895c4a": {
        "spec": d.CASES["chancy-2895c4a"], "spec_sha256": d.sha(ROOT / d.CASES["chancy-2895c4a"]),
        "integration": spec["integration"], "integration_sha256": d.sha(ROOT / spec["integration"]),
        "oracle_sha256": spec["oracle"]["sha256"]}}}))
    ns = d.argparse.Namespace(manifest=man, case="chancy-2895c4a", out=tmp_path / "out")
    with pytest.raises(SystemExit, match="propose"):
        d.cmd_run(ns)
