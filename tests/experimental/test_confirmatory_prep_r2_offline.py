"""Offline tests of revision candidate r2's development-only preparation path
(research_tools/confirmatory_prep/prep_r2.py). SIMULATION integrations and a fake client only: no database,
container, model request or real case."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_tools" / "confirmatory_prep"))
sys.path.insert(0, str(ROOT / "src"))
import prep  # noqa: E402
import prep_r2 as r2  # noqa: E402

from deployspecter.config import Settings  # noqa: E402
from deployspecter.meta_client import MetaClient  # noqa: E402

K, S = r2.K, r2.S
H = {"R1": "R1", "R2": "R2"}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["jobs", "flag"],
          "properties": {"jobs": {"type": "array", "minItems": 1, "maxItems": 2, "items": {
              "type": "object", "additionalProperties": False, "required": ["n"],
              "properties": {"n": {"type": "integer", "minimum": 0, "maximum": 5}}}}, "flag": {"type": "boolean"}}}
W_T = {"jobs": [{"n": 0}], "flag": True}
W_HOLD = {"jobs": [{"n": 1}], "flag": False}
W_PAIR = {"jobs": [{"n": 2}, {"n": 3}], "flag": True}


def validate(w):
    if not isinstance(w, dict) or set(w) != {"jobs", "flag"}:
        return ["shape"]
    ns = [j["n"] for j in w["jobs"]]
    return [] if len(set(ns)) == len(ns) else ["distinct n"]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class SimInteg:
    """SIMULATION: flag=True violates target clause T on R1; each run advances the fake clock by ``secs``."""
    SIMULATION = True

    def __init__(self, clock, secs=5.0, slow=()):
        self.clock, self.secs, self.slow, self.calls = clock, secs, slow, 0

    def run(self, handle, w, seed):
        self.calls += 1
        self.clock.t += self.slow[0] if self.calls in self.slow[1:] else self.secs
        return {"verdict": "SOUND", "clauses": {"T": "VIOLATED" if handle == "R1" and w["flag"] else "HOLDS"}}


def executor(wls, clock=None, **kw):
    clock = clock or Clock()
    return r2.R2Executor(integration=kw.pop("integ", None) or SimInteg(clock), oracle_targets={"T"},
                         journal_sha=r2.journal(wls), clock=clock, simulation=True, **kw)


def test_r2_fixes_k_6_and_s_2():
    assert (K, S) == (6, 2)
    with pytest.raises(SystemExit, match="K = 6 and S = 2"):
        executor({"A": [W_T]}, k=4)
    with pytest.raises(SystemExit, match="K = 6 and S = 2"):
        r2.score_case({}, {"T"}, k=6, s=3)


def test_real_or_non_development_execution_stays_refused():
    with pytest.raises(SystemExit, match="real confirmatory execution is not enabled"):
        r2.R2Executor(integration=SimInteg(Clock()), oracle_targets={"T"}, journal_sha={}, real=True,
                      simulation=True)
    with pytest.raises(SystemExit, match="development or SIMULATION"):
        r2.R2Executor(integration=SimInteg(Clock()), oracle_targets={"T"}, journal_sha={}, split="confirmatory")


def test_ds_confirm_real_gate_is_unchanged_and_not_imported():
    src = (ROOT / "research_tools" / "confirmatory_prep" / "prep_r2.py").read_text()
    assert "confirmatory/run.py" in src and "import run" not in src and "from run" not in src


def test_every_arm_gets_equal_schedules_and_no_early_stopping_after_detection():
    wls = {"G": [W_T, W_HOLD, W_PAIR, {"jobs": [{"n": 4}], "flag": False}, {"jobs": [{"n": 0}, {"n": 1}],
                                                                            "flag": False}, W_HOLD],
           "M": [W_T], "D": []}
    e = executor(wls)
    cells = {arm: e.run_arm("c", arm, wl, H) for arm, wl in wls.items()}
    case = r2.score_case(cells, {"T"})
    for c in case["arms"].values():
        assert c["schedules_used_defective"] == c["schedules_used_corrected"] == K * S
    m = case["arms"]["M"]
    assert m["primary"]["detected"] and m["primary"]["schedules_to_detection"] == 1
    assert sum(r["slot"] == "R1" for r in cells["M"]) == K * S  # every schedule ran after the first detection
    assert case["arms"]["D"]["defective"]["REFUSED"] == K * S and case["arms"]["D"]["primary"]["censored"]
    assert case["arms"]["G"]["distinct_workloads_executed"] == 5  # W_HOLD twice in G's list
    with pytest.raises(SystemExit, match="unequal schedules|unequal budget"):
        r2.score_case({**cells, "M": cells["M"][:-2]}, {"T"})


def test_workloads_must_match_the_admission_journal():
    wls = {"M": [W_T, W_HOLD]}
    e = executor(wls)
    with pytest.raises(SystemExit, match="admission journal"):
        e.run_arm("c", "M", [W_HOLD, W_T], H)  # reordered after the journal
    with pytest.raises(SystemExit, match="admission journal"):
        e.run_arm("c", "M", [W_T, W_PAIR], H)  # substituted
    assert len(e.run_arm("c", "M", [W_T, W_HOLD], H)) == 2 * K * S


def test_exactly_r_requests_and_none_once_runs_start():
    plan = r2.RequestPlan("c", "M", r=2)
    assert plan.begin() == 0 and plan.begin() == 1
    with pytest.raises(SystemExit, match="exactly R = 2"):
        plan.begin()
    plan2 = r2.RequestPlan("c", "M", r=2)
    plan2.begin()
    wls = {"M": [W_T]}
    executor(wls).run_arm("c", "M", [W_T], H, plan=plan2)
    with pytest.raises(SystemExit, match="outcome-dependent"):
        plan2.begin()
    assert plan2.summary()["made"] == 1 and plan2.summary()["unused_allowance"] == 1


def test_invalid_repeated_and_unfilled_slots_are_all_accounted():
    a = prep.admit([{"bad": 1}, W_T, {"jobs": [{"n": 1}, {"n": 1}], "flag": True}], validate, K)
    acc = r2.slot_accounting(a)
    assert acc == {"proposals": 3, "invalid": 2, "valid_beyond_k": 0, "admitted": 1, "distinct_admitted": 1,
                   "slots": K, "slots_filled_first_time": 1, "slots_filled_by_repetition": K - 1,
                   "slots_unfilled_refused": 0}
    none = r2.slot_accounting(prep.admit([{"bad": 1}], validate, K))
    assert none["slots_unfilled_refused"] == K and none["slots_filled_by_repetition"] == 0 and none["invalid"] == 1


def test_eligibility_rules_are_frozen_and_applied_identically_to_every_arm():
    rule = r2.EligibilityRule("no-pair", "reject two-job workloads (prospective)", lambda w: len(w["jobs"]) == 2)
    frozen = r2.freeze_eligibility([rule])
    v = r2.UniformValidator(validate, [rule], frozen)
    for props in ([W_PAIR, W_T], [W_T, W_PAIR, W_HOLD]):  # two arms, one validator
        adm = prep.admit(props, v, K)
        assert W_PAIR not in adm["admitted"] and any(i["problems"] == ["ineligible:no-pair"] for i in adm["invalid"])
    g = prep.generate_det_g(SCHEMA, v, "c", K)
    assert g["shortfall"] == 0 and all(len(w["jobs"]) == 1 for w in g["workloads"]) and g["rejected_by_validate"]
    changed = r2.EligibilityRule("no-pair", "reject two-job workloads (prospective)", lambda w: len(w["jobs"]) > 1)
    with pytest.raises(SystemExit, match="differ from the freeze"):
        r2.UniformValidator(validate, [changed], frozen)
    with pytest.raises(SystemExit, match="differ from the freeze"):
        r2.UniformValidator(validate, [], frozen)


def test_a_run_over_the_executor_limit_is_incomplete_for_any_arm():
    clock = Clock()
    integ = SimInteg(clock, secs=5.0, slow=(r2.L_RUN_SECONDS + 1, 1))  # the first run overruns L
    e = executor({"M": [W_T]}, clock=clock, integ=integ)
    runs = e.run_arm("c", "M", [W_T], H)
    assert runs[0]["verdict"] == "INCOMPLETE" and runs[0]["verdict_before_limit"] == "SOUND"
    cell = r2.score_case({"M": runs}, {"T"})["arms"]["M"]
    assert cell["primary"]["schedules_to_detection"] == 2 and cell["time"]["runs_over_executor_limit"] == 1


def test_the_safety_ceiling_excludes_the_case_for_every_arm_and_cannot_be_reached_within_limits():
    clock = Clock()
    e = executor({"M": [W_T]}, clock=clock, integ=SimInteg(clock, secs=r2.L_RUN_SECONDS))  # every run at L
    assert len(e.run_arm("c", "M", [W_T], H)) == 2 * K * S  # 24 x 300 s = 7,200 s < 7,500 s: not truncated
    clock2 = Clock()
    e2 = executor({"M": [W_T]}, clock=clock2, integ=SimInteg(clock2, secs=400.0))
    with pytest.raises(r2.InfrastructureFailure, match="excluded for every arm"):
        e2.run_arm("c", "M", [W_T], H)
    assert r2.excluded_case("c", "ceiling", ["M", "G"]) == {"case": "c", "excluded_for_every_arm": ["G", "M"],
                                                            "reason": "ceiling", "rerun": False}


def test_time_rule_bounds_900_and_1200_are_unsafe_and_7500_is_safe():
    assert r2.bound_seconds(48.1, gap=0.9) == pytest.approx(1175.1)
    rep = {x["ceiling_s"]: x for x in r2.ceiling_report([900, 1200, 7500], 48.1, 0.9)}
    assert rep[900]["margin_over_observed_s"] < 0 and rep[1200]["margin_over_observed_s"] == pytest.approx(24.9)
    assert rep[1200]["can_truncate_an_arm"] and not rep[7500]["can_truncate_an_arm"]
    assert r2.time_rule_problems(1200) and r2.time_rule_problems(7200) and r2.time_rule_problems(7500) == []


def test_independent_exposure_records_and_a_separate_integrator():
    t = 100.0
    ok = [r2.exposure_record("I1", "integrator", "c", [{"item": "README", "kind": "docs"}], t),
          r2.exposure_record("C1", "curator", "c", [{"item": "fix", "kind": "fix"}], t),
          r2.exposure_record("O1", "oracle_author", "c", [{"item": "api", "kind": "public_api"}], t)]
    assert r2.independence_problems(ok, freeze_at=200.0) == []
    same = ok + [r2.exposure_record("C1", "integrator", "c", [], t)]
    assert any("separate people" in p for p in r2.independence_problems(same, 200.0))
    blind = [r2.exposure_record("I1", "integrator", "c", [{"item": "fix tests", "kind": "fix_tests"}], t)]
    assert any("read fix_tests" in p for p in r2.independence_problems(blind, 200.0))
    late = [r2.exposure_record("I1", "integrator", "c", [], 300.0)]
    assert any("after the freeze" in p for p in r2.independence_problems(late, 200.0))
    assert any("no Integrator" in p for p in r2.independence_problems(ok[1:], 200.0))
    tampered = [dict(ok[0], reads=[])]
    assert any("altered" in p for p in r2.independence_problems(tampered, 200.0))


def test_frozen_inputs_are_leak_checked():
    labels = [{"case_id": "c-7f3a", "repository": "https://example.invalid/x", "buggy_commit": "a" * 40,
               "fixed_commit": "b1c2d3e4f5" + "0" * 30, "fix_paths": ["pkg/worker.py"], "defect_summary": "x",
               "keywords": ["stranded"], "exposure": "none"}]
    rule = r2.EligibilityRule("no-pair", "jobs left stranded are rejected", lambda w: False)
    hits = prep.leak_check_inputs(labels, {"schema": SCHEMA, "eligibility": rule.description})
    assert {h["input"] for h in hits} == {"eligibility"}


class FakeResponses:
    def __init__(self, model):
        self.model, self.calls = model, 0

    def create(self, **kw):
        self.calls += 1
        return SimpleNamespace(model=self.model, output_text='{"workloads": []}', status="completed",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=20, output_tokens_details=None))


def client(responses):
    s = Settings(admin_dsn="", test_dsn="", model_api_key=None, model_base_url="http://invalid",
                 model_name="muse-spark-1.3")
    return MetaClient(s, client=SimpleNamespace(responses=responses), max_requests=5, max_estimated_usd=1.0)


REQ = dict(proposer_source=b"p", instructions="i", context={"r": 1}, schema={"type": "object"}, prompt="x",
           schema_name="s", max_output_tokens=1000)


def frozen(**extra):
    f = prep.freeze_record(proposer_source=b"p", instructions="i", context={"r": 1}, schema={"type": "object"},
                           model="muse-spark-1.3", reasoning_effort="medium", store=False, max_output_tokens=1000)
    return {**f, **extra}


def test_model_identity_uses_the_exact_accepted_string_and_hashes_are_checked_on_every_request():
    r = FakeResponses("muse-spark-1.3-2026-10-01")
    plan = r2.RequestPlan("c", "M", r=2)
    out = r2.r2_generate(client(r), frozen(accepted_provider_model="muse-spark-1.3-2026-10-01"), plan, **REQ)
    assert out["usable"] and out["accepted_provider_model"] == "muse-spark-1.3-2026-10-01"
    with pytest.raises(SystemExit, match="changed"):  # the second request's context differs: refused, not sent
        r2.r2_generate(client(r), frozen(accepted_provider_model="muse-spark-1.3-2026-10-01"), plan,
                       **{**REQ, "context": {"r": 2}})
    assert r.calls == 1 and plan.summary()["by_status"] == {"ok": 1, "refused_before_sending": 1}
    with pytest.raises(SystemExit, match="exactly R = 2"):
        r2.r2_generate(client(r), frozen(), plan, **REQ)  # no replacement for the refused request
    mismatch = r2.r2_generate(client(FakeResponses("muse-spark-1.3")), frozen(accepted_provider_model="other"),
                              r2.RequestPlan("c", "M", r=1), **REQ)
    assert mismatch["status"] == "provider_model_mismatch" and not mismatch["usable"]
