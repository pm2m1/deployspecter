"""EXPERIMENTAL: the frozen analysis of the Muse-contribution experiment (``experiment.py analyze``).

Inputs: the frozen manifest, the generation directory and the run directory (``reservations_<part>``,
``chancy_<part>``, ``chancy_units``, ``procrastinate_<arm>``). Outputs, in ``--out``:

* ``results.jsonl``: one row per (case, arm, replicate), in the schema of ``RESULTS_SCHEMA.md``;
* ``results_workloads.jsonl``: one row per executed workload;
* ``invariants.json``: every candidate's eight checks and class, per domain, and the mutation score;
* ``summary.json``: the per-case tables and Q1-Q4, by the manifest's analysis rules. Descriptive only.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from deployspecter.experimental.invariants import validator as iv
from deployspecter.experimental.muse_contrib import observe as ob

RES_TARGETS = {"buggy-vs-corrected": "buggy", "fixed-vs-corrected": "fixed"}


def jl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def _rid(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _usage(gen: Path, labels: list[str]) -> dict[str, Any]:
    out = {"tokens_in": 0, "tokens_out": 0, "est_usd": 0.0}
    for e in jl(gen / "calls.jsonl"):
        if e.get("kind") == "call" and e.get("call") in labels:
            out["tokens_in"] += e.get("input_tokens") or 0
            out["tokens_out"] += e.get("output_tokens") or 0
            out["est_usd"] = round(out["est_usd"] + (e.get("estimated_usd") or 0.0), 6)
    return out


def _invalid(gen: Path, label: str) -> int:
    f = gen / f"{label}.validated.json"
    return sum(not r["valid"] for r in json.loads(f.read_text())) if f.exists() else 0


def _call_label(arm: str, case: str, replicate: int, group: str | None = None) -> list[str]:
    if arm == "B3":
        return {"chancy-2895c4a": [f"mc_chancy_w_r{replicate}"], "procrastinate-3.3-3.4": [f"mc_procr_w_r{replicate}"]
                }.get(case, [f"mc_res_w_r{replicate}"])
    if arm == "B5":
        return [f"mc_state_{group}_r{replicate}"]
    return []


# ------------------------------------------------------------------------------------------------ reservations

def reservations_rows(m: dict[str, Any], gen: Path, run: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    method, workloads = [], []
    for part in ("B1", "B2", "B3", "B4", "B5"):
        recs = jl(run / f"reservations_{part}" / "runs.jsonl")
        groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for r in recs:
            groups.setdefault((r["replicate"], r["group"]), []).append(r)
        for (rep, group), rs in sorted(groups.items()):
            for tname, build in RES_TARGETS.items():
                case = (f"reservations/{tname}" if part in ("B1", "B2", "B3")
                        else f"reservations-state:{group}/{tname}")
                row = _fold_reservations(rs, build, case, part, rep)
                labels = _call_label(part, case, rep, group)
                row["invalid_proposals"] = (sum(_invalid(gen, x) for x in labels) if labels
                                            else sum(r["plan"] != "bridged" for r in rs))
                row.update(_usage(gen, labels))
                row["run_id"] = _rid(m["frozen_at"], case, part, rep)
                method.append(row)
                for r in rs:
                    workloads.append({"case": case, "arm": part, "replicate": rep, "label": r["label"],
                                      **_fold_reservations([r], build, case, part, rep, workload_level=True)})
    return method, workloads


def _fold_reservations(rs: list[dict[str, Any]], build: str, case: str, arm: str, rep: int, *,
                       workload_level: bool = False) -> dict[str, Any]:
    used, first, seconds, to_first, refused, fvv, executed = 0, None, 0.0, None, 0, False, 0
    for r in rs:
        seconds += r.get("seconds") or 0.0
        if r.get("construction") != "verified":
            refused += 1
            continue
        executed += 1
        b = r["builds"].get(build, {})
        if "refused" in b:
            refused += 1
        for x in b.get("runs", []):
            used += 1
            if first is None and x.get("sound") and x["violations"]:
                first, to_first = used, round(seconds, 3)
        ref = r["builds"].get("corrected", {})
        if "refused" in ref:
            refused += 1
        fvv |= any(x.get("sound") and x["violations"] for x in ref.get("runs", []))
    row = {"case": case, "arm": arm, "replicate": rep, "schedules_used": used, "detected": first is not None,
           "first_detection_schedule": first, "wall_seconds": round(seconds, 3),
           "wall_seconds_to_first_detection": to_first, "refused": refused, "fixed_version_violation": fvv,
           "workloads_executed": executed, "nonblind": True, "known_defect": True}
    if workload_level:
        row.pop("nonblind")
    return row


# ------------------------------------------------------------------------------------------------ chancy

def chancy_rows(m: dict[str, Any], gen: Path, run: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    method, workloads = [], []
    for part in ("B1", "B2", "B3"):
        units = jl(run / f"chancy_{part}" / "units.jsonl")
        for rep in sorted({u["replicate"] for u in units}):
            us = [u for u in units if u["replicate"] == rep]
            row = _fold_chancy(us)
            labels = _call_label(part, "chancy-2895c4a", rep)
            row.update(case="chancy-2895c4a", arm=part, replicate=rep, run_id=_rid(m["frozen_at"], "chancy", part, rep),
                       invalid_proposals=sum(_invalid(gen, x) for x in labels), nonblind=True, known_defect=True,
                       **_usage(gen, labels))
            method.append(row)
            for label in dict.fromkeys(u["label"] for u in us):
                workloads.append({"case": "chancy-2895c4a", "arm": part, "replicate": rep, "label": label,
                                  "workload": next(u["workload"] for u in us if u["label"] == label),
                                  **_fold_chancy([u for u in us if u["label"] == label])})
    return method, workloads


def _fold_chancy(us: list[dict[str, Any]]) -> dict[str, Any]:
    used, first, seconds, to_first, refused, nov, fvv = 0, None, 0.0, None, 0, 0, False
    for u in us:
        seconds += u.get("seconds") or 0.0
        if u["verdict"] != "SOUND":
            refused += 1
        if u["role"] == "defective":
            used += 1
            if u["verdict"] == "SOUND" and u["case_verdict"] == "NO_VERDICT":
                nov += 1
            if first is None and u["verdict"] == "SOUND" and u["case_verdict"] == "VIOLATED":
                first, to_first = used, round(seconds, 2)
        elif u["role"] == "reference" and u["verdict"] == "SOUND" and u["case_verdict"] == "VIOLATED":
            fvv = True
    return {"schedules_used": used, "detected": first is not None, "first_detection_schedule": first,
            "wall_seconds": round(seconds, 2), "wall_seconds_to_first_detection": to_first, "refused": refused,
            "no_verdict_runs": nov, "fixed_version_violation": fvv,
            "workloads_executed": len({u["label"] for u in us})}


# ------------------------------------------------------------------------------------------------ procrastinate

def procrastinate_rows(m: dict[str, Any], gen: Path, run: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    method, workloads = [], []
    for arm in ("B1", "B2", "B3"):
        rs = jl(run / f"procrastinate_{arm}" / "runs.jsonl")
        if not rs:
            continue
        labels = _call_label(arm, "procrastinate-3.3-3.4", 0)
        fvv = any(r.get("violations_after_errata") for r in rs)
        method.append({"case": "procrastinate-3.3-3.4", "arm": arm, "replicate": 0,
                       "run_id": _rid(m["frozen_at"], "procrastinate", arm), "schedules_used": len(rs),
                       "detected": False, "first_detection_schedule": None,
                       "wall_seconds": round(sum(r["seconds"] for r in rs), 2), "wall_seconds_to_first_detection": None,
                       "invalid_proposals": sum(_invalid(gen, x) for x in labels),
                       "refused": sum(r.get("verdict") != "SOUND" for r in rs), "fixed_version_violation": fvv,
                       "workloads_executed": len(rs), "nonblind": True, "known_defect": False, **_usage(gen, labels)})
        for r in rs:
            workloads.append({"case": "procrastinate-3.3-3.4", "arm": arm, "replicate": 0, "label": r["label"],
                              "workload": r["workload"], "verdict": r.get("verdict"), "exit": r.get("exit"),
                              "phases_sound": r.get("phases_sound"), "coverage": r.get("coverage"),
                              "violations_after_errata": r.get("violations_after_errata"),
                              "unchecked": r.get("unchecked"), "mismatched": r.get("mismatched"),
                              "seconds": r["seconds"]})
    return method, workloads


# ------------------------------------------------------------------------------------------------ comparisons

def compare(rows: list[dict[str, Any]], muse_arm: str, det_arms: list[str]) -> dict[str, Any]:
    """The manifest's Q1/Q2 rule for one case."""
    det = [r for r in rows if r["arm"] in det_arms]
    mu = [r for r in rows if r["arm"] == muse_arm]
    if not mu or not det:
        return {"verdict": "not answerable", "why": "an arm has no rows"}
    det_detects = any(r["detected"] for r in det)
    mu_all = all(r["detected"] for r in mu)
    mu_any = any(r["detected"] for r in mu)
    base = {"muse": [(r["replicate"], r["detected"], r["first_detection_schedule"]) for r in mu],
            "deterministic": [(r["arm"], r["detected"], r["first_detection_schedule"]) for r in det]}
    if not det_detects and not mu_any:
        return {"verdict": "matched", "why": "no arm detected (no defect detected in this case)", **base}
    if not det_detects and mu_any:
        return {"verdict": "improved" if mu_all else "mixed", "why": "Muse detected where no deterministic arm did",
                **base}
    if det_detects and not mu_all:
        return {"verdict": "underperformed", "why": "a Muse replicate failed to detect where a deterministic arm did",
                **base}
    best = min(r["first_detection_schedule"] for r in det if r["detected"])
    worst_det = max(r["first_detection_schedule"] for r in det if r["detected"])
    ms = [r["first_detection_schedule"] for r in mu]
    if all(x < best for x in ms):
        v = "improved"
    elif all(x > worst_det for x in ms):
        v = "underperformed"
    else:
        v = "matched"
    return {"verdict": v, "why": f"schedules to first detection: Muse {ms}, deterministic best {best}", **base}


