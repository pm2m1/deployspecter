"""EXPERIMENTAL, offline: the ds-invariant/1 language and the deterministic invariant validator."""

from __future__ import annotations

from deployspecter.experimental.invariants import language as lg
from deployspecter.experimental.invariants import validator as iv
from deployspecter.experimental.invariants.language import agg, agg_hyp, col, cond, const, row_hyp, term
from deployspecter.experimental.muse_contrib import candidates as cand

R = "reservations"


def state(events, reservations=(), calls=()):
    return {"events": [dict(zip(("id", "capacity", "reserved"), e, strict=True)) for e in events],
            "reservations": [dict(zip(("id", "event_id", "holder", "qty"), r, strict=True)) for r in reservations],
            "rejections": [], "calls": [dict(zip(("id", "actor", "handler", "status"), c, strict=True)) for c in calls]}


SEATS = term("child_sum", table="reservations", fk="event_id", col="qty")
R1 = row_hyp("r1", "reserved equals seats", R, "events", [], [cond(col("reserved"), "eq", SEATS)])


def test_admissibility_refuses_unknown_names_type_mixes_self_compare_and_null_ordering():
    assert lg.problems(R1) == []
    bad = [row_hyp("x", "", R, "nope", [], [cond(col("id"), "eq", const(1))]),
           row_hyp("x", "", R, "events", [], [cond(col("nope"), "eq", const(1))]),
           row_hyp("x", "", R, "reservations", [], [cond(col("holder"), "lt", const(3))]),
           row_hyp("x", "", R, "events", [], [cond(col("reserved"), "eq", col("reserved"))]),
           row_hyp("x", "", R, "events", [], [cond(col("reserved"), "lt", const(None))]),
           row_hyp("x", "", R, "events", [], []),
           row_hyp("x", "", R, "events", [], [cond(col("id"), "eq", const(1))] * 5),
           {"id": "x", "kind": "row"},
           agg_hyp("x", "", R, [agg("sum", "reservations", "holder")], "eq", [agg("int", value=0)]),
           agg_hyp("x", "", R, [agg("count", "events")], "eq", [agg("count", "events")])]
    for h in bad:
        assert lg.problems(h), h


def test_evaluation_child_sums_filters_and_signed_aggregates():
    s = state([(1, 10, 4)], [(1, 1, "jack", 2), (2, 1, "jill", 2)], [(1, "a", "reserve_seats", "reserved")])
    assert lg.evaluate(R1, s, s)["holds"]
    broken = state([(1, 10, 5)], [(1, 1, "jack", 2), (2, 1, "jill", 2)])
    assert not lg.evaluate(R1, broken, broken)["holds"]
    r5 = cand.reservations()[4]
    before = state([(1, 10, 2)], [(1, 1, "jack", 2)])
    assert lg.evaluate(r5, before, s)["holds"]  # 2 = 1 + 1 reserved - 0 released
    assert not lg.evaluate(r5, before, before | {"calls": s["calls"]})["holds"]


def test_null_semantics_are_fixed():
    jobs = {"jobs": [{"id": 1, "n": 1, "state": "failed", "attempts": 1, "max_attempts": 1, "has_claim_id": None,
                      "completed": True, "errors": 0, "taken": True, "cancelled": True, "executions": 1,
                      "successes": 1}]}
    d_c2 = cand.chancy()[1]  # where has_claim_id ne NULL: a NULL column excludes the row
    assert lg.evaluate(d_c2, jobs, jobs) == {"holds": True, "matched": 0, "violations": []}
    eq_null = row_hyp("x", "", "chancy_jobs", "jobs", [], [cond(col("has_claim_id"), "eq", const(None))])
    assert lg.evaluate(eq_null, jobs, jobs)["holds"]
    assert not lg._compare(None, "lt", 1) and not lg._compare(True, "eq", 1)


