"""Offline tests of ds-nbc/1 Track B's conversion and analysis (synthetic SIMULATION ledgers; no runs, no model)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_tools" / "nbc"))
sys.path.insert(0, str(ROOT / "research_tools"))


def rec(case, arm, rep, slot, verdict, used, first=None, usd=0.0):
    return {"case_id": case, "arm": arm, "replicate": rep, "slot": slot, "verdict": verdict, "schedules_used": used,
            "first_violation_schedule": first, "wall_seconds": 10.0 * used, "invalid_proposals": 2 if usd else 0,
            "tokens_in": 100 if usd else 0, "tokens_out": 50 if usd else 0, "est_usd": usd, "SIMULATION": True}


def test_conversion_takes_the_buggy_slot_in_replicate_order_with_its_control(tmp_path):
    import run_nbc
    import track_b
    out = tmp_path / "c1"
    lg = run_nbc.Ledger(out / "RESULTS_LEDGER.jsonl")
    for r in [rec("c1", "RA-DET", 0, "R1", "HOLDS", 4), rec("c1", "RA-DET", 0, "R2", "HOLDS", 4),
              rec("c1", "RA-DET", 1, "R1", "VIOLATED", 2, 2), rec("c1", "RA-DET", 1, "R2", "VIOLATED", 3, 3),
              rec("c1", "RA-MD", 0, "R1", "VIOLATED", 1, 1, 0.02), rec("c1", "RA-MD", 0, "R2", "HOLDS", 4, None, 0.02)]:
        lg.append(r)
    rows = track_b.to_paired([out], {"c1": "R1"})
    det = [r for r in rows if r["arm"] == "RA-DET"]
    assert [r["run_id"] for r in det] == ["r0", "r1"]
    assert det[1]["detected"] and det[1]["fixed_version_violation"] and not det[0]["fixed_version_violation"]
    md = [r for r in rows if r["arm"] == "RA-MD"]
    assert md[0]["est_usd"] == 0.02 and md[0]["invalid_proposals"] == 2 and md[0]["schedule_budget"] == 12


def test_the_analysis_is_insufficient_below_ten_cases_and_carries_intervals(tmp_path):
    import paired_eval as pe
    import run_nbc
    import track_b
    outs = []
    for i in range(3):
        out = tmp_path / f"c{i}"
        lg = run_nbc.Ledger(out / "RESULTS_LEDGER.jsonl")
        for arm, v in (("RA-DET", "VIOLATED"), ("RA-MD", "HOLDS" if i else "VIOLATED")):
            lg.append(rec(f"c{i}", arm, 0, "R1", v, 1 if v == "VIOLATED" else 4, 1 if v == "VIOLATED" else None))
            lg.append(rec(f"c{i}", arm, 0, "R2", "HOLDS", 4))
        outs.append(out)
    rows = track_b.to_paired(outs, {f"c{i}": "R1" for i in range(3)})
    p = tmp_path / "runs.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    table, _ = pe.paired_table(pe.load([p]), "RA-MD", "RA-DET")
    res = pe.compare(table, "RA-MD", "RA-DET")
    assert res["verdict"].startswith("INSUFFICIENT SAMPLE")
    assert track_b.clopper_pearson(3, 3)[0] < 0.3 and track_b.clopper_pearson(0, 3) == (0.0, 0.7076)