# ------------------------------------------------------------------------------------------------ invariants

def reservations_observations(run: Path) -> list[dict[str, Any]]:
    seen, out = set(), []
    for part in ("B1", "B2", "B3", "B4", "B5", "mutants"):
        for o in jl(run / f"reservations_{part}" / "observations.jsonl"):
            if o["obs_id"] not in seen:
                seen.add(o["obs_id"])
                out.append(o)
    return out


def chancy_observations(run: Path) -> list[dict[str, Any]]:
    out = []
    for d in sorted((run / "chancy_units").glob("*")):
        f = d / "report.json"
        if not f.exists():
            continue
        rep = json.loads(f.read_text())
        if "run" in rep and isinstance(rep["run"], dict) and "coordinator" not in rep["run"]:
            cj = d / "coordinator.json.gz"
            if cj.exists():
                rep["run"]["coordinator"] = json.loads(gzip.open(cj).read())
        src, mu = rep.get("source"), rep.get("mutant")
        role = "mutant" if mu else ("defective" if src == "chancy-git-80f570f.tar" else "reference")
        build = f"mutant_{mu}" if mu else src
        out.append(ob.chancy_observation(d.name, build, role, mu, rep))
    return out


def invariant_candidates(m: dict[str, Any], gen: Path, domain: str, label: str) -> list[tuple[str, dict[str, Any]]]:
    det = [("deterministic" if not h["id"].startswith("X-") else "control", h)
           for h in m["deterministic_inputs"]["invariant_candidates"][domain]]
    f = gen / f"{label}.validated.json"
    muse = []
    for r in (json.loads(f.read_text()) if f.exists() else []):
        h = dict(r["hypothesis"]) if isinstance(r["hypothesis"], dict) else {"id": r["label"]}
        h["id"] = f"{r['label']}:{h.get('id')}"
        muse.append(("muse", h))
    return det + muse


