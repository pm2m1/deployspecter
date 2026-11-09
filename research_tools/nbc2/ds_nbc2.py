"""ds-nbc/2: a prospective RA-NONBLIND exploratory comparison of three workload sources on the four ds-nbc/1 cases.

    ds_nbc2.py freeze  --out docs/experimental_mvp/nonblind_corpus/nbc2/MANIFEST.json
    ds_nbc2.py propose --manifest M --case CASE           # one Muse request; journaled before any run of CASE
    ds_nbc2.py run     --manifest M --case CASE --out DIR # all three arms, every slot, both revisions
    ds_nbc2.py analyze --manifest M --runs DIR ... --out DIR

**Arms.** These are not the frozen confirmatory RA-* arms, and their budgets are their own.
- **D1:** each case's existing developer-written, fix-informed workloads, `integration.workloads("RA-DET")` (3).
- **D2:** an outcome-blind generator (`d2_workloads`). It reads only the integration's `WORKLOAD_JSON_SCHEMA` and a
  seed derived from the case id. It never sees the fix, the oracle, any result or `MUSE_CONTEXT`'s prose. It emits
  `K` distinct workloads that the integration's `validate` accepts, and they are computed into the manifest at
  freeze time.
- **M:** one Muse request for `P` proposals, with the same frozen proposer, prompt and context as ds-nbc/1
  (`muse_workloads.Proposer`, `nbc-muse-workloads/1`). That context is the integration's `MUSE_CONTEXT` and schema.
  The valid proposals are admitted in model order, and the whole journal is written before any run of that case.

**Allocation (identical for every arm).**
- **Slots:** `K` = 6 workload slots per case per arm. Slot k runs workload `list[k % len(list)]`. A list shorter
  than K is cycled; an empty list makes every slot REFUSED ("no valid workload").
- **Schedules:** `S` = 2 per slot per revision, so 12 on R1 and 12 on R2 per arm and case.
- **Seeds:** `seed(case, k, j)`, the same for every arm.
- **No early stopping:** every slot and schedule runs on both revisions, so every admitted proposal is executed and
  utilization is complete.
- **Arm order per case:** a frozen permutation from sha256(case_id).

**Accounting.**
- Every run is SOUND, REFUSED, INCOMPLETE or ERROR, appended at once to a hash-chained ledger. None is retried.
- Invalid Muse proposals stay in the denominator.

**Outcomes per case and arm:**
- `detected`: any SOUND R1 run whose oracle reports VIOLATED;
- `false_positive`: any SOUND R2 run VIOLATED;
- `schedules_to_detection`: the 1-based position of the first violating R1 schedule in slot order, censored at 12;
- the seconds of R1 execution up to and including that schedule;
- utilization: distinct workloads executed, and those that violated at least once.

**Analysis.**
- **Primary:** M vs D2, paired by case, with `paired_eval`'s exact McNemar and Gehan sign test and the
  insufficient-sample rule (fewer than 10 cases).
- **Secondary:** D1 vs D2 and D1 vs M.
- **Sensitivity:** without saq-addee31 (its v2 encoding came after outcomes in ds-nbc/1).
- **Intervals:** exact Clopper–Pearson intervals throughout.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "research_tools" / "nbc"))
sys.path.insert(0, str(REPO / "research_tools"))
sys.path.insert(0, str(REPO / "src"))

VERSION = "ds-nbc2/1"
LABEL = "RA-NONBLIND-EXPLORATORY ds-nbc/2"
K, S, P, R = 6, 2, 6, 1
ARMS = ("D1", "D2", "M")
CASES = {  # case_id -> case spec (unchanged ds-nbc/1 specs; saq-addee31 uses its v2 spec, as admitted in ds-nbc/1)
    "chancy-2895c4a": "docs/experimental_mvp/nonblind_corpus/chancy/cases/chancy-2895c4a/case_spec.json",
    "pgqueuer-8dfadf4": "docs/experimental_mvp/nonblind_corpus/pgqueuer/cases/pgqueuer-8dfadf4/case_spec.json",
    "saq-c570ba4": "docs/experimental_mvp/nonblind_corpus/saq/cases/saq-c570ba4/case_spec.json",
    "saq-addee31": "docs/experimental_mvp/nonblind_corpus/saq/cases/saq-addee31/case_spec_v2.json",
}
SENSITIVITY_EXCLUDE = ["saq-addee31"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def wsha(w: Any) -> str:
    return hashlib.sha256(json.dumps(w, sort_keys=True).encode()).hexdigest()


def seed(case: str, k: int, j: int) -> int:
    return int(hashlib.sha256(f"ds-nbc/2|{case}|{k}|{j}".encode()).hexdigest()[:8], 16)


def arm_order(case: str) -> list[str]:
    perms = list(itertools.permutations(ARMS))
    return list(perms[hashlib.sha256(f"ds-nbc/2|order|{case}".encode()).digest()[0] % len(perms)])


def load_integration(path: Path) -> Any:
    sys.path.insert(0, str(path.parent))
    s = importlib.util.spec_from_file_location(f"nbc2_integ_{sha(path)[:12]}", path)
    m = importlib.util.module_from_spec(s)
    assert s.loader is not None
    sys.modules[s.name] = m
    s.loader.exec_module(m)
    return m


def spec_of(case: str) -> dict[str, Any]:
    return json.loads((REPO / CASES[case]).read_text())


# ------------------------------------------------------------------------------------------------ D2 (outcome-blind)

def _sample(schema: dict[str, Any], rnd: random.Random) -> Any:
    """One value drawn from exactly what the schema declares. Numeric bounds are hit with probability 1/2 (boundary
    emphasis), enums uniformly, array lengths uniformly in [minItems, maxItems]."""
    if "enum" in schema:
        return rnd.choice(schema["enum"])
    t = schema.get("type")
    if t == "object":
        return {k: _sample(v, rnd) for k, v in schema["properties"].items()}
    if t == "array":
        n = rnd.randint(schema.get("minItems", 0), schema.get("maxItems", 3))
        return [_sample(schema["items"], rnd) for _ in range(n)]
    if t == "boolean":
        return rnd.random() < 0.5
    if t == "integer":
        lo, hi = schema["minimum"], schema["maximum"]
        return rnd.choice([lo, hi]) if rnd.random() < 0.5 else rnd.randint(lo, hi)
    raise ValueError(f"D2 can't sample schema type {t!r}")


def d2_workloads(integration: Any, case: str, k: int = K, max_draws: int = 100_000) -> dict[str, Any]:
    rnd = random.Random(int(hashlib.sha256(f"ds-nbc/2|D2|{case}".encode()).hexdigest()[:16], 16))
    out, seen, draws, rejected = [], set(), 0, 0
    while len(out) < k and draws < max_draws:
        draws += 1
        w = _sample(integration.WORKLOAD_JSON_SCHEMA, rnd)
        if integration.validate(w):
            rejected += 1
            continue
        h = wsha(w)
        if h not in seen:
            seen.add(h)
            out.append(w)
    return {"workloads": out, "draws": draws, "rejected_by_validate": rejected}


# ------------------------------------------------------------------------------------------------ manifest

def manifest() -> dict[str, Any]:
    import muse_workloads as mw
    cases = {}
    for c, sp in CASES.items():
        s = spec_of(c)
        integ = load_integration(REPO / s["integration"])
        cases[c] = {"spec": sp, "spec_sha256": sha(REPO / sp), "integration": s["integration"],
                    "integration_sha256": sha(REPO / s["integration"]), "oracle_sha256": s["oracle"]["sha256"],
                    "R1_sha256": s["R1"]["sha256"], "R2_sha256": s["R2"]["sha256"],
                    "buggy": s.get("buggy", "R1"),
                    "D1": integ.workloads("RA-DET"), "D2": d2_workloads(integ, c),
                    "muse_prompt_sha256": mw.prompt_sha256(integ, P), "arm_order": arm_order(c)}
    files = ["research_tools/nbc2/ds_nbc2.py", "research_tools/nbc/muse_workloads.py", "research_tools/nbc/budget.py",
             "research_tools/paired_eval.py", "research_tools/nbc/track_b.py"]
    return {
        "experiment": VERSION, "label": LABEL,
        "status": "FROZEN before any ds-nbc/2 Muse request or run",
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "relation_to_ds_nbc1": "a separate experiment; ds-nbc/1's result (RA-DET 4/4 vs RA-MD 1/4) stays its primary "
                               "result and is not replaced; ds-nbc/1 Muse proposals are not reused",
        "arms": {"D1": "developer-written, fix-informed workloads (each integration's RA-DET list, 3 per case)",
                 "D2": "outcome-blind schema generator: WORKLOAD_JSON_SCHEMA only, seeded by case id, K distinct "
                       "valid workloads, computed here at freeze time",
                 "M": f"one Muse request (R={R}) for P={P} proposals with the frozen nbc-muse-workloads/1 proposer "
                      "(MUSE_CONTEXT plus schema); valid proposals admitted in model order"},
        "d2_disclosure": "the generator was written before any of its output was seen; its output for the four cases "
                         "was printed once during development before this freeze, and the generator, seeds and K were "
                         "not changed afterwards",
        "information": "D2 and M see no fix details, oracle, triggering schedule or result; D2 sees less than M (the "
                       "schema only); D1 was written with knowledge of each fix",
        "allocation": {"K_slots_per_arm": K, "S_schedules_per_slot_per_revision": S,
                       "schedules_per_arm_per_revision": K * S,
                       "slot_rule": "slot k runs list[k % len(list)]; an empty list makes every slot REFUSED",
                       "early_stopping": "none: every slot and schedule runs on R1 and on R2",
                       "seed": "int(sha256('ds-nbc/2|case|k|j')[:8], 16), identical across arms",
                       "arm_order": "a permutation of (D1, D2, M) from sha256('ds-nbc/2|order|case')[0] % 6"},
        "admission": {"M": "valid proposals in model order, all admitted (P=6, at most 6); invalid ones counted; no "
                           "substitution or reordering after outcomes", "D1": "all 3", "D2": "all 6"},
        "accounting": "SOUND / REFUSED / INCOMPLETE / ERROR per run, hash-chained, never retried; wall seconds per run",
        "outcomes": "detected, false positive, schedules to first detection (censored at 12), R1 seconds to "
                    "detection, utilization (distinct workloads executed, workloads that violated)",
        "analysis": {"primary": "M vs D2 (paired_eval: exact McNemar, Gehan sign test; INSUFFICIENT SAMPLE below "
                                "10 cases)", "secondary": ["D1 vs D2", "D1 vs M"],
                     "sensitivity": f"excluding {SENSITIVITY_EXCLUDE} (post-hoc encoding in ds-nbc/1)",
                     "uncertainty": "exact Clopper-Pearson 95% intervals", "unit": "the historical case",
                     "post_hoc": "anything else is labelled POST-HOC"},
        "model": {"proposer": mw.VERSION, "model": "MODEL_NAME from the .env file or DEFAULT_MODEL (muse-spark-1.3), "
                                                    "recorded per call", "reasoning_effort": "medium", "store": False,
                  "max_output_tokens": mw.MAX_OUTPUT_TOKENS, "requests_per_case": R, "proposals_per_request": P},
        "cost": {"cap_usd": 1.00, "ledger": "docs/experimental_mvp/nonblind_corpus/nbc2/META_LEDGER_nbc2.jsonl "
                                            "(shared with onb-pilot/3), DS_NBC_META_CAP_USD=1.00"},
        "cases": cases,
        "code_sha256": {f: sha(REPO / f) for f in files},
    }


# ------------------------------------------------------------------------------------------------ ledger

class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def entries(self) -> list[dict[str, Any]]:
        return [json.loads(x) for x in self.path.read_text().splitlines()] if self.path.exists() else []

    def append(self, rec: dict[str, Any]) -> None:
        es = self.entries()
        e = {"seq": len(es), "prev": es[-1]["hash"] if es else None, "label": LABEL, "run": rec,
             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        e["hash"] = hashlib.sha256(json.dumps(e, sort_keys=True, default=str).encode()).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(e, sort_keys=True, default=str) + "\n")

    def verify(self) -> list[str]:
        p, prev = [], None
        for i, e in enumerate(self.entries()):
            h = e.pop("hash")
            if e.get("prev") != prev or e.get("seq") != i:
                p.append(f"entry {i}: chain broken")
            if hashlib.sha256(json.dumps(e, sort_keys=True, default=str).encode()).hexdigest() != h:
                p.append(f"entry {i}: hash mismatch")
            prev = h
        return p


def journal_path(m: Path, case: str) -> Path:
    return m.parent / "proposals" / f"{case}.json"


def cmd_propose(o: argparse.Namespace) -> int:
    import muse_workloads as mw
    man = json.loads(o.manifest.read_text())
    c = man["cases"][o.case]
    j = journal_path(o.manifest, o.case)
    if j.exists():
        raise SystemExit("refused: this case's Muse proposals are already journaled (never asked twice)")
    if (o.manifest.parent / "runs" / o.case).exists():
        raise SystemExit("refused: a run of this case already exists; M must be journaled before any run")
    integ = load_integration(REPO / c["integration"])
    if sha(REPO / c["integration"]) != c["integration_sha256"]:
        raise SystemExit("refused: the integration changed since the freeze")
    prop = mw.Proposer(integ, j.parent / "muse" / o.case, p=P, env_file=o.env_file)
    error = None
    try:
        text, usage = prop({"case_id": o.case}, "M", 0)
    except ConnectionError as exc:  # R = 1: a failed request is final, never retried; M then has no workload
        text, usage, error = "", {"tokens_in": 0, "tokens_out": 0, "est_usd": None}, str(exc)[:300]
    try:
        raw = json.loads(text)
    except ValueError:
        raw = None
    items = raw if isinstance(raw, list) else []
    verdicts = [{"index": i, "workload": w, "problems": integ.validate(w)} for i, w in enumerate(items)]
    admitted = [v["workload"] for v in verdicts if not v["problems"]]
    j.parent.mkdir(parents=True, exist_ok=True)
    j.write_text(json.dumps({"experiment": VERSION, "case_id": o.case, "proposer": mw.VERSION,
                             "parse_ok": isinstance(raw, list), "proposals": verdicts, "admitted": admitted,
                             "invalid": len(verdicts) - len(admitted) + (0 if isinstance(raw, list) else 1),
                             "usage": usage, "request_error": error, "manifest_sha256": sha(o.manifest),
                             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, indent=1, sort_keys=True)
                 + "\n")
    print(json.dumps({"admitted": len(admitted), "proposals": len(verdicts), "usage": usage}))
    return 0


def cmd_run(o: argparse.Namespace) -> int:
    man = json.loads(o.manifest.read_text())
    c = man["cases"][o.case]
    s = spec_of(o.case)
    for f, want in ((REPO / c["spec"], c["spec_sha256"]), (REPO / c["integration"], c["integration_sha256"]),
                    (REPO / s["oracle"]["path"], c["oracle_sha256"])):
        if sha(f) != want:
            raise SystemExit(f"refused: {f} changed since the freeze")
    j = journal_path(o.manifest, o.case)
    if not j.exists():
        raise SystemExit("refused: run `propose` first; M's proposals must be journaled before any run")
    lists = {"D1": c["D1"], "D2": c["D2"]["workloads"], "M": json.loads(j.read_text())["admitted"]}
    integ = load_integration(REPO / c["integration"])
    ledger = Ledger(o.out / "RESULTS_LEDGER.jsonl")
    if why := ledger.verify():
        raise SystemExit("refused: the ledger is broken: " + "; ".join(why))
    done = {(e["run"]["arm"], e["run"]["k"], e["run"]["j"], e["run"]["slot"]) for e in ledger.entries()}
    oracle = REPO / s["oracle"]["path"]
    handles: dict[str, Any] = {}
    (o.out / "work").mkdir(parents=True, exist_ok=True)
    for arm in c["arm_order"]:
        wl = lists[arm]
        for k in range(K):
            w = wl[k % len(wl)] if wl else None
            for jj in range(1, S + 1):
                for slot in ("R1", "R2"):
                    if (arm, k, jj, slot) in done:
                        continue
                    rec: dict[str, Any] = {"case_id": o.case, "arm": arm, "k": k, "j": jj, "slot": slot,
                                           "seed": seed(o.case, k, jj), "workload": w,
                                           "workload_sha256": wsha(w) if w is not None else None}
                    if w is None:
                        rec.update(verdict="REFUSED", reason="no valid workload for this arm", wall_seconds=0.0)
                        ledger.append(rec)
                        continue
                    if slot not in handles:
                        handles[slot] = integ.prepare(Path(s[slot]["archive"]), s[slot]["sha256"],
                                                      o.out / "work" / "build")
                        (o.out / f"HANDLE_{slot}.json").write_text(json.dumps(handles[slot], default=str) + "\n")
                    out = o.out / "work" / "runs" / f"{arm}__k{k}__j{jj}__{slot}"
                    t0 = time.monotonic()
                    try:
                        r = integ.run(handles[slot], w, rec["seed"], out, oracle_code=oracle,
                                      oracle_sha256=c["oracle_sha256"])
                    except Exception as exc:  # noqa: BLE001 - recorded as a final ERROR, never retried
                        r = {"verdict": "ERROR", "reason": f"{type(exc).__name__}: {str(exc)[:500]}"}
                    rec["wall_seconds"] = round(time.monotonic() - t0, 3)
                    rec.update({k2: r.get(k2) for k2 in ("verdict", "oracle", "corroboration", "reason")})
                    ledger.append(rec)
    print(json.dumps({"case": o.case, "entries": len(ledger.entries())}))
    return 0


# ------------------------------------------------------------------------------------------------ analysis

def cell(runs: list[dict[str, Any]], case: str, arm: str, buggy: str) -> dict[str, Any]:
    rs = sorted([r for r in runs if r["case_id"] == case and r["arm"] == arm], key=lambda r: (r["k"], r["j"]))
    b = [r for r in rs if r["slot"] == buggy]
    f = [r for r in rs if r["slot"] != buggy]
    first, secs, pos = None, 0.0, 0
    for r in b:
        pos += 1
        secs += r.get("wall_seconds") or 0.0
        if r["verdict"] == "SOUND" and r.get("oracle") == "VIOLATED":
            first = (pos, round(secs, 1))
            break
    cnt = lambda rs2, v: sum(1 for r in rs2 if r["verdict"] == v)  # noqa: E731
    trig = {r["workload_sha256"] for r in b if r["verdict"] == "SOUND" and r.get("oracle") == "VIOLATED"}
    corr = [r.get("corroboration") or {} for r in rs if r["verdict"] == "SOUND"]
    return {"case": case, "arm": arm, "detected": first is not None,
            "schedules_to_detection": first[0] if first else None, "censored": first is None,
            "seconds_to_detection": first[1] if first else None,
            "false_positive": any(r["verdict"] == "SOUND" and r.get("oracle") == "VIOLATED" for r in f),
            "R1": {v: cnt(b, v) for v in ("SOUND", "REFUSED", "INCOMPLETE", "ERROR")},
            "R2": {v: cnt(f, v) for v in ("SOUND", "REFUSED", "INCOMPLETE", "ERROR")},
            "R1_seconds_total": round(sum(r.get("wall_seconds") or 0 for r in b), 1),
            "distinct_workloads_executed": len({r["workload_sha256"] for r in b if r["workload_sha256"]}),
            "workloads_that_violated": len(trig),
            "params_compared": sum(x.get("params_compared") or 0 for x in corr),
            "params_mismatched": sum(x.get("params_mismatched") or 0 for x in corr),
            "params_unchecked": sum(x.get("params_unchecked") or 0 for x in corr),
            "statements_matched": sum(x.get("matched") or 0 for x in corr)}


def paired_rows(runs: list[dict[str, Any]], buggy: dict[str, str], usage: dict[str, dict[str, Any]],
                invalid: dict[str, int]) -> list[dict[str, Any]]:
    """paired_eval run records: one per (case, arm, slot k) on the buggy revision, in slot order."""
    rows = []
    for (case, arm, k) in sorted({(r["case_id"], r["arm"], r["k"]) for r in runs}):
        b = sorted([r for r in runs if (r["case_id"], r["arm"], r["k"]) == (case, arm, k)
                    and r["slot"] == buggy[case]], key=lambda r: r["j"])
        f = [r for r in runs if (r["case_id"], r["arm"], r["k"]) == (case, arm, k) and r["slot"] != buggy[case]]
        hit = next((r["j"] for r in b if r["verdict"] == "SOUND" and r.get("oracle") == "VIOLATED"), None)
        u = usage.get(case, {}) if arm == "M" and k == 0 else {}
        rows.append({"case": case, "arm": arm, "run_id": f"k{k}", "seq": k, "schedules_used": len(b),
                     "detected": hit is not None, "first_detection_schedule": hit,
                     "wall_seconds": round(sum(r.get("wall_seconds") or 0 for r in b), 3),
                     "invalid_proposals": invalid.get(case, 0) if arm == "M" and k == 0 else 0,
                     "refused": any(r["verdict"] != "SOUND" for r in b),
                     "fixed_version_violation": any(r["verdict"] == "SOUND" and r.get("oracle") == "VIOLATED"
                                                    for r in f),
                     "tokens_in": u.get("tokens_in", 0), "tokens_out": u.get("tokens_out", 0),
                     "est_usd": u.get("est_usd", 0), "schedule_budget": K * S})
    return rows


def cmd_analyze(o: argparse.Namespace) -> int:
    import paired_eval as pe
    import track_b
    man = json.loads(o.manifest.read_text())
    runs = []
    for d in o.runs:
        lg = Ledger(d / "RESULTS_LEDGER.jsonl")
        if why := lg.verify():
            raise SystemExit(f"refused: {d}: " + "; ".join(why))
        runs += [e["run"] for e in lg.entries()]
    buggy = {c: v["buggy"] for c, v in man["cases"].items()}
    usage, invalid = {}, {}
    for c in man["cases"]:
        jp = journal_path(o.manifest, c)
        if jp.exists():
            jj = json.loads(jp.read_text())
            usage[c], invalid[c] = jj["usage"], jj["invalid"]
    o.out.mkdir(parents=True, exist_ok=True)
    cases = sorted({r["case_id"] for r in runs})
    cells = [cell(runs, c, a, buggy[c]) for c in cases for a in ARMS]
    res: dict[str, Any] = {"label": LABEL, "analyzer": VERSION, "manifest_sha256": sha(o.manifest), "cells": cells,
                           "comparisons": {}}
    for scope, keep in (("all", cases), ("sensitivity_without_" + "_".join(SENSITIVITY_EXCLUDE),
                                         [c for c in cases if c not in SENSITIVITY_EXCLUDE])):
        rows = [r for r in paired_rows(runs, buggy, usage, invalid) if r["case"] in keep]
        p = o.out / f"paired_runs_{scope}.jsonl"
        p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
        loaded = pe.load([p])
        res["comparisons"][scope] = {}
        for x, y in (("M", "D2"), ("D1", "D2"), ("D1", "M")):
            table, excluded = pe.paired_table(loaded, x, y)
            cmp = pe.compare(table, x, y, excluded)
            n = len({c for (c, _a) in table})
            cmp["detection_ci95"] = {a: {"k": sum(1 for (c, a2), v in table.items() if a2 == a and v["detected"]),
                                         "n": n} for a in (x, y)}
            for a in (x, y):
                d = cmp["detection_ci95"][a]
                d["ci"] = track_b.clopper_pearson(d["k"], d["n"])
            res["comparisons"][scope][f"{x}_vs_{y}"] = cmp
    (o.out / "ANALYSIS.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    for scope, comps in res["comparisons"].items():
        for name, cmp in comps.items():
            print(scope, name, cmp["verdict"], cmp["mcnemar"], cmp["detection_ci95"])
    return 0


def main() -> int:
    a = argparse.ArgumentParser()
    sub = a.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--out", type=Path, required=True)
    pr = sub.add_parser("propose")
    pr.add_argument("--manifest", type=Path, required=True)
    pr.add_argument("--case", required=True)
    pr.add_argument("--env-file", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("--manifest", type=Path, required=True)
    r.add_argument("--case", required=True)
    r.add_argument("--out", type=Path, required=True)
    an = sub.add_parser("analyze")
    an.add_argument("--manifest", type=Path, required=True)
    an.add_argument("--runs", nargs="+", type=Path, required=True)
    an.add_argument("--out", type=Path, required=True)
    o = a.parse_args()
    if o.cmd == "freeze":
        if o.out.exists():
            raise SystemExit("refused: the manifest is already frozen")
        o.out.parent.mkdir(parents=True, exist_ok=True)
        o.out.write_text(json.dumps(manifest(), indent=1, sort_keys=True) + "\n")
        print(o.out, sha(o.out))
        return 0
    return {"propose": cmd_propose, "run": cmd_run, "analyze": cmd_analyze}[o.cmd](o)


if __name__ == "__main__":
    raise SystemExit(main())
