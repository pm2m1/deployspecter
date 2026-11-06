"""EXPERIMENTAL: the deterministic invariant validator. Proposers (Muse, a developer, a baseline) only propose; this
decides.

An **observation** is one run (or one constructed initial state) as data::

    {"obs_id", "case", "build", "role": reference | defective | mutant, "mutant": id or None,
     "schedule_class": initial | serial | concurrent, "sound": bool,
     "initial": state, "final": state, "oracle_violated": bool or None}

``reference`` is the corrected build; ``defective`` a known-defective build (a historical parent, a frozen buggy
switch); ``mutant`` an injected application mutant (a controlled experiment, never an upstream defect). Only
``sound`` observations count (a run whose server log didn't corroborate it, or that didn't complete, says nothing).

For each candidate, the eight checks, then one class:

1. corrected reference: holds on every reference run;
2. valid initial states: holds on every constructed initial state (initial = final = that state);
3. serial reference behaviour;  4. concurrent reference behaviour (both subsets of 1, reported separately);
5. mutants: killed when violated by at least one sound run of that mutant (defective builds reported apart);
6. vacuity: a row hypothesis that constrained no row in any observation, or one that also holds on every one of
   ``SYNTHETIC_STATES`` seeded random states of the domain (a tautology);
7. false positives: every reference or initial-state violation, listed;
8. kills and missed mutants, listed.

Classes, in this order:
* ``invalid``: inadmissible, false on an initial state, or a false positive on the reference;
* ``not_assessable``: admissible, but no sound reference observation exists;
* ``valid_vacuous``: never violated anywhere, and vacuous by check 6;
* ``valid``: holds on the reference, kills nothing;
* ``duplicates_oracle``: among the observations the oracle judged, violated in exactly those it flags (at least
  one); violations where the oracle gave no verdict are counted beside, not as disagreement;
* ``new_property_candidate``: violated in at least one sound observation the existing oracle judged and did *not*
  flag.
  A candidate only: whether it is a genuine new correctness property needs a documentary basis and a reviewer;
  the validator never decides that;
* ``mutant_sensitive``: kills at least one mutant or defective build, and its violations are a strict subset of
  the oracle's.
"""

from __future__ import annotations

import random
from typing import Any

from . import language as lg

SYNTHETIC_STATES = 300
POOLS = {"holder": ["jack", "jill", "zed"], "actor": ["a", "b", "c"], "handler": ["reserve_seats", "release_seats"],
         "status": ["reserved", "released", "rejected", "nothing_held", None],
         "state": ["pending", "running", "succeeded", "failed", "retrying"]}


def synthetic_states(domain: str, n: int = SYNTHETIC_STATES, seed: int = 0) -> list[dict[str, list[dict[str, Any]]]]:
    """Seeded random states of the domain's tables (types and nullability respected; no application invariant)."""
    rnd = random.Random(f"{domain}:{seed}")
    tables = lg.DOMAINS[domain]
    out = []
    for _ in range(n):
        state: dict[str, list[dict[str, Any]]] = {}
        for t, cols in tables.items():
            rows = []
            for i in range(rnd.randint(0, 4)):
                row: dict[str, Any] = {}
                for c, (ty, nullable) in cols.items():
                    if c == "id":
                        row[c] = i + 1
                    elif nullable and rnd.random() < 0.2:
                        row[c] = None
                    elif ty == "int":
                        row[c] = rnd.randint(1, 4) if c.endswith("_id") else rnd.randint(-2, 12)
                    elif ty == "bool":
                        row[c] = rnd.random() < 0.5
                    else:
                        row[c] = rnd.choice([p for p in POOLS.get(c, ["x", "y"]) if p is not None])
                rows.append(row)
            state[t] = rows
        out.append(state)
    return out


def _complete(state: dict[str, list[dict[str, Any]]], domain: str) -> dict[str, list[dict[str, Any]]]:
    return {t: list(state.get(t, [])) for t in lg.DOMAINS[domain]}