def invariants(m: dict[str, Any], gen: Path, run: Path) -> dict[str, Any]:
    import chancy_runner as cr  # noqa: PLC0415
    out: dict[str, Any] = {}
    for domain, label, obs, mutants in (
            ("reservations", "mc_inv_res", reservations_observations(run),
             [b[len("mutant_"):] for b in m["budgets"]["reservations_mutants"]["builds"]]),
            ("chancy_jobs", "mc_inv_chancy", chancy_observations(run), cr.MUTANTS)):
        results = []
        for source, h in invariant_candidates(m, gen, domain, label):
            a = iv.assess(h, obs)
            a["source"] = source
            results.append(a)
        sound = [o for o in obs if o["sound"]]
        out[domain] = {"observations": len(obs), "sound": len(sound),
                       "by_role": {r: sum(o["role"] == r for o in sound) for r in ("reference", "defective", "mutant")},
                       "candidates": results, "mutation_score": iv.mutation_score(results, mutants),
                       "mutation_score_deterministic": iv.mutation_score(
                           [r for r in results if r["source"] == "deterministic"], mutants),
                       "mutation_score_muse": iv.mutation_score([r for r in results if r["source"] == "muse"], mutants),
                       "oracle_kills": _oracle_kills(sound, mutants)}
    return out


