"""EXPERIMENTAL, NONBLIND exploratory comparison: Muse's contribution (arms B1-B5) and invariant proposals.

    experiment.py freeze    --out docs/experimental_mvp/muse_contrib/EXPERIMENT_MANIFEST_v1.json
    experiment.py generate  --manifest M --out GEN_DIR            (Muse calls; no database)
    experiment.py run-reservations --manifest M --gen GEN_DIR --part B1|B2|B3|B4|B5|mutants --out RUN_DIR  (DB)
    experiment.py run-chancy --manifest M --gen GEN_DIR --part B1|B2|B3|mutants --out RUN_DIR             (DB)
    experiment.py run-procrastinate --manifest M --gen GEN_DIR --arm B1|B2|B3 --out RUN_DIR               (DB)
    experiment.py analyze   --manifest M --gen GEN_DIR --run RUN_DIR --out ANALYSIS_DIR

Every DB command must run under ``research_tools/pg_lock.py``. ``generate`` and every ``run-*`` refuse unless the
manifest is committed and unchanged (the freeze precedes every outcome). Evidence is never overwritten: a part's
output directory must not exist. Nothing a model returns is executed: proposals are JSON, validated by
deterministic code, and only validated workloads reach the trusted handlers.

These arms are exploratory and deliberately *not* the frozen protocol's A-G/R arms; nothing here is Gate 2A/A2
evidence, and no case is blind.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "research_tools"))

from deployspecter.experimental.invariants import language as lg  # noqa: E402
from deployspecter.experimental.muse_contrib import candidates as cand  # noqa: E402
from deployspecter.experimental.muse_contrib import chancy as ch  # noqa: E402
from deployspecter.experimental.muse_contrib import prompts as P  # noqa: E402
from deployspecter.experimental.muse_contrib import reservations as rs  # noqa: E402

MANIFEST_VERSION = "ds-mc-experiment/1"
SDISTS = Path(os.environ.get("DS_THIRDPARTY_SDIST_DIR", ""))
CODE_FILES = ["src/deployspecter/experimental/invariants/language.py",
              "src/deployspecter/experimental/invariants/validator.py",
              "src/deployspecter/experimental/muse_contrib/greedy.py",
              "src/deployspecter/experimental/muse_contrib/chancy.py",
              "src/deployspecter/experimental/muse_contrib/reservations.py",
              "src/deployspecter/experimental/muse_contrib/observe.py",
              "src/deployspecter/experimental/muse_contrib/candidates.py",
              "src/deployspecter/experimental/muse_contrib/prompts.py",
              "research_tools/muse_contrib/experiment.py", "research_tools/muse_contrib/chancy_runner.py",
              "research_tools/muse_contrib/analyze.py"]

PROCRASTINATE_B1 = [  # the frozen case's overlap workload, compare_1's second curated one, and one written now
    {"old_jobs": [{"kind": "ok"}, {"kind": "flaky", "fail_in": "3.3.0"}, {"kind": "flaky", "fail_in": "3.4.0"},
                  {"kind": "fail"}],
     "new_jobs": [{"kind": "ok"}, {"kind": "flaky", "fail_in": "3.3.0"}, {"kind": "flaky", "fail_in": "3.4.0"},
                  {"kind": "fail"}], "admin": [1]},
    {"old_jobs": [{"kind": "flaky", "fail_in": "3.4.0"}, {"kind": "flaky", "fail_in": "3.4.0"}],
     "new_jobs": [{"kind": "flaky", "fail_in": "3.3.0"}, {"kind": "flaky", "fail_in": "3.3.0"}], "admin": [1, 2]},
    {"old_jobs": [{"kind": "fail"}, {"kind": "flaky", "fail_in": "3.4.0"}],
     "new_jobs": [{"kind": "fail"}, {"kind": "ok"}], "admin": [1, 2]},
]

CALLS = [  # (label, prompt name, n, purpose)
    ("mc_chancy_w_r0", "chancy_workloads", 6, "B3 chancy-2895c4a workloads, replicate 0"),
    ("mc_chancy_w_r1", "chancy_workloads", 6, "B3 chancy-2895c4a workloads, replicate 1"),
    ("mc_procr_w_r0", "procrastinate_workloads", 5, "B3 procrastinate overlap workloads, replicate 0"),
    ("mc_res_w_r0", "reservations_planner", 8, "B3 reservations workloads, replicate 0"),
    ("mc_res_w_r1", "reservations_planner", 8, "B3 reservations workloads, replicate 1"),
    *[(f"mc_state_{s}_r{r}", f"reservations_state:{s}", 4, f"B5 states for actor set {s}, replicate {r}")
      for s in rs.ACTOR_SETS for r in (0, 1)],
    ("mc_inv_res", "invariants:reservations", 8, "invariant hypotheses, reservations"),
    ("mc_inv_chancy", "invariants:chancy_jobs", 8, "invariant hypotheses, chancy jobs"),
]

BUDGET = {"chancy": {"workloads_per_arm": 3, "seeds": [0, 1, 2], "trees": ["defective", "reference"]},
          "chancy_mutants": {"workloads": [0, 2], "seeds": [0, 1]},  # indices into chancy B1
          "procrastinate": {"workloads_per_arm": 3, "policy": {"kind": "alternate"}},
          "reservations": {"workloads_per_arm": 6, "schedules": 20, "builds": ["buggy", "fixed", "corrected"]},
          "reservations_state": {"states_per_arm": 3, "schedules": 20, "builds": ["buggy", "fixed", "corrected"]},
          "reservations_mutants": {"schedules": 20, "on": ["B1", "B4"],
                                   "builds": ["mutant_no_reservation_lock", "mutant_no_guard",
                                              "mutant_no_event_lock", "mutant_off_by_one"]},
          "muse_estimated_usd_ceiling": 5.0}


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


# ------------------------------------------------------------------------------------------------ freeze

def build_manifest() -> dict[str, Any]:
    prompts = []
    for label, name, n, purpose in CALLS:
        instructions, text, _model = P.catalog(name, n)
        prompts.append({"label": label, "prompt": name, "n": n, "purpose": purpose,
                        "sha256": P.prompt_sha256(name, n), "instructions": instructions, "text": text})
    return {
        "version": MANIFEST_VERSION, "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "nonblind": True, "exploratory": True,
        "arm_names": "B1 curated, B2 strong systematic deterministic (greedy pairwise coverage), B3 Muse workloads, "
                     "B4 identical workload + deterministic state, B5 identical workload + Muse state (same "
                     "constructor). Not the frozen protocol's A-G/R arms.",
        "code": {"revision_at_freeze": git("rev-parse", "HEAD"),
                 "files_sha256": {f: sha_file(REPO / f) for f in CODE_FILES}},
        "model": {"name": "muse-spark-1.3 (config default; MODEL_NAME unset)", "reasoning_effort": "medium",
                  "store": False, "max_output_tokens": P.MAX_OUTPUT_TOKENS,
                  "sampling": "provider defaults (no temperature or seed)", "pricing": P.PRICING_NOTE},
        "prompts": prompts,
        "cases": {
            "chancy-2895c4a": {"defective": "chancy-git-80f570f.tar (fix parent)",
                               "reference": "chancy-git-2895c4a.tar (fix commit)", "oracle": "historical_oracles."
                               "chancy_2895c4a (H0-H2, frozen)", "known_defect": True},
            "procrastinate-3.3-3.4": {"releases": ["procrastinate-3.3.0.tar.gz", "procrastinate-3.4.0.tar.gz"],
                                      "oracle": "frozen case checker, after errata E1 and E2",
                                      "known_defect": False},
            "reservations": {"builds": "buggy (lock_row off), fixed (lock_row on; still has the release defect), "
                                       "corrected (reference)", "oracle": "R1, R2 (frozen properties) + R5",
                             "targets": {"buggy-vs-corrected": "buggy", "fixed-vs-corrected": "fixed"},
                             "known_defect": True}},
        "deterministic_inputs": {
            "chancy": {"B1": ch.CURATED, "B2": ch.systematic(BUDGET["chancy"]["workloads_per_arm"])},
            "procrastinate": {"B1": PROCRASTINATE_B1, "B2": _procr_systematic()},
            "reservations": {"B1": rs.curated(), "B2": rs.systematic(BUDGET["reservations"]["workloads_per_arm"])},
            "reservations_state": {"actor_sets": rs.ACTOR_SETS,
                                   "B4": {s: rs.deterministic_states(s, BUDGET["reservations_state"]
                                                                     ["states_per_arm"]) for s in rs.ACTOR_SETS}},
            "invariant_candidates": {"reservations": cand.reservations(), "chancy_jobs": cand.chancy()},
            "chancy_mutants": _chancy_mutants()},
        "budgets": BUDGET,
        "selection_rules": {
            "B3/B5": "proposals are validated in returned order; the first K distinct valid ones (K = the arm's "
                     "budget) are executed; duplicates and invalid ones are recorded, never repaired or replaced "
                     "later. For reservations, valid means the unmodified chain returns `bridged` (dry-run "
                     "snapshot); a live construction failure afterwards counts against the arm.",
            "replicates": "B3 has two independent Muse calls per case (one for procrastinate); B5 two per actor "
                          "set. Deterministic arms run once."},
        "outcomes": {
            "detected": "at least one sound run on the defective build violates the case oracle (chancy: case "
                        "checker VIOLATED; reservations: any R1/R2/R5 violation)",
            "first_detection_schedule": "1-based cumulative index, over the arm's workloads in order, of the first "
                                        "detecting schedule on the defective build; null when not detected "
                                        "(censored at schedules_used)",
            "schedules_used": "schedules executed on the defective build",
            "fixed_version_violation": "any oracle violation on the reference build (or, for procrastinate, any "
                                       "violation after errata)",
            "invalid_proposals": "proposals rejected by validation (B3/B5)",
            "refused": "runs refused (corroboration, isolation, or a constructed state the harness refuses)",
            "no_verdict": "chancy runs whose H0 sanity check fails count as schedules used, never as detection"},
        "analysis_rules": {
            "Q1": "per case, B3 vs the better of B1/B2: improved if B3 detects where neither does, or every B3 "
                  "replicate needs strictly fewer schedules to first detection than both; underperformed if B3 "
                  "fails to detect where B1 or B2 detects, or every replicate needs more; else matched. Validity, "
                  "fixed-version violations and cost reported beside.",
            "Q2": "per actor set and target, B5 vs B4 with the same rule.",
            "Q3": "invariant candidates classified by invariants.validator; Muse improved if at least one Muse "
                  "candidate kills a mutant or defective build that no deterministic candidate kills, matched if "
                  "its useful classes are a subset, underperformed if it has no useful candidate where the "
                  "deterministic set does. A 'genuine new property' needs documentary review: never claimed here.",
            "Q4": "not measured here (onboarding is a separate workstream); reported as not answerable.",
            "statistics": "descriptive only: the sample (3 cases, 2 replicates) supports no inferential claim."}}


def _procr_systematic() -> list[dict[str, Any]]:
    from deployspecter.experimental import muse_procrastinate as mp
    return mp.systematic(BUDGET["procrastinate"]["workloads_per_arm"])


def _chancy_mutants() -> dict[str, Any]:
    import chancy_runner as cr  # noqa: PLC0415 - research tool module beside this one
    return {"existing": cr.EXISTING_MUTANTS, "new": {k: list(v) for k, v in cr.NEW_MUTANTS.items()},
            "base": cr.FIX_BASE}


def require_frozen(manifest: Path) -> dict[str, Any]:
    rel = str(manifest.resolve().relative_to(REPO))
    if not git("ls-files", rel):
        raise SystemExit(f"refused: {rel} isn't committed (freeze before any outcome)")
    if git("status", "--porcelain", "--", rel):
        raise SystemExit(f"refused: {rel} differs from its committed version")
    m = json.loads(manifest.read_text())
    for f, h in m["code"]["files_sha256"].items():
        if sha_file(REPO / f) != h:
            raise SystemExit(f"refused: {f} changed since the freeze")
    return m


# ------------------------------------------------------------------------------------------------ generate

def generate(m: dict[str, Any], out: Path, env_file: Path) -> None:
    from deployspecter.experimental.muse import client_from_env_file
    if out.exists():
        raise SystemExit(f"refused: {out} exists")
    out.mkdir(parents=True)
    ledger = out / "calls.jsonl"
    client = client_from_env_file(env_file, max_requests=len(m["prompts"]),
                                  max_estimated_usd=m["budgets"]["muse_estimated_usd_ceiling"], timeout=600)
    for pr in m["prompts"]:
        try:
            raw = P.call(client, pr["prompt"], pr["n"], label=pr["label"], purpose=pr["purpose"], ledger=ledger,
                         frozen_sha256=pr["sha256"])
        except Exception as exc:  # noqa: BLE001 - a failed call is recorded and the next one still runs
            (out / f"{pr['label']}.error.txt").write_text(f"{type(exc).__name__}: {exc}"[:2000])
            continue
        (out / f"{pr['label']}.proposals.json").write_text(json.dumps(raw, indent=1))
        validate_generated(pr, raw, ledger, out)
    (out / "COST.json").write_text(json.dumps({"requests": client.requests_sent,
                                               "estimated_usd": round(client.estimated_usd, 6)}, indent=1))


def validate_generated(pr: dict[str, Any], raw: list[tuple[str, Any]], ledger: Path, out: Path) -> None:
    from deployspecter.experimental import muse_apps as ma
    from deployspecter.experimental import muse_procrastinate as mp
    from deployspecter.experimental import planning_adapter as pa
    name, rows = pr["prompt"], []
    for label, p in raw:
        if name == "chancy_workloads":
            w, reasons = ch.validate(p)
            row = {"label": label, "valid": w is not None, "reasons": reasons, "workload": w}
        elif name == "procrastinate_workloads":
            w, reasons = mp.validate(p)
            row = {"label": label, "valid": w is not None, "reasons": reasons, "workload": w}
        elif name in ("reservations_planner",) or name.startswith("reservations_state:"):
            try:
                doc = (ma.to_workload_spec(p) if name == "reservations_planner" else
                       rs.state_to_document(name.split(":", 1)[1], label.replace("#", "_"), p, origin="model_proposal",
                                            author="muse"))
            except Exception as exc:  # noqa: BLE001
                row = {"label": label, "valid": False, "reasons": [f"shape: {str(exc)[:200]}"], "document": None}
            else:
                plan = pa.plan(doc)
                detail = plan.detail[0] if isinstance(plan.detail, list) and plan.detail else plan.detail
                row = {"label": label, "valid": plan.outcome == "bridged",
                       "reasons": [] if plan.outcome == "bridged" else [f"{plan.outcome}@{plan.stage}",
                                                                         json.loads(json.dumps(detail, default=str))],
                       "document": doc}
        else:
            reasons = lg.problems(p)
            row = {"label": label, "valid": not reasons, "reasons": reasons, "hypothesis": p}
        rows.append(row)
        P.record_validation(ledger, label, row["valid"], row["reasons"])
    (out / f"{pr['label']}.validated.json").write_text(json.dumps(rows, indent=1, default=str))


def selected(gen: Path, label: str, k: int, field: str) -> tuple[list[tuple[str, Any]], dict[str, int]]:
    """The first k distinct valid proposals of one call, in returned order (the frozen selection rule)."""
    f = gen / f"{label}.validated.json"
    if not f.exists():
        return [], {"returned": 0, "valid": 0, "invalid": 0, "duplicate": 0, "call_failed": 1}
    rows = json.loads(f.read_text())
    out, seen, dup = [], set(), 0
    for r in rows:
        if not r["valid"]:
            continue
        key = json.dumps({kk: vv for kk, vv in (r[field] or {}).items() if kk not in ("name", "rationale")}
                         if field == "document" else r[field], sort_keys=True)
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        if len(out) < k:
            out.append((r["label"], r[field]))
    return out, {"returned": len(rows), "valid": sum(r["valid"] for r in rows),
                 "invalid": sum(not r["valid"] for r in rows), "duplicate": dup, "call_failed": 0}


def call_usage(gen: Path, label: str) -> dict[str, Any]:
    ledger = gen / "calls.jsonl"
    if not ledger.exists():
        return {"tokens_in": 0, "tokens_out": 0, "est_usd": 0.0}
    for line in ledger.read_text().splitlines():
        e = json.loads(line)
        if e.get("kind") == "call" and e.get("call") == label:
            return {"tokens_in": e.get("input_tokens") or 0, "tokens_out": e.get("output_tokens") or 0,
                    "est_usd": e.get("estimated_usd") or 0.0}
    return {"tokens_in": 0, "tokens_out": 0, "est_usd": 0.0}


# ------------------------------------------------------------------------------------------------ reservations

def target() -> Any:
    from deployspecter.experimental import harness as hz
    secret = Path(os.path.expanduser("~/.ds_mvp_pg_password"))
    return hz.Target("127.0.0.1", int(os.environ.get("DS_MVP_PORT", "55441")), "ds_mvp", secret.read_text().strip())


def reservations_items(m: dict[str, Any], gen: Path, part: str) -> list[dict[str, Any]]:
    """[{arm, replicate, group, label, document}] for one part, in execution order."""
    k = m["budgets"]["reservations"]["workloads_per_arm"]
    det = m["deterministic_inputs"]
    items = []
    if part in ("B1", "B2"):
        for label, d in det["reservations"][part]:
            items.append({"arm": part, "replicate": 0, "group": "workload", "label": label, "document": d})
    elif part == "B3":
        for r in (0, 1):
            for label, d in selected(gen, f"mc_res_w_r{r}", k, "document")[0]:
                items.append({"arm": "B3", "replicate": r, "group": "workload", "label": label, "document": d})
    elif part == "B4":
        for s, states in det["reservations_state"]["B4"].items():
            for i, st in enumerate(states):
                d = rs.state_to_document(s, f"b4_{s}_{i}", st, origin="deterministic_baseline", author="ds_mc_grid")
                items.append({"arm": "B4", "replicate": 0, "group": s, "label": f"B4/{s}#{i}", "document": d})
    elif part == "B5":
        ks = m["budgets"]["reservations_state"]["states_per_arm"]
        for s in rs.ACTOR_SETS:
            for r in (0, 1):
                for label, d in selected(gen, f"mc_state_{s}_r{r}", ks, "document")[0]:
                    items.append({"arm": "B5", "replicate": r, "group": s, "label": label, "document": d})
    return items


def run_reservations(m: dict[str, Any], gen: Path, part: str, out: Path) -> int:
    from deployspecter.experimental import harness as hz
    from deployspecter.experimental import planning_adapter as pa
    from deployspecter.experimental.muse_contrib import observe as ob
    from deployspecter.experimental.stepping import Refused
    if out.exists():
        raise SystemExit(f"refused: {out} exists")
    out.mkdir(parents=True)
    t = target()
    if part == "mutants":
        builds = m["budgets"]["reservations_mutants"]["builds"]
        items = [it for p in m["budgets"]["reservations_mutants"]["on"] for it in reservations_items(m, gen, p)]
        s_max = m["budgets"]["reservations_mutants"]["schedules"]
    else:
        key = "reservations_state" if part in ("B4", "B5") else "reservations"
        builds, s_max = m["budgets"][key]["builds"], m["budgets"][key]["schedules"]
        items = reservations_items(m, gen, part)
    rows_f, obs_f = out / "runs.jsonl", out / "observations.jsonl"
    for it in items:
        started = time.monotonic()
        plan = pa.plan(it["document"])
        rec: dict[str, Any] = {**{k: v for k, v in it.items() if k != "document"}, "plan": plan.outcome,
                               "stage": plan.stage, "document_sha256": hashlib.sha256(json.dumps(
                                   it["document"], sort_keys=True).encode()).hexdigest(), "builds": {}}
        if plan.outcome != "bridged":
            rec["construction"] = "not_bridged"
            _append(rows_f, rec)
            continue
        try:
            fx = pa.PlannedReservations(plan)
            digest = fx.verify_initial(t)
            admin = t.admin()
            try:
                initial = fx.read_state(admin)
            finally:
                admin.close()
        except Refused as exc:
            rec["construction"], rec["construction_detail"] = "refused", str(exc)[:300]
            _append(rows_f, rec)
            continue
        rec["construction"], rec["initial_digest"] = "verified", digest
        invs = pa.invocations(plan)
        for build in builds:
            b0 = time.monotonic()
            try:
                results, exhausted = hz.explore_app(t, fx, invs, None, variant=build, max_runs=s_max,
                                                    label=it["label"], expected_digest=digest, corroborate=True)
            except Refused as exc:
                rec["builds"][build] = {"refused": str(exc)[:300], "seconds": round(time.monotonic() - b0, 3)}
                continue
            runs = []
            for i, r in enumerate(results):
                o = ob.reservations_observation(f"{it['arm']}/r{it['replicate']}/{it['label']}/{build}/{i}", build,
                                                r, initial, invs)
                o.update(arm=it["arm"], replicate=it["replicate"], group=it["group"], label=it["label"])
                _append(obs_f, o)
                runs.append({"i": i, "outcome": r.trace.outcome, "chosen": r.trace.chosen,
                             "violations": r.violations, "seconds": round(r.seconds, 4),
                             "serial": o["schedule_class"] == "serial", "sound": o["sound"],
                             "corroboration": {k: (r.corroboration or {}).get(k) for k in
                                               ("ok", "matched", "params_mismatched", "params_unchecked",
                                                "problems")}})
            rec["builds"][build] = {"runs": runs, "exhausted": exhausted,
                                    "seconds": round(time.monotonic() - b0, 3)}
        rec["seconds"] = round(time.monotonic() - started, 3)
        _append(rows_f, rec)
        print(json.dumps({"label": it["label"], "arm": it["arm"], "construction": rec["construction"],
                          **{b: sum(bool(x["violations"]) for x in v.get("runs", [])) for b, v in
                             rec["builds"].items()}}), flush=True)
    _drop_schema(t, "ds_run")
    return 0


def _drop_schema(t: Any, schema: str) -> None:
    from psycopg import sql
    admin = t.admin()
    try:
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
    finally:
        admin.close()


def _append(path: Path, entry: dict[str, Any]) -> None:
    with path.open("a") as f:
        f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")


# ------------------------------------------------------------------------------------------------ chancy

def chancy_items(m: dict[str, Any], gen: Path, part: str) -> list[dict[str, Any]]:
    k = m["budgets"]["chancy"]["workloads_per_arm"]
    det = m["deterministic_inputs"]["chancy"]
    if part in ("B1", "B2"):
        return [{"arm": part, "replicate": 0, "label": f"{part}#{i}", "workload": w} for i, w in enumerate(det[part])]
    if part == "B3":
        return [{"arm": "B3", "replicate": r, "label": label, "workload": w}
                for r in (0, 1) for label, w in selected(gen, f"mc_chancy_w_r{r}", k, "workload")[0]]
    raise ValueError(part)


def run_chancy(m: dict[str, Any], gen: Path, part: str, out: Path) -> int:
    import chancy_runner as cr  # noqa: PLC0415
    if out.exists():
        raise SystemExit(f"refused: {out} exists")
    out.mkdir(parents=True)
    t = target()
    cache_dir = out.parent / "chancy_units"  # one directory per (tree, mutant, workload, seed): shared across arms
    cache_dir.mkdir(exist_ok=True)
    images: dict[tuple[str, str | None], Any] = {}

    def unit(source: str, mutant: str | None, w: dict[str, Any], seed: int) -> tuple[str, dict[str, Any], bool]:
        uid = hashlib.sha256(json.dumps([source, mutant, w, seed], sort_keys=True).encode()).hexdigest()[:16]
        d = cache_dir / uid
        if (d / "report.json").exists():
            return uid, json.loads((d / "report.json").read_text()), True
        if (source, mutant) not in images:
            images[(source, mutant)] = cr.build(SDISTS / source, mutant)
        image, env = images[(source, mutant)]
        b0 = time.monotonic()
        try:
            rep = cr.run_one(t, image, env, SDISTS / source, w, seed, d, mutant=mutant)
        except Exception as exc:  # noqa: BLE001 - a crashed run is evidence, kept as such
            d.mkdir(parents=True, exist_ok=True)
            rep = {"verdict": "CRASHED", "error": f"{type(exc).__name__}: {str(exc)[:500]}", "workload": w,
                   "source": source, "mutant": mutant, "policy": {"kind": "seeded", "seed": seed}}
            (d / "report.json").write_text(json.dumps(rep, indent=1))
        rep["_seconds"] = round(time.monotonic() - b0, 2)
        (d / "seconds.json").write_text(json.dumps({"seconds": rep["_seconds"]}))
        return uid, rep, False

    rows_f = out / "units.jsonl"
    if part == "mutants":
        b1 = m["deterministic_inputs"]["chancy"]["B1"]
        plan = [(cr.FIX, mu, b1[i], s, "mutant", mu) for mu in cr.MUTANTS
                for i in m["budgets"]["chancy_mutants"]["workloads"] for s in m["budgets"]["chancy_mutants"]["seeds"]]
        plan += [(src, None, b1[i], s, role, None) for src, role in ((cr.FIX, "reference"), (cr.PARENT, "defective"))
                 for i in m["budgets"]["chancy_mutants"]["workloads"] for s in m["budgets"]["chancy_mutants"]["seeds"]]
        meta = [{"arm": "mutants", "replicate": 0, "label": f"mut#{j}"} for j in range(len(plan))]
    else:
        plan, meta = [], []
        for it in chancy_items(m, gen, part):
            for role, src in (("defective", cr.PARENT), ("reference", cr.FIX)):
                for s in m["budgets"]["chancy"]["seeds"]:
                    plan.append((src, None, it["workload"], s, role, None))
                    meta.append({k: v for k, v in it.items() if k != "workload"})
    for (src, mu, w, s, role, _), mt in zip(plan, meta, strict=True):
        uid, rep, cached = unit(src, mu, w, s)
        co = rep.get("case_oracle") or {}
        row = {**mt, "unit": uid, "cached": cached, "source": src, "role": role, "mutant": mu, "workload": w,
               "seed": s, "verdict": rep.get("verdict"), "case_verdict": co.get("verdict"),
               "seconds": rep.get("_seconds") or json.loads((cache_dir / uid / "seconds.json").read_text())["seconds"]
               if (cache_dir / uid / "seconds.json").exists() else None,
               "refusal_reasons": ((rep.get("run") or {}).get("refusal_reasons")), "error": rep.get("error")}
        _append(rows_f, row)
        print(json.dumps({k: row[k] for k in ("label", "role", "mutant", "seed", "verdict", "case_verdict",
                                               "cached", "seconds")}), flush=True)
    _drop_schema(t, cr.ri.CAPTURE_SCHEMA)  # the runner's capture schema, left for inspection by each run
    return 0


# ------------------------------------------------------------------------------------------------ procrastinate

def run_procrastinate(m: dict[str, Any], gen: Path, arm: str, out: Path) -> int:
    import mvp_compare_procrastinate as mcp  # noqa: PLC0415 - the existing comparison's coverage reader
    if out.exists():
        raise SystemExit(f"refused: {out} exists")
    out.mkdir(parents=True)
    k = m["budgets"]["procrastinate"]["workloads_per_arm"]
    if arm in ("B1", "B2"):
        ws = [(f"{arm}#{i}", w) for i, w in enumerate(m["deterministic_inputs"]["procrastinate"][arm])]
    else:
        ws = selected(gen, "mc_procr_w_r0", k, "workload")[0]
    env = {k2: v for k2, v in os.environ.items() if k2 not in ("MODEL_API_KEY", "DATABASE_URL")}
    for i, (label, w) in enumerate(ws):
        d = out / f"{arm}_{i}"
        b0 = time.monotonic()
        proc = subprocess.run([sys.executable, str(REPO / "research_tools/isolated/run_rolling_upgrade.py"),
                               "--old-sdist", str(SDISTS / "procrastinate-3.3.0.tar.gz"), "--new-sdist",
                               str(SDISTS / "procrastinate-3.4.0.tar.gz"), "--policy",
                               json.dumps(m["budgets"]["procrastinate"]["policy"]), "--overlap", json.dumps(w),
                               "--out", str(d)], capture_output=True, text=True, timeout=1800, env=env)
        (out / f"{arm}_{i}.log").write_text(f"exit={proc.returncode}\n" + proc.stdout[-20000:] + "\n--- stderr\n"
                                            + proc.stderr[-20000:])
        res = mcp.coverage(d) if (d / "report.json").exists() else {"verdict": "CRASHED"}
        row = {"arm": arm, "replicate": 0, "label": label, "workload": w, "exit": proc.returncode,
               "seconds": round(time.monotonic() - b0, 2), **res}
        _append(out / "runs.jsonl", row)
        print(json.dumps({k2: row.get(k2) for k2 in ("label", "exit", "verdict", "phases_sound",
                                                      "violations_after_errata", "seconds")}, default=str), flush=True)
    return 0


# ------------------------------------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--out", type=Path, required=True)
    g = sub.add_parser("generate")
    g.add_argument("--manifest", type=Path, required=True)
    g.add_argument("--out", type=Path, required=True)
    g.add_argument("--env-file", type=Path, default=Path.home() / "Desktop/deployspecter/.env")
    for name in ("run-reservations", "run-chancy", "run-procrastinate"):
        r = sub.add_parser(name)
        r.add_argument("--manifest", type=Path, required=True)
        r.add_argument("--gen", type=Path, required=True)
        r.add_argument("--part", "--arm", dest="part", required=True)
        r.add_argument("--out", type=Path, required=True)
    an = sub.add_parser("analyze")
    an.add_argument("--manifest", type=Path, required=True)
    an.add_argument("--gen", type=Path, required=True)
    an.add_argument("--run", type=Path, required=True)
    an.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.cmd == "freeze":
        if a.out.exists():
            raise SystemExit(f"refused: {a.out} exists (a frozen manifest is never overwritten)")
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(build_manifest(), indent=1, sort_keys=True, default=str) + "\n")
        print(sha_file(a.out))
        return 0
    m = require_frozen(a.manifest)
    if a.cmd == "generate":
        generate(m, a.out, a.env_file)
        return 0
    if a.cmd == "run-reservations":
        return run_reservations(m, a.gen, a.part, a.out)
    if a.cmd == "run-chancy":
        return run_chancy(m, a.gen, a.part, a.out)
    if a.cmd == "run-procrastinate":
        return run_procrastinate(m, a.gen, a.part, a.out)
    import analyze  # noqa: PLC0415 - research tool module beside this one
    return analyze.main(m, a.gen, a.run, a.out)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