def assess(h: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    """The eight checks and the class of one candidate over the observations of its domain's case(s)."""
    out: dict[str, Any] = {"id": h.get("id"), "statement": h.get("statement"), "problems": lg.problems(h)}
    if out["problems"]:
        out["class"] = "invalid"
        out["why"] = "inadmissible"
        return out
    domain = h["domain"]
    out["digest"] = lg.digest(h)
    sound = [o for o in observations if o["sound"]]
    verdicts: dict[str, bool] = {}
    matched_any = False
    initial_bad, seen_initial = [], set()
    for o in sound:
        init, fin = _complete(o["initial"], domain), _complete(o["final"], domain)
        key = repr(sorted((t, [sorted(r.items()) for r in rs]) for t, rs in init.items()))
        if key not in seen_initial:
            seen_initial.add(key)
            e0 = lg.evaluate(h, init, init)
            matched_any |= e0["matched"] > 0
            if not e0["holds"]:
                initial_bad.append({"obs_id": o["obs_id"], "violations": e0["violations"]})
        e = lg.evaluate(h, init, fin)
        matched_any |= e["matched"] > 0
        verdicts[o["obs_id"]] = e["holds"]
    ref = [o for o in sound if o["role"] == "reference"]
    out["checks"] = {
        "1_reference": {"runs": len(ref), "violated": sum(not verdicts[o["obs_id"]] for o in ref)},
        "2_initial_states": {"states": len(seen_initial), "violated": len(initial_bad)},
        "3_serial_reference": _tally(ref, verdicts, "serial"),
        "4_concurrent_reference": _tally(ref, verdicts, "concurrent"),
    }
    kills, missed, defects = {}, [], {}
    for mid in sorted({o["mutant"] for o in sound if o["role"] == "mutant"}):
        runs = [o for o in sound if o["role"] == "mutant" and o["mutant"] == mid]
        bad = sum(not verdicts[o["obs_id"]] for o in runs)
        if bad:
            kills[mid] = {"runs": len(runs), "violated": bad}
        else:
            missed.append(mid)
    for b in sorted({o["build"] for o in sound if o["role"] == "defective"}):
        runs = [o for o in sound if o["role"] == "defective" and o["build"] == b]
        defects[b] = {"runs": len(runs), "violated": sum(not verdicts[o["obs_id"]] for o in runs)}
    out["checks"]["5_mutants"] = {"killed": kills, "missed": missed, "defective_builds": defects}
    tautology = all(lg.evaluate(h, s0, s1)["holds"] for s0, s1 in
                    zip(synthetic_states(domain), synthetic_states(domain, seed=1), strict=True))
    vacuous = (h["kind"] == "row" and not matched_any) or tautology
    out["checks"]["6_vacuity"] = {"constrained_no_row": h["kind"] == "row" and not matched_any,
                                  "holds_on_every_synthetic_state": tautology}
    fps = [o["obs_id"] for o in ref if not verdicts[o["obs_id"]]]
    out["checks"]["7_false_positives"] = {"reference_runs": fps, "initial_states": initial_bad}
    out["checks"]["8_kills"] = {"mutants_killed": sorted(kills), "mutants_missed": missed}

    nonref = [o for o in sound if o["role"] != "reference"]
    violated = {o["obs_id"] for o in nonref if not verdicts[o["obs_id"]]}
    oracle = {o["obs_id"] for o in sound if o.get("oracle_violated")}
    comparable = {o["obs_id"] for o in sound if o.get("oracle_violated") is not None}
    beyond = sorted((violated & comparable) - oracle)  # the oracle judged these and found nothing
    no_verdict = sorted(violated - comparable)  # the oracle declined to judge these (e.g. its sanity check failed)
    out["oracle_comparison"] = {"violated": len(violated), "oracle_flags": len(oracle & comparable),
                                "both": len(violated & oracle), "only_this": len(beyond),
                                "only_oracle": len((oracle & comparable) - violated),
                                "where_oracle_gave_no_verdict": len(no_verdict)}
    if initial_bad or fps:
        out["class"], out["why"] = "invalid", "false positive on the reference" if fps else "false on an initial state"
    elif not ref:
        out["class"], out["why"] = "not_assessable", "no sound reference observation"
    elif not violated:
        out["class"] = "valid_vacuous" if vacuous else "valid"
        out["why"] = "never violated" + (" and vacuous" if vacuous else "; kills nothing")
    elif violated & comparable == oracle & comparable and oracle & comparable:
        out["class"], out["why"] = "duplicates_oracle", "violated exactly where the existing oracle is"
        if no_verdict:
            out["why"] += f", and in {len(no_verdict)} observation(s) the oracle gave no verdict on"
    elif beyond:
        out["class"] = "new_property_candidate"
        out["why"] = (f"violated in {len(beyond)} sound observation(s) the existing oracle doesn't flag; a genuine new "
                      "property needs a documentary basis and a reviewer")
    else:
        out["class"] = "mutant_sensitive"
        out["why"] = "kills, within what the oracle already flags" + (
            f", plus {len(no_verdict)} observation(s) the oracle gave no verdict on" if no_verdict else "")
    out["vacuous"] = vacuous
    return out


def _tally(runs: list[dict[str, Any]], verdicts: dict[str, bool], cls: str) -> dict[str, int]:
    sel = [o for o in runs if o["schedule_class"] == cls]
    return {"runs": len(sel), "violated": sum(not verdicts[o["obs_id"]] for o in sel)}


def mutation_score(results: list[dict[str, Any]], mutants: list[str]) -> dict[str, Any]:
    """Across the admissible, non-invalid candidates: which mutants at least one kills, which none does."""
    ok = [r for r in results if r.get("class") not in ("invalid", "not_assessable")]
    killed = sorted({m for r in ok for m in r["checks"]["5_mutants"]["killed"]})
    return {"mutants": sorted(mutants), "killed_by_some_candidate": killed,
            "missed_by_all": sorted(set(mutants) - set(killed))}