def _oracle_kills(sound: list[dict[str, Any]], mutants: list[str]) -> dict[str, Any]:
    killed = sorted({o["mutant"] for o in sound if o["role"] == "mutant" and o.get("oracle_violated")})
    return {"killed": killed, "missed": sorted(set(mutants) - set(killed))}


def q3(inv: dict[str, Any]) -> dict[str, Any]:
    useful = ("mutant_sensitive", "duplicates_oracle", "new_property_candidate")
    out = {}
    for domain, d in inv.items():
        def kills(src: str, d: dict[str, Any] = d) -> set[str]:
            return {k for c in d["candidates"] if c["source"] == src and c.get("class") in useful
                    for k in list(c["checks"]["5_mutants"]["killed"]) + [b for b, v in c["checks"]["5_mutants"]
                                                                         ["defective_builds"].items() if v["violated"]]}
        mk, dk = kills("muse"), kills("deterministic")
        classes = {src: {} for src in ("muse", "deterministic", "control")}
        for c in d["candidates"]:
            classes[c["source"]][c["class"]] = classes[c["source"]].get(c["class"], 0) + 1
        if mk - dk:
            v = "improved"
        elif not mk and dk:
            v = "underperformed"
        else:
            v = "matched"
        out[domain] = {"verdict": v, "muse_kills": sorted(mk), "deterministic_kills": sorted(dk),
                       "muse_only": sorted(mk - dk), "classes": classes}
    return out


def main(m: dict[str, Any], gen: Path, run: Path, out: Path) -> int:
    if out.exists():
        raise SystemExit(f"refused: {out} exists")
    out.mkdir(parents=True)
    method, workloads = [], []
    for fn in (reservations_rows, chancy_rows, procrastinate_rows):
        a, b = fn(m, gen, run)
        method += a
        workloads += b
    for f, rows in (("results.jsonl", method), ("results_workloads.jsonl", workloads)):
        (out / f).write_text("".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows))
    inv = invariants(m, gen, run)
    (out / "invariants.json").write_text(json.dumps(inv, indent=1, sort_keys=True, default=str))
    cases = sorted({r["case"] for r in method})
    q1 = {c: compare([r for r in method if r["case"] == c], "B3", ["B1", "B2"]) for c in cases
          if not c.startswith("reservations-state")}
    q2 = {c: compare([r for r in method if r["case"] == c], "B5", ["B4"]) for c in cases
          if c.startswith("reservations-state")}
    cost = _usage(gen, [e["call"] for e in jl(gen / "calls.jsonl") if e.get("kind") == "call"])
    summary = {"Q1": q1, "Q2": q2, "Q3": q3(inv),
               "Q4": {"verdict": "not answerable", "why": "onboarding effort is not measured by this experiment"},
               "muse_cost_total": cost, "calls": sum(1 for e in jl(gen / "calls.jsonl") if e.get("kind") == "call"),
               "method_rows": len(method), "workload_rows": len(workloads)}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True, default=str))
    print(json.dumps({k: v for k, v in summary.items() if k != "Q3"}, indent=1, default=str))
    return 0
