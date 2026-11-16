"""Solo r3 design: the detection-level sensitivity analysis, and the run-record fields it needs.

    python research_tools/solo/detection_level.py --runs RUNS.jsonl... --out DIR \
        [--muse RA-MD] [--primary RA-DET-G] [--secondary RA-DET] [--budget N]

Every detection is attributed to the maintainer-test assertions it rests on. A detection counts in this analysis only
if at least one VIOLATED assertion at that schedule has ``uses_deployspecter_code: false``. The case-level strict
subset (``ANALYSIS_PLAN_SOLO.md``) can't show a case that is detected only through DeploySpecter's own tables; this
count can.

## Results schema: ``ds-solo-run/1``

A run record is a ``research_tools/paired_eval.py`` run record, with its required fields unchanged, plus:

    record_format         "ds-solo-run/1"
    defective_schedules   the defective revision's schedules this run consumed, in order (exactly
                          ``schedules_used`` of them, numbered 1..schedules_used):
        {"schedule": int,
         "status": "VIOLATED" | "HOLDS" | "INCOMPLETE" | "REFUSED" | "NO_VERDICT",
         "violated_assertions": [{"line": int, "uses_deployspecter_code": bool}, ...]}

``status`` is the schedule's verdict as the main analysis counts it: VIOLATED is a SOUND, corroborated violation of
the case's maintainer oracle. ``violated_assertions`` is the maintainer oracle's verdict record for that schedule
(``MaintainerOracle.evaluate``, key ``violated_assertions``; build it with ``schedule_entry``). It is non-empty
exactly when the status is VIOLATED.

Checked on load (``Refused`` otherwise): the ``paired_eval`` checks; ``first_detection_schedule`` is the first
VIOLATED schedule; every flag is a boolean (a format-1 spec has no flags and can't enter this analysis).

Derived per run by ``annotate`` and written into the record:

    detection_violated_with_deployspecter_code      lines VIOLATED at the first detection, flag true
    detection_violated_without_deployspecter_code   lines VIOLATED at the first detection, flag false
    first_detection_schedule_without_deployspecter_code
                                                    the first VIOLATED schedule with a flag-false VIOLATED assertion,
                                                    else null
    detected_without_deployspecter_code             bool

## The analysis (``analyse``)

The primary comparison (exact McNemar, the Muse arm against RA-DET-G), the secondary one (against RA-DET; Holm across
the two McNemar tests) and time to detection (``paired_eval``'s Gehan sign test and Kaplan-Meier medians) are run
twice, by ``paired_eval``'s own code: on the records as they are (``main``), and on a view in which a run detects at
``first_detection_schedule_without_deployspecter_code`` (``detection_level``). In that view nothing else changes: the
same cases, budgets and costs, and ``fixed_version_violation`` is kept as recorded (a corrected-revision violation by
any assertion still counts against the arm). Per arm, the report puts the two detection counts side by side.

The detection-level result is a sensitivity analysis and never replaces the main one. With fewer than 10 paired
defect cases it is descriptive only, and the report says so in those words.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paired_eval as pe  # noqa: E402

FORMAT = "ds-solo-run/1"
STATUSES = ("VIOLATED", "HOLDS", "INCOMPLETE", "REFUSED", "NO_VERDICT")
DERIVED = ("detection_violated_with_deployspecter_code", "detection_violated_without_deployspecter_code",
           "first_detection_schedule_without_deployspecter_code", "detected_without_deployspecter_code")
Refused = pe.Refused


def schedule_entry(schedule: int, status: str, oracle_record: dict[str, Any]) -> dict[str, Any]:
    """One ``defective_schedules`` entry from the main analysis's status and ``MaintainerOracle.evaluate``'s record."""
    if status not in STATUSES:
        raise Refused(f"schedule {schedule}: unknown status {status!r}")
    if status == "VIOLATED" and oracle_record.get("verdict") != "VIOLATED":
        raise Refused(f"schedule {schedule}: status VIOLATED but the maintainer oracle says "
                      f"{oracle_record.get('verdict')!r}")
    violated = (oracle_record.get("violated_assertions") or []) if status == "VIOLATED" else []
    return {"schedule": schedule, "status": status,
            "violated_assertions": [{"line": v["line"], "uses_deployspecter_code": v["uses_deployspecter_code"]}
                                    for v in violated]}


def validate(row: dict[str, Any], where: str = "") -> dict[str, Any]:
    pe.validate(row, where)
    if row.get("record_format") != FORMAT:
        raise Refused(f"{where}: record_format must be {FORMAT!r}")
    sched = row.get("defective_schedules")
    if not isinstance(sched, list) or len(sched) != row["schedules_used"]:
        raise Refused(f"{where}: defective_schedules must list exactly schedules_used ({row['schedules_used']}) "
                      "schedules")
    first = None
    for i, s in enumerate(sched, 1):
        if s.get("schedule") != i:
            raise Refused(f"{where}: defective_schedules must be numbered 1..{len(sched)} in order")
        if s.get("status") not in STATUSES:
            raise Refused(f"{where}: schedule {i}: unknown status {s.get('status')!r}")
        va = s.get("violated_assertions")
        if not isinstance(va, list) or bool(va) != (s["status"] == "VIOLATED"):
            raise Refused(f"{where}: schedule {i}: violated_assertions must be non-empty exactly when VIOLATED")
        for v in va:
            if not isinstance(v.get("line"), int) or not isinstance(v.get("uses_deployspecter_code"), bool):
                raise Refused(f"{where}: schedule {i}: each violated assertion needs an int line and a boolean "
                              "uses_deployspecter_code (format-1 specs have no flags)")
        if s["status"] == "VIOLATED" and first is None:
            first = i
    if first != row["first_detection_schedule"]:
        raise Refused(f"{where}: first_detection_schedule {row['first_detection_schedule']} is not the first "
                      f"VIOLATED schedule ({first})")
    return row


def annotate(row: dict[str, Any]) -> dict[str, Any]:
    """The record with the derived detection-level fields (recomputed, so a stale value can't survive)."""
    out = {k: v for k, v in row.items() if k not in DERIVED}
    first = row["first_detection_schedule"]
    at_first = row["defective_schedules"][first - 1]["violated_assertions"] if first else []
    out["detection_violated_with_deployspecter_code"] = [v["line"] for v in at_first if v["uses_deployspecter_code"]]
    out["detection_violated_without_deployspecter_code"] = [v["line"] for v in at_first
                                                            if not v["uses_deployspecter_code"]]
    strict = next((s["schedule"] for s in row["defective_schedules"] if s["status"] == "VIOLATED" and any(
        not v["uses_deployspecter_code"] for v in s["violated_assertions"])), None)
    out["first_detection_schedule_without_deployspecter_code"] = strict
    out["detected_without_deployspecter_code"] = strict is not None
    return out


def load(paths: list[Path]) -> list[dict[str, Any]]:
    rows = pe.load(paths)
    return [annotate(validate(r, f"{r['case']}/{r['arm']}/{r['run_id']}")) for r in rows]


def detection_level_view(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The records with each run detecting only where a VIOLATED assertion uses no DeploySpecter code."""
    out = []
    for r in rows:
        r = annotate(r)
        s = r["first_detection_schedule_without_deployspecter_code"]
        out.append(dict(r, detected=s is not None, first_detection_schedule=s))
    return out


def holm(ps: list[float]) -> list[float]:
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj = [0.0] * len(ps)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = running
    return adj


def _view(rows: list[dict[str, Any]], muse: str, comparators: list[str], budget: int | None) -> dict[str, Any]:
    comps = {}
    for y in comparators:
        table, excluded = pe.paired_table(rows, muse, y, budget=budget)
        comps[f"{muse} vs {y}"] = pe.compare(table, muse, y, excluded=excluded)
    adj = holm([c["mcnemar"]["p_exact_two_sided"] for c in comps.values()])
    for c, p in zip(comps.values(), adj, strict=True):
        c["mcnemar"]["p_holm"] = p
    return comps


def analyse(rows: list[dict[str, Any]], muse: str = "RA-MD", primary: str = "RA-DET-G",
            secondary: str | None = "RA-DET", budget: int | None = None) -> dict[str, Any]:
    rows = [dict(annotate(r), _order=r.get("_order", i)) for i, r in enumerate(rows)]  # file order, as pe.load
    arms = {r["arm"] for r in rows}
    comparators = [primary] + ([secondary] if secondary and secondary in arms else [])
    view = detection_level_view(rows)
    main, level = _view(rows, muse, comparators, budget), _view(view, muse, comparators, budget)
    cm, cl = pe.cells(rows, budget), pe.cells(view, budget)
    per_arm = {}
    for arm in sorted(arms):
        keys = [k for k in cm if k[1] == arm and cm[k]["case_kind"] != "no_known_defect"]
        a, b = sum(cm[k]["detected"] for k in keys), sum(cl[k]["detected"] for k in keys)
        per_arm[arm] = {"defect_cases": len(keys), "detected_main": a, "detected_detection_level": b,
                        "detected_only_through_deployspecter_code": a - b,
                        "cases_detected_only_through_deployspecter_code": sorted(
                            k[0] for k in keys if cm[k]["detected"] and not cl[k]["detected"])}
    n = min(c["paired_defect_cases"] for c in level.values())
    label = ("Detection-level sensitivity analysis: only detections with at least one VIOLATED assertion that uses "
             "no DeploySpecter code are counted. It does not replace the main result.")
    if n < pe.MIN_CASES:
        label += f" Descriptive only: {n} paired defect cases, fewer than {pe.MIN_CASES}."
    return {"format": "ds-solo-detection-level/1", "arms": {"muse": muse, "comparators": comparators},
            "per_arm": per_arm, "main": main, "detection_level": level, "detection_level_label": label}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", nargs="+", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--muse", default="RA-MD")
    ap.add_argument("--primary", default="RA-DET-G")
    ap.add_argument("--secondary", default="RA-DET")
    ap.add_argument("--budget", type=int)
    a = ap.parse_args(argv)
    try:
        result = analyse(load(a.runs), a.muse, a.primary, a.secondary, a.budget)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "detection_level.json").write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({"per_arm": result["per_arm"], "label": result["detection_level_label"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