def obs(oid, role, final, *, mutant=None, cls="concurrent", oracle=None, initial=None, sound=True):
    return {"obs_id": oid, "case": "t", "build": f"mutant_{mutant}" if mutant else role, "role": role,
            "mutant": mutant, "schedule_class": cls, "sound": sound, "initial": initial or final, "final": final,
            "oracle_violated": oracle}


GOOD = state([(1, 10, 2)], [(1, 1, "jack", 2)])
BAD_R1 = state([(1, 10, 4)], [(1, 1, "jack", 2)])
BAD_OTHER = state([(1, 10, 2)], [(1, 1, "jack", 2), (2, 1, "jack", 0)])
CORPUS = [obs("ref0", "reference", GOOD, cls="serial", oracle=False), obs("ref1", "reference", GOOD, oracle=False),
          obs("m1", "mutant", BAD_R1, mutant="m1", oracle=True, initial=GOOD),
          obs("m2", "mutant", BAD_OTHER, mutant="m2", oracle=False, initial=GOOD),
          obs("m4", "mutant", state([(1, 10, 2)], [(1, 1, "jack", 3)]), mutant="m4", oracle=True, initial=GOOD),
          obs("ignored", "mutant", BAD_R1, mutant="m3", oracle=True, initial=GOOD, sound=False)]


def test_classes_on_a_synthetic_corpus():
    a = iv.assess(R1, CORPUS)
    assert a["class"] == "duplicates_oracle" and sorted(a["checks"]["5_mutants"]["killed"]) == ["m1", "m4"]
    assert a["checks"]["5_mutants"]["missed"] == ["m2"]  # m3's only run wasn't sound: no evidence
    qty = row_hyp("q", "qty positive", R, "reservations", [], [cond(col("qty"), "ge", const(1))])
    assert iv.assess(qty, CORPUS)["class"] == "new_property_candidate"
    controls = {h["id"]: iv.assess(h, CORPUS)["class"] for h in cand.reservations() if h["id"].startswith("X-")}
    assert controls == {"X-R-taut": "valid_vacuous", "X-R-false": "invalid"}
    never = row_hyp("v", "", R, "reservations", [cond(col("holder"), "eq", const("nobody"))],
                    [cond(col("qty"), "ge", const(5))])
    a = iv.assess(never, CORPUS)
    assert a["class"] == "valid_vacuous" and a["checks"]["6_vacuity"]["constrained_no_row"]
    weak = row_hyp("w", "", R, "events", [], [cond(col("reserved"), "le", const(3))])
    assert iv.assess(weak, CORPUS)["class"] == "mutant_sensitive"  # kills m1, a strict subset of the oracle
    assert iv.assess(R1, [o for o in CORPUS if o["role"] != "reference"])["class"] == "not_assessable"
    declined = CORPUS + [obs("m5", "mutant", BAD_R1, mutant="m5", oracle=None, initial=GOOD)]
    a = iv.assess(R1, declined)
    assert a["class"] == "duplicates_oracle" and a["oracle_comparison"]["where_oracle_gave_no_verdict"] == 1


def test_false_at_start_and_reference_false_positives_are_invalid():
    start_bad = [obs("ref", "reference", GOOD, initial=BAD_R1)]
    assert iv.assess(R1, start_bad)["why"] == "false on an initial state"
    fp = [obs("ref", "reference", BAD_R1, initial=GOOD)]
    a = iv.assess(R1, fp)
    assert a["class"] == "invalid" and a["checks"]["7_false_positives"]["reference_runs"] == ["ref"]


def test_synthetic_states_are_seeded_and_respect_the_domain():
    a, b = iv.synthetic_states("chancy_jobs", 5), iv.synthetic_states("chancy_jobs", 5)
    assert a == b and a != iv.synthetic_states("chancy_jobs", 5, seed=1)
    for s in a:
        for r in s["jobs"]:
            assert r["state"] in iv.POOLS["state"] and type(r["completed"]) is bool


def test_every_deterministic_candidate_is_admissible():
    for h in cand.reservations() + cand.chancy():
        assert lg.problems(h) == [], h["id"]
