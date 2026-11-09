"""Offline tests of the development-only preparation path (research_tools/confirmatory_prep/prep.py) for revision
candidate r1's proposed #23–#28. SIMULATION integrations only: no database, container, model or real case."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_tools" / "confirmatory_prep"))
import prep  # noqa: E402

K, S = 6, 2
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["jobs", "flag"],
          "properties": {"jobs": {"type": "array", "minItems": 1, "maxItems": 2, "items": {
              "type": "object", "additionalProperties": False, "required": ["n"],
              "properties": {"n": {"type": "integer", "minimum": 0, "maximum": 5}}}}, "flag": {"type": "boolean"}}}


def validate(w):
    if not isinstance(w, dict) or set(w) != {"jobs", "flag"}:
        return ["shape"]
    ns = [j["n"] for j in w["jobs"]]
    return [] if len(set(ns)) == len(ns) else ["distinct n"]


class SimInteg:
    """SIMULATION: on R1, flag=True violates target clause T; with 2 jobs, clause X (non-target) is violated on both
    revisions; a workload with n == 5 is INCOMPLETE; n == 4 is REFUSED."""
    SIMULATION = True

    def __init__(self):
        self.calls = []

    def run(self, handle, w, seed):
        self.calls.append((handle, seed))
        ns = [j["n"] for j in w["jobs"]]
        if 5 in ns:
            return {"verdict": "INCOMPLETE"}
        if 4 in ns:
            return {"verdict": "REFUSED"}
        clauses = {"T": "VIOLATED" if handle == "R1" and w["flag"] else "HOLDS",
                   "X": "VIOLATED" if len(ns) == 2 else "HOLDS"}
        return {"verdict": "SOUND", "clauses": clauses}


def ex():
    return prep.PrepExecutor(k=K, s=S, integration=SimInteg(), oracle_targets={"T"}, simulation=True)


H = {"R1": "R1", "R2": "R2"}
W_T = {"jobs": [{"n": 0}], "flag": True}        # target violation on R1 only
W_HOLD = {"jobs": [{"n": 1}], "flag": False}    # holds everywhere
W_X = {"jobs": [{"n": 0}, {"n": 1}], "flag": False}  # non-target clause X violated on both revisions
W_INC = {"jobs": [{"n": 5}], "flag": True}
W_REF = {"jobs": [{"n": 4}], "flag": True}


def test_equal_resource_budgets_whatever_the_number_of_workloads():
    e = ex()
    for arm, wl in (("A", [W_HOLD]), ("B", [W_HOLD, W_T, W_X, W_HOLD, W_T, W_HOLD]), ("C", [])):
        runs = e.run_arm("c", arm, wl, H)
        assert sum(r["slot"] == "R1" for r in runs) == K * S == sum(r["slot"] == "R2" for r in runs)
    with pytest.raises(SystemExit, match="unequal budget"):
        prep.score_cell(runs[:-1], {"T"}, K, S)


def test_slot_allocation_and_seeds_are_deterministic_and_equal_across_arms():
    assert prep.slots(["a", "b"], 5) == ["a", "b", "a", "b", "a"] and prep.slots([], 3) == [None, None, None]
    e = ex()
    a = [(r["k"], r["j"], r["seed"]) for r in e.run_arm("c", "A", [W_HOLD], H)]
    b = [(r["k"], r["j"], r["seed"]) for r in e.run_arm("c", "B", [W_T, W_X], H)]
    assert a == b and a == [(r["k"], r["j"], r["seed"]) for r in ex().run_arm("c", "A", [W_HOLD], H)]


def test_invalid_proposals_stay_in_the_accounting_and_order_is_kept():
    props = [{"bad": 1}, W_T, {"jobs": [{"n": 1}, {"n": 1}], "flag": True}] + [W_HOLD] * 7
    a = prep.admit(props, validate, K)
    assert a["admitted"][0] == W_T and len(a["admitted"]) == K
    assert [x["index"] for x in a["invalid"]] == [0, 2] and a["valid_beyond_k"] == 2 and a["proposals"] == 10


def test_repeated_workloads_cycle_with_new_seeds_and_the_generator_supplies_distinct_ones():
    runs = ex().run_arm("c", "A", [W_T, W_HOLD], H)
    shas = [r["workload_sha256"] for r in runs if r["slot"] == "R1" and r["j"] == 1]
    assert len(set(shas)) == 2 and len(shas) == K
    seeds = {r["seed"] for r in runs if r["slot"] == "R1" and r["workload_sha256"] == prep.sha(W_T)}
    assert len(seeds) == K  # each repetition has its own seeds
    g = prep.generate_det_g(SCHEMA, validate, "c", K)
    assert g["distinct"] == K and g["shortfall"] == 0 and all(not validate(w) for w in g["workloads"])
    assert g == {**prep.generate_det_g(SCHEMA, validate, "c", K), "generation_seconds": g["generation_seconds"]}
    tiny = {"type": "object", "properties": {"flag": {"type": "boolean"}}}
    assert prep.generate_det_g(tiny, lambda w: [], "c", K)["shortfall"] == K - 2  # never padded


def test_target_and_non_target_clause_scoring_with_the_void_rule():
    runs = ex().run_arm("c", "M", [W_X, W_T], H)
    cell = prep.score_cell(runs, {"T"}, K, S)
    assert cell["primary"]["void"] and not cell["primary"]["detected"]  # X on the corrected revision voids (primary)
    assert {v["k"] for v in cell["triage_queue"]} == {0, 2, 4} and all(v["violated"] == ["X"] for v in
                                                                          cell["triage_queue"])
    assert cell["secondary_slot_level"] == {"detected": True, "schedules_to_detection": 3}
    clean = prep.score_cell(ex().run_arm("c", "M", [W_HOLD, W_T], H), {"T"}, K, S)
    assert clean["primary"]["detected"] and clean["primary"]["schedules_to_detection"] == 3


def test_a_corrected_version_target_violation_voids_and_is_queued():
    runs = ex().run_arm("c", "M", [W_T], H)
    for r in runs:
        if r["slot"] == "R2" and r["k"] == 1:
            r["clauses"] = {"T": "VIOLATED", "X": "HOLDS"}
    cell = prep.score_cell(runs, {"T"}, K, S)
    assert cell["primary"]["void"] and cell["triage_queue"][0]["target_violated"]
    with pytest.raises(SystemExit, match="no target clause"):
        prep.score_cell(runs, set(), K, S)


def test_incomplete_and_refused_runs_consume_schedules_and_are_nondetections():
    runs = ex().run_arm("c", "M", [W_INC, W_REF, W_T], H)
    cell = prep.score_cell(runs, {"T"}, K, S)
    assert cell["defective"]["INCOMPLETE"] == 4 and cell["defective"]["REFUSED"] == 4
    assert cell["primary"]["schedules_to_detection"] == 5  # slots 0 and 1 used 4 schedules first
    empty = prep.score_cell(ex().run_arm("c", "M", [], H), {"T"}, K, S)
    assert empty["defective"]["REFUSED"] == K * S and empty["primary"]["censored"]


def test_nondetection_is_censored_at_k_times_s_and_end_to_end_adds_generation():
    cell = prep.score_cell(ex().run_arm("c", "M", [W_HOLD], H), {"T"}, K, S)
    assert cell["primary"] == {"void": False, "detected": False, "schedules_to_detection": None, "censored_at": 12,
                               "censored": True}
    hit = prep.score_cell(ex().run_arm("c", "M", [W_T], H), {"T"}, K, S, generation_seconds=40.0)
    assert hit["end_to_end_seconds_to_detection"] >= 40.0 > hit["execution_seconds_to_detection"]


def test_model_and_context_hashes_are_verified_before_any_request():
    base = dict(proposer_source=b"code", instructions="instr", context={"registry": ["op"]}, schema=SCHEMA,
                model="muse-spark-1.3", reasoning_effort="medium", store=False, max_output_tokens=16000)
    frozen = prep.freeze_record(**base)
    prep.verify_freeze(frozen, prep.freeze_record(**base))
    for change in ({"context": {"registry": ["op", "x"]}}, {"model": "other"}, {"max_output_tokens": 1},
                   {"instructions": "instr!"}, {"store": True}):
        with pytest.raises(SystemExit, match="changed"):
            prep.verify_freeze(frozen, prep.freeze_record(**{**base, **change}))


def test_no_hidden_label_leaks_into_frozen_inputs():
    labels = [{"case_id": "c-7f3a", "repository": "https://example.invalid/x", "buggy_commit": "a" * 40,
               "fixed_commit": "b1c2d3e4f5" + "0" * 30, "fix_paths": ["pkg/worker.py"],
               "defect_summary": "x", "keywords": ["stranded"], "exposure": "none"}]
    clean = {"schema": SCHEMA, "context": {"registry": ["enqueue(n)"], "schema_ddl": "CREATE TABLE jobs()"}}
    assert prep.leak_check_inputs(labels, clean) == []
    leaky = {**clean, "context": {"notes": "see b1c2d3e and pkg/worker.py; job left stranded"}}
    fields = {h["label_field"] for h in prep.leak_check_inputs(labels, leaky)}
    assert {"c-7f3a:fixed_commit[:7]", "c-7f3a:fix_path", "c-7f3a:keyword"} <= fields


def test_the_preparation_path_never_runs_a_real_or_non_development_evaluation():
    with pytest.raises(SystemExit, match="real confirmatory execution is not enabled"):
        prep.PrepExecutor(k=K, s=S, integration=SimInteg(), oracle_targets={"T"}, real=True, simulation=True)
    with pytest.raises(SystemExit, match="development or SIMULATION"):
        prep.PrepExecutor(k=K, s=S, integration=SimInteg(), oracle_targets={"T"}, split="confirmatory")
