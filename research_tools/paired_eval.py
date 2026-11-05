"""EXPERIMENTAL: paired detection evaluation over the same cases, the same budgets and competing methods (arms).

    python research_tools/paired_eval.py --runs FILE.jsonl... --arms ARM_X ARM_Y --out DIR [--budget N]
    python research_tools/paired_eval.py --from-existing OUT.jsonl     # convert the repository's development outputs

Input: one JSON object per line, per *run*. Required fields (extra fields are kept, and missing ones are refused):

    case, arm, run_id                  strings
    schedules_used                     int >= 0: schedules this run consumed
    detected                           bool: an independently verified oracle violation on the buggy revision
                                       (corroborated, not refused)
    first_detection_schedule           int (1-based, within this run) when detected, else null
    wall_seconds                       number, or null when it wasn't recorded
    invalid_proposals, refused         int >= 0, and bool
    fixed_version_violation            bool: the same workload or schedule violated an oracle on the corrected revision
    tokens_in, tokens_out, est_usd     numbers (0 for deterministic arms)

Optional:
* ``schedule_budget``: the case×arm total schedule budget. It must be the same on every row of a case×arm. When
  absent, it is the sum of ``schedules_used``.
* ``case_kind``: ``defect`` (the default), ``injected_mutant``, or ``no_known_defect``. A ``no_known_defect`` case
  has no buggy revision. It enters the false-positive, invalidity and cost tables, never the detection analysis.
* ``seq``: the run order within a case×arm. The default is file order.

## Predeclared analysis (fixed in code before any new outcome is seen)

**Unit and pairing.** The unit is the *case*. Two arms are compared only on the cases where both ran with
**identical** ``schedule_budget``; a mismatch is refused, never silently truncated. ``--budget N`` truncates every
case×arm to its first N schedules, in run order, before pairing. That is a declared, symmetric choice.

**Schedules to first detection** (per case×arm): the runs are taken in order. T is the cumulative schedules used
before the first detecting run, plus that run's ``first_detection_schedule``. Without a detection, T is
right-censored at the budget.

**Seconds to first detection:** the cumulative ``wall_seconds`` through the first detecting run. This is an upper
bound, because the detecting run's own time is counted whole. It is null if any time involved was missing.

**Statistics.**
1. **Detection (binary per case, detected within budget):** an exact two-sided McNemar test on the discordant
   pairs (b: only X detects; c: only Y detects), p = min(1, 2·P[Bin(b+c, ½) ≤ min(b, c)]).
2. **Schedules to detection, censoring-aware:** a paired Gehan score per case.
   - +1 when X's T is *definitely* smaller: X is observed, and Y is censored, or observed and larger.
   - −1 when Y's T is definitely smaller.
   - 0 when the order is indeterminate: both censored, or tied.

   An exact two-sided sign test then runs on the non-zero scores. A sign-flip permutation test on ±1 scores is
   the same test, so a Wilcoxon on ranks is not used: ranks of censored values aren't defined without further
   assumptions.
3. **Descriptive:** the Kaplan–Meier median of T per arm across the paired cases, the detection rate, the
   fixed-version false positives, invalid and refused counts, tokens and estimated dollars.

**Verdict rule.**
- With fewer than ``MIN_CASES`` (10) paired defect cases, the verdict is **INSUFFICIENT SAMPLE**, whatever the
  p-values: there is no claim of advantage, matching RA-Q3's "descriptive unless ≥ 10 blind cases".
- Otherwise ``X better`` or ``Y better`` requires p < ``ALPHA`` (0.05) on the McNemar test, or on the Gehan sign
  test when McNemar has no discordant pairs. Anything else is ``NO DETECTED DIFFERENCE``.
- Fixed-version false positives are always reported next to the verdict. If both arms have them, the verdict
  carries a warning.

This module only reads run records. It runs nothing, calls no model, and never sees sealed labels: the records
carry outcomes, which the scorer produces after an authorized reveal.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

MIN_CASES = 10
ALPHA = 0.05
REQUIRED = {"case": str, "arm": str, "run_id": str, "schedules_used": int, "detected": bool,
            "first_detection_schedule": (int, type(None)), "wall_seconds": (int, float, type(None)),
            "invalid_proposals": int, "refused": bool, "fixed_version_violation": bool,
            "tokens_in": (int, float), "tokens_out": (int, float), "est_usd": (int, float)}
CASE_KINDS = ("defect", "injected_mutant", "no_known_defect")


class Refused(ValueError):
    """An input or comparison the predeclared analysis does not accept."""


# ------------------------------------------------------------------------------------------------ input

def validate(row: dict[str, Any], where: str = "") -> dict[str, Any]:
    missing = sorted(set(REQUIRED) - set(row))
    if missing:
        raise Refused(f"{where}: missing required fields {missing}")
    for k, t in REQUIRED.items():
        v = row[k]
        if isinstance(v, bool) and t in (int, (int, float)) or (t is int and isinstance(v, bool)):
            raise Refused(f"{where}: {k} must be a number, not a bool")
        if not isinstance(v, t):
            raise Refused(f"{where}: {k} has type {type(v).__name__}")
    if row["schedules_used"] < 0 or row["invalid_proposals"] < 0:
        raise Refused(f"{where}: negative count")
    if row["detected"] != (row["first_detection_schedule"] is not None):
        raise Refused(f"{where}: detected and first_detection_schedule disagree")
    if row["detected"] and not 1 <= row["first_detection_schedule"] <= row["schedules_used"]:
        raise Refused(f"{where}: first_detection_schedule outside 1..schedules_used")
    if row["detected"] and row["refused"]:
        raise Refused(f"{where}: a refused run cannot count as a verified detection")
    if row.get("case_kind", "defect") not in CASE_KINDS:
        raise Refused(f"{where}: unknown case_kind {row.get('case_kind')!r}")
    if row.get("case_kind") == "no_known_defect" and row["detected"]:
        raise Refused(f"{where}: a no_known_defect case has no buggy revision; record a violation as "
                      "fixed_version_violation")
    return row


def load(paths: list[Path]) -> list[dict[str, Any]]:
    rows, seen = [], set()
    for p in paths:
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if not line.strip():
                continue
            r = validate(json.loads(line), f"{p}:{i}")
            key = (r["case"], r["arm"], r["run_id"])
            if key in seen:
                raise Refused(f"{p}:{i}: duplicate run {key}")
            seen.add(key)
            r.setdefault("_order", len(rows))
            rows.append(r)
    return rows


# ------------------------------------------------------------------------------------------------ per case x arm

def summarize_cell(runs: list[dict[str, Any]], budget: int | None = None) -> dict[str, Any]:
    runs = sorted(runs, key=lambda r: (r.get("seq", r["_order"]), r["_order"]))
    budgets = {r.get("schedule_budget") for r in runs}
    if len(budgets) > 1:
        raise Refused(f"{runs[0]['case']}/{runs[0]['arm']}: rows declare different schedule_budget {budgets}")
    declared = budgets.pop()
    total = sum(r["schedules_used"] for r in runs)
    cell_budget = declared if declared is not None else total
    if total > cell_budget:
        raise Refused(f"{runs[0]['case']}/{runs[0]['arm']}: {total} schedules used exceed the budget {cell_budget}")
    if budget is not None:
        if budget > cell_budget:
            raise Refused(f"{runs[0]['case']}/{runs[0]['arm']}: --budget {budget} exceeds its budget {cell_budget}")
        cell_budget = budget
    used = 0
    secs: float | None = 0.0
    t = None
    t_secs = None
    within = []  # every run that starts inside the budget, whether or not a detection came earlier
    for r in runs:
        if used >= cell_budget:
            break
        take = min(r["schedules_used"], cell_budget - used)
        within.append(r)
        if r["detected"] and r["first_detection_schedule"] <= take and t is None:
            t = used + r["first_detection_schedule"]
            t_secs = None if secs is None or r["wall_seconds"] is None else secs + r["wall_seconds"]
        used += take
        secs = None if secs is None or r["wall_seconds"] is None else secs + r["wall_seconds"]
    # costs, invalidity, refusals and false positives count every run within the budget
    return {"case": runs[0]["case"], "arm": runs[0]["arm"], "case_kind": runs[0].get("case_kind", "defect"),
            "budget": cell_budget, "runs": len(within), "schedules_used": used,
            "detected": t is not None, "schedules_to_detection": t if t is not None else cell_budget,
            "censored": t is None, "seconds_to_detection_upper_bound": t_secs,
            "detecting_runs": sum(r["detected"] for r in within),
            "fixed_version_false_positives": sum(r["fixed_version_violation"] for r in within),
            "invalid_proposals": sum(r["invalid_proposals"] for r in within),
            "refused_runs": sum(r["refused"] for r in within),
            "tokens_in": sum(r["tokens_in"] for r in within), "tokens_out": sum(r["tokens_out"] for r in within),
            "est_usd": round(sum(r["est_usd"] for r in within), 6)}


def cells(rows: list[dict[str, Any]], budget: int | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[(r["case"], r["arm"])].append(r)
    kinds: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        kinds[r["case"]].add(r.get("case_kind", "defect"))
    for c, k in kinds.items():
        if len(k) > 1:
            raise Refused(f"case {c} is declared as several kinds {sorted(k)}")
    return {k: summarize_cell(v, budget) for k, v in sorted(by.items())}


# ------------------------------------------------------------------------------------------------ statistics

def binom_two_sided_sign(k: int, n: int) -> float:
    """Exact two-sided sign test: p = min(1, 2·P[Bin(n, ½) ≤ min(k, n−k)]). n = 0 gives 1."""
    if n == 0:
        return 1.0
    m = min(k, n - k)
    tail = sum(math.comb(n, i) for i in range(m + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def mcnemar_exact(b: int, c: int) -> float:
    return binom_two_sided_sign(b, b + c)


def gehan_score(x: dict[str, Any], y: dict[str, Any]) -> int:
    """+1 when x's schedules to detection is definitely smaller, −1 when y's is, 0 when indeterminate."""
    tx, cx, ty, cy = x["schedules_to_detection"], x["censored"], y["schedules_to_detection"], y["censored"]
    if not cx and (cy and tx <= ty or not cy and tx < ty):
        return 1
    if not cy and (cx and ty <= tx or not cx and ty < tx):
        return -1
    return 0


def km_median(times: list[tuple[int, bool]]) -> float | None:
    """Kaplan–Meier median of (time, censored) pairs: the smallest t with S(t) ≤ 0.5, or None when it is not reached."""
    s = 1.0
    at_risk = len(times)
    for t in sorted({t for t, _ in times}):
        events = sum(1 for tt, c in times if tt == t and not c)
        if at_risk and events:
            s *= 1 - events / at_risk
        if s <= 0.5:
            return float(t)
        at_risk -= sum(1 for tt, _ in times if tt == t)
    return None


def paired_table(rows: list[dict[str, Any]], x: str, y: str, budget: int | None = None
                 ) -> tuple[dict[tuple[str, str], dict[str, Any]], list[str]]:
    """The case×arm cells of arms x and y on the cases both ran; ``--budget`` applies only to these cells. Returns the
    cells and the cases that only one arm ran (excluded, and reported)."""
    if x == y:
        raise Refused("compare two different arms")
    cx = {r["case"] for r in rows if r["arm"] == x}
    cy = {r["case"] for r in rows if r["arm"] == y}
    if not cx or not cy:
        raise Refused(f"no runs for arm {x if not cx else y}")
    both = cx & cy
    return cells([r for r in rows if r["arm"] in (x, y) and r["case"] in both], budget), sorted((cx | cy) - both)


def compare(table: dict[tuple[str, str], dict[str, Any]], x: str, y: str,
            excluded: list[str] | None = None) -> dict[str, Any]:
    cases_x = {c for c, a in table if a == x}
    cases_y = {c for c, a in table if a == y}
    both = sorted(cases_x & cases_y)
    for c in both:
        if table[(c, x)]["budget"] != table[(c, y)]["budget"]:
            raise Refused(f"case {c}: {x} has budget {table[(c, x)]['budget']} but {y} has "
                          f"{table[(c, y)]['budget']}; compare only under identical budgets (or pass --budget)")
    defect = [c for c in both if table[(c, x)]["case_kind"] != "no_known_defect"]
    per_case = []
    b = c_ = 0
    scores = []
    for c in defect:
        cx, cy = table[(c, x)], table[(c, y)]
        b += cx["detected"] and not cy["detected"]
        c_ += cy["detected"] and not cx["detected"]
        g = gehan_score(cx, cy)
        scores.append(g)
        per_case.append({"case": c, "case_kind": cx["case_kind"], "budget": cx["budget"],
                         x: {k: cx[k] for k in ("detected", "schedules_to_detection", "censored")},
                         y: {k: cy[k] for k in ("detected", "schedules_to_detection", "censored")},
                         "gehan_score": g})
    pos, neg = scores.count(1), scores.count(-1)
    p_mcnemar = mcnemar_exact(b, c_)
    p_gehan = binom_two_sided_sign(pos, pos + neg)

    def arm_totals(arm: str) -> dict[str, Any]:
        cs = [table[(c, arm)] for c in both]
        dc = [table[(c, arm)] for c in defect]
        return {"cases": len(cs), "defect_cases": len(dc), "detected_cases": sum(v["detected"] for v in dc),
                "km_median_schedules_to_detection": km_median([(v["schedules_to_detection"], v["censored"])
                                                               for v in dc]),
                "fixed_version_false_positives": sum(v["fixed_version_false_positives"] for v in cs),
                "invalid_proposals": sum(v["invalid_proposals"] for v in cs),
                "refused_runs": sum(v["refused_runs"] for v in cs),
                "tokens_in": sum(v["tokens_in"] for v in cs), "tokens_out": sum(v["tokens_out"] for v in cs),
                "est_usd": round(sum(v["est_usd"] for v in cs), 6)}

    tx, ty = arm_totals(x), arm_totals(y)
    if len(defect) < MIN_CASES:
        verdict = f"INSUFFICIENT SAMPLE ({len(defect)} paired defect cases < {MIN_CASES}): descriptive only"
    else:
        p = p_mcnemar if b + c_ else p_gehan
        stat_b, stat_c = (b, c_) if b + c_ else (pos, neg)
        if p < ALPHA:
            verdict = f"{x} better" if stat_b > stat_c else f"{y} better"
        else:
            verdict = "NO DETECTED DIFFERENCE"
    warnings = []
    if tx["fixed_version_false_positives"] and ty["fixed_version_false_positives"]:
        warnings.append("both arms have fixed-version false positives")
    elif tx["fixed_version_false_positives"] or ty["fixed_version_false_positives"]:
        warnings.append("one arm has fixed-version false positives; read detections with them")
    excluded = sorted(set(excluded or []) | ((cases_x | cases_y) - set(both)))
    return {"arms": [x, y], "paired_cases": len(both), "paired_defect_cases": len(defect),
            "unpaired_cases_excluded": excluded, "mcnemar": {"b_only_" + x: b, "c_only_" + y: c_,
                                                             "p_exact_two_sided": p_mcnemar},
            "gehan_sign": {"x_definitely_faster": pos, "y_definitely_faster": neg,
                           "indeterminate": scores.count(0), "p_exact_two_sided": p_gehan},
            "totals": {x: tx, y: ty}, "per_case": per_case, "verdict": verdict, "warnings": warnings,
            "min_cases": MIN_CASES, "alpha": ALPHA}


# ------------------------------------------------------------------------------------------------ existing data

def from_existing(repo: Path) -> list[dict[str, Any]]:
    """Convert the repository's development outputs (all NONBLIND) to run records. Each record says where it came
    from. Seeds or workloads are the runs, in their recorded order."""
    d = repo / "docs/experimental_mvp"
    out: list[dict[str, Any]] = []

    def rec(**kw: Any) -> None:
        base = {"invalid_proposals": 0, "refused": False, "tokens_in": 0, "tokens_out": 0, "est_usd": 0.0,
                "nonblind": True}
        base.update(kw)
        out.append(validate(base, f"existing:{kw['case']}/{kw['arm']}/{kw['run_id']}"))

    def elapsed(run_dir: Path) -> float | None:
        f = run_dir / "coordinator.json"
        if not f.exists():
            return None
        ms = json.loads(f.read_text()).get("elapsed_ms")
        return None if ms is None else round(ms / 1000, 3)

    # 1. the historical Chancy case: seeded exploration and the forced schedule, buggy parent paired with the fix
    h = d / "historical/chancy_2895c4a/runs"
    for arm, bug, fix, budget in (("seeded_explore", "final_explore_git-80f570f", "final_explore_git-2895c4a", 10),
                                  ("forced_schedule_fix_informed", "final_forced_git-80f570f",
                                   "final_forced_git-2895c4a", 3)):
        b = json.loads((h / bug / "summary.json").read_text())
        f = {r["run"]: r for r in json.loads((h / fix / "summary.json").read_text())}
        for i, r in enumerate(b):
            ok = r["verdict"] == "SOUND" and r["corroboration"]["ok"]
            det = ok and r["case_verdict"] == "VIOLATED"
            fr = f.get(r["run"])
            rec(case="chancy-2895c4a", case_kind="defect", arm=arm, run_id=r["run"], seq=i, schedules_used=1,
                schedule_budget=budget, detected=det, first_detection_schedule=1 if det else None,
                wall_seconds=elapsed(h / bug / r["run"]), refused=not ok,
                fixed_version_violation=bool(fr and fr.get("case_verdict") == "VIOLATED"),
                source=f"{bug} vs {fix}")
    # 2. the injected mutant (no_skip_locked_claim), against the unmutated original on the same seeds
    res = d / "results"
    mut = json.loads((res / "simultaneous_mutant_compete_5/summary.json").read_text())
    orig = {r["run"]: r for r in json.loads((res / "simultaneous_explore_compete_4/summary.json").read_text())}
    for i, r in enumerate(mut):
        ok = r["verdict"] == "SOUND"
        det = ok and bool(r.get("violations"))
        o = orig.get(r["run"])
        rec(case="chancy-mutant-no_skip_locked_claim", case_kind="injected_mutant", arm="seeded_explore",
            run_id=r["run"], seq=i, schedules_used=1, schedule_budget=10, detected=det,
            first_detection_schedule=1 if det else None,
            wall_seconds=elapsed(res / "simultaneous_mutant_compete_5" / r["run"]), refused=not ok,
            fixed_version_violation=bool(o and o["verdict"] == "SOUND" and o.get("violations")),
            source="simultaneous_mutant_compete_5 vs simultaneous_explore_compete_4")
    # 3. no-known-defect workload comparisons: curated, systematic and Muse workloads, one schedule each
    def ledger_tokens(path: Path) -> tuple[int, int, float]:
        rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []
        return (sum(r.get("input_tokens", 0) for r in rows), sum(r.get("output_tokens", 0) for r in rows),
                round(sum(r.get("estimated_usd", 0) for r in rows), 6))

    proc = d / "rolling_upgrade/procrastinate_3.3_3.4/compare_1"
    s = json.loads((proc / "summary.json").read_text())
    ti, to, usd = ledger_tokens(proc / "muse_ledger.jsonl")
    for arm, rows in s["rows"].items():
        for i, r in enumerate(rows):
            first_muse = arm == "muse" and i == 0
            rec(case="procrastinate-3.3-3.4-blue-green", case_kind="no_known_defect", arm=arm,
                run_id=f"{arm}_{i}", seq=i, schedules_used=1, schedule_budget=s["k"], detected=False,
                first_detection_schedule=None, wall_seconds=None, refused=r["verdict"] != "SOUND",
                fixed_version_violation=bool(r.get("violations_after_errata")),
                invalid_proposals=(s["muse_requested"] - s["muse_valid"]) if first_muse else 0,
                tokens_in=ti if first_muse else 0, tokens_out=to if first_muse else 0,
                est_usd=usd if first_muse else 0.0, source="rolling_upgrade/procrastinate_3.3_3.4/compare_1")
    for case, sub in (("chancy-0.25.1-0.26.0-mixed", "real_app_pilot_chancy_compare_1"),
                      ("pgqueuer-1.6.0-lifecycle", "pgqueuer_compare_1")):
        rep = json.loads((res / sub / "report.json").read_text())
        ti, to, usd = ledger_tokens(res / sub / "muse_ledger.jsonl")
        k = rep["budget"]["workloads_per_source"]
        for src in rep["sources"]:
            for i, r in enumerate(src["runs"]):
                sound = r.get("sound", r.get("corroborated", False) and r.get("isolation_ok", True))
                first = i == 0
                rec(case=case, case_kind="no_known_defect", arm=src["source"], run_id=f"{src['source']}_{i}", seq=i,
                    schedules_used=1, schedule_budget=k, detected=False, first_detection_schedule=None,
                    wall_seconds=None, refused=not sound, fixed_version_violation=bool(r.get("oracle_violations")),
                    invalid_proposals=(src["proposals"] - src["valid"]) if first else 0,
                    tokens_in=ti if first and src["source"] == "muse" else 0,
                    tokens_out=to if first and src["source"] == "muse" else 0,
                    est_usd=usd if first and src["source"] == "muse" else 0.0,
                    source=f"results/{sub}", source_seconds=src.get("seconds"))
    for i, r in enumerate(out):
        r["_order"] = i
    return out


# ------------------------------------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", nargs="+", type=Path)
    ap.add_argument("--arms", nargs=2)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--budget", type=int)
    ap.add_argument("--from-existing", type=Path, help="write the repository's development outputs as run records")
    a = ap.parse_args(argv)
    try:
        if a.from_existing:
            rows = from_existing(Path(__file__).resolve().parents[1])
            a.from_existing.write_text("".join(json.dumps({k: v for k, v in r.items() if k != "_order"},
                                                          sort_keys=True) + "\n" for r in rows))
            print(f"wrote {len(rows)} run records to {a.from_existing}")
            if not a.runs:
                return 0
        if not (a.runs and a.arms and a.out):
            ap.error("--runs, --arms and --out are required")
        rows = load(a.runs)
        table, excluded = paired_table(rows, *a.arms, budget=a.budget)
        result = compare(table, *a.arms, excluded=excluded)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "cells.json").write_text(json.dumps([dict(v, key=list(k)) for k, v in table.items()], indent=1))
    (a.out / "comparison.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: result[k] for k in ("arms", "paired_cases", "paired_defect_cases", "verdict",
                                              "warnings")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
