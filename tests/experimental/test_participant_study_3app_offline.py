"""Offline tests of the three-application onboarding study (onb-study/2, DRAFT; NOT RUN). SYNTHETIC sessions only:
no participant, no model request, no database or container."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research_tools" / "onboarding"))

from deployspecter.experimental.onboarding_pilot import participant_study_3app as ps  # noqa: E402
from deployspecter.experimental.onboarding_pilot import session  # noqa: E402

BUDGET = 5400.0


def test_six_sequences_balance_applications_and_conditions_and_mirrors_balance_order():
    assert len(ps.sequences("min18")) == 6 and len(ps.sequences("pref24")) == 12
    for seq in ps.sequences("pref24").values():
        assert seq[0][0] != seq[1][0] and {seq[0][1], seq[1][1]} == {"D", "M"}
    b = ps.balance("min18", per_sequence=3)
    assert set(b["application_condition"].values()) == {6} and len(b["application_condition"]) == 6
    assert set(b["condition_period"].values()) == {9}
    assert b["application_period"]["X/P1"] == 12 and "X/P2" not in b["application_period"]  # order not balanced
    b24 = ps.balance("pref24", per_sequence=2)
    assert set(b24["application_period"].values()) == {8} and set(b24["application_condition"].values()) == {8}


def test_allocation_is_deterministic_in_full_blocks():
    ids = [f"P{i:02d}" for i in range(18)]
    a = ps.allocate(ids, "seed-1")
    assert a == ps.allocate(ids, "seed-1") and a != ps.allocate(ids, "seed-2")
    assert sorted(a.values()) == sorted(list(ps.sequences("min18")) * 3)
    a24 = ps.allocate([f"P{i:02d}" for i in range(24)], "s", "pref24")
    assert sorted(a24.values()) == sorted(list(ps.sequences("pref24")) * 2)
    with pytest.raises(ValueError):
        ps.allocate(ids, "s", "two-app")


def test_sign_test_power_and_sample_size_table():
    assert ps.critical_count(18) == 14 and ps.critical_count(12) == 10 and ps.critical_count(24) == 18
    assert round(ps.sign_test_p(14, 4), 3) == 0.031
    assert ps.power(18, 0.5) <= 0.05 and ps.power(18, 0.9) > 0.8 > ps.power(18, 0.7)
    t = ps.sample_size_table([18, 24], [0.75, 0.85], [0.2])
    r18 = t["rows"][0]
    assert r18["critical"] == 14 and r18["attrition"]["20%"]["n_effective"] == 14
    assert r18["minimum_detectable_p_80"] > t["rows"][1]["minimum_detectable_p_80"]


def _session(tmp: Path, pid: str, app: str, cond: str, t0: float, s3_after: float | None, calls=(), lines=10):
    d = tmp / f"{pid}_{app}_{cond}"
    session.start(d, app=app, condition=cond, operator=pid, repo=ROOT, paths=[], operator_kind="independent", now=t0)
    for i, secs in enumerate(calls):
        session.append(d, "model_call", now=t0 + 10 + i, label=f"c{i}", tokens_in=1, tokens_out=1, est_usd=0.001,
                       **({} if secs is None else {"call_seconds": secs, "call_started_t": t0 + 10 + i - secs}))
    if s3_after is not None:
        session.append(d, "milestone", now=t0 + s3_after, name="S3", evidence="synthetic")
    session.append(d, "end", now=t0 + (s3_after or BUDGET) + 1)
    m = ps.measure(d)
    m["adapter_lines_final"] = lines
    return m


def _synthetic(tmp: Path, n_per_seq: int, m_factor: float, design: str = "min18"):
    ids = [f"P{i:02d}" for i in range(n_per_seq * len(ps.sequences(design)))]
    alloc = ps.allocate(ids, "synthetic", design)
    base = {"X": 1000.0, "Y": 2000.0, "Z": 3000.0}
    ms = []
    for i, p in enumerate(ids):
        for period, (app, cond) in enumerate(ps.sequences(design)[alloc[p]]):
            t = base[app] * (m_factor if cond == "M" else 1.0) * (1 + 0.01 * i)
            ms.append(_session(tmp, p, app, cond, 1e6 * (i + 1) + period * 1e4, t,
                               calls=(30.0,) if cond == "M" else (), lines=6 if cond == "M" else 10))
    return ms, alloc


def test_timing_separates_end_to_end_and_execution_only(tmp_path):
    m = _session(tmp_path, "P1", "X", "M", 0.0, 900.0, calls=(40.0, 20.0))
    assert m["end_to_end_seconds_to_S3"] == 900.0 and m["muse_call_seconds_before_S3"] == 60.0
    assert m["execution_only_seconds_to_S3"] == 840.0 and m["execution_only_available"]
    u = _session(tmp_path, "P2", "X", "M", 0.0, 900.0, calls=(None,))  # an untimed (onb-study/1) call
    assert u["execution_only_seconds_to_S3"] is None and u["untimed_muse_calls"] == 1


def test_analysis_with_a_large_synthetic_effect_is_analyzable_and_significant(tmp_path):
    ms, alloc = _synthetic(tmp_path, 3, m_factor=0.5)
    r = ps.analyze(ms, alloc, BUDGET)
    assert r["verdict"].startswith("ANALYZABLE") and r["independent_participants_paired"] == 18
    assert r["primary_end_to_end"]["m_faster"] == 18 and r["primary_end_to_end"]["sign_test_p_two_sided"] < 0.001
    assert r["secondary_execution_only"]["m_faster"] == 18 and r["claim_rule_descriptive"]["met_on_all_three"]
    assert set(r["per_sequence"].values()) == {3}


def test_small_samples_deviations_and_developer_sessions_are_handled(tmp_path):
    ms, alloc = _synthetic(tmp_path, 2, m_factor=0.9)
    r = ps.analyze(ms, alloc, BUDGET)
    assert r["verdict"].startswith("INSUFFICIENT SAMPLE (12")
    swapped = dict(alloc)
    p0 = sorted(swapped)[0]
    swapped[p0] = next(q for q in ps.sequences("min18") if q != alloc[p0])
    r2 = ps.analyze(ms, swapped, BUDGET)
    assert r2["independent_participants_paired"] == 11 and len(r2["protocol_deviations"]) == 1
    dev = [dict(m, operator_kind="developer-ai") for m in ms]
    assert ps.analyze(dev, alloc, BUDGET)["excluded_non_independent_sessions"] == len(ms)


def test_censored_pairs_count_only_when_their_direction_is_determined(tmp_path):
    alloc = {p: "Q1" for p in "ABCE"}  # (X, D) then (Y, M); every M session censored (Y median 5400)
    ms = [_session(tmp_path, "A", "X", "D", 1e6, 1000.0), _session(tmp_path, "A", "Y", "M", 1e6 + 1e4, None),
          _session(tmp_path, "B", "X", "D", 2e6, None), _session(tmp_path, "B", "Y", "M", 2e6 + 1e4, None),
          _session(tmp_path, "C", "X", "D", 3e6, 5000.0), _session(tmp_path, "C", "Y", "M", 3e6 + 1e4, None),
          _session(tmp_path, "E", "X", "D", 4e6, 5300.0), _session(tmp_path, "E", "Y", "M", 4e6 + 1e4, None)]
    r = ps.analyze(ms, alloc, BUDGET)["primary_end_to_end"]  # X median 5150: A 0.19, C 0.97, E 1.03 vs M >= 1.0
    assert (r["ties_or_both_censored"], r["d_faster"], r["undetermined"], r["m_faster"]) == (1, 2, 1, 0)


def test_the_timed_muse_command_logs_durations_and_keeps_the_frozen_checks(tmp_path, monkeypatch):
    import pilot_v2
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps({"apps": {"X": {"condition": "M", "declared_paths": [], "sdist": "x.tar.gz",
                                              "budgets": {"muse": {"max_calls": 1, "max_estimated_usd": 0.5}}}}}))
    entry = ({"budgets": {"muse": {"max_calls": 1, "max_estimated_usd": 0.5}}}, {"sdist": "x.tar.gz"})
    monkeypatch.setattr(pilot_v2.pilot, "manifest_entry", lambda m, app: entry)
    d = tmp_path / "s"
    session.start(d, app="X", condition="M", operator="P1", repo=ROOT, paths=[], operator_kind="independent")
    ticks = iter([100.0, 112.5])
    a = SimpleNamespace(session=d, manifest=man, sdists=tmp_path, env_file=tmp_path / "none", label="c1")
    rc = pilot_v2.cmd_muse_timed(a, ask=lambda *_: {"tokens_in": 1, "tokens_out": 2, "est_usd": 0.01,
                                                    "reply_sha256": "0" * 64, "proposal": {}},
                                 monotonic=lambda: next(ticks), pkginfo=lambda p: "docs")
    call = [e for e in session.events(d) if e["kind"] == "model_call"][0]
    assert rc == 0 and call["call_seconds"] == 12.5 and call["timing"] == "onb-study/2" and not session.verify(d)
    assert pilot_v2.cmd_muse_timed(a, ask=lambda *_: {}, pkginfo=lambda p: "docs") == 2  # budget spent
    dd = tmp_path / "d"
    session.start(dd, app="X", condition="D", operator="P1", repo=ROOT, paths=[], operator_kind="independent")
    assert pilot_v2.cmd_muse_timed(SimpleNamespace(**{**vars(a), "session": dd}), ask=lambda *_: {}) == 2
