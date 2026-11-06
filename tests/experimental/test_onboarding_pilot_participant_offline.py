"""EXPERIMENTAL, offline: the independent-participant study code (onb-study/1), on SYNTHETIC data only. No record
here is, or represents, a real participant."""

from __future__ import annotations

import collections
import json

import pytest

from deployspecter.experimental.onboarding_pilot import participant_study as ps


def test_allocation_is_balanced_deterministic_and_counterbalanced():
    ids = [f"synthetic_{i}" for i in range(12)]
    a = ps.allocate(ids, "seed-x")
    assert a == ps.allocate(ids, "seed-x") and a != ps.allocate(ids, "seed-y")
    assert collections.Counter(a.values()) == {s: 3 for s in ps.SEQUENCES}
    for first, second in ps.SEQUENCES.values():
        assert {first[0], second[0]} == {"D", "M"} and {first[1], second[1]} == {"A", "B"}
    assert collections.Counter(s[0][0] for s in ps.SEQUENCES.values()) == {"D": 2, "M": 2}


def synth(n, m_faster=True, budget=5400.0):
    alloc = ps.allocate([f"p{i}" for i in range(n)], "s")
    out = []
    for i in range(n):
        d = {"operator": f"p{i}", "operator_kind": "independent", "condition": "D", "seconds_to_S3": 1000.0 + i,
             "interventions": 10, "attempts_failed": 2, "adapter_lines_revised": 30, "proposals_rejected": 0,
             "est_usd": 0}
        m = d | {"condition": "M", "seconds_to_S3": (500.0 if m_faster else 2000.0) + i, "interventions": 8,
                 "est_usd": 0.05}
        out += [d, m]
    return out, alloc


def test_a_consistent_synthetic_effect_is_detected_with_enough_participants():
    ms, alloc = synth(12)
    r = ps.analyze(ms, alloc, 5400)
    assert r["verdict"].startswith("ANALYZABLE") and r["primary"]["m_faster"] == 12
    assert r["primary"]["sign_test_p_two_sided"] < 0.001 and r["secondary_median_differences_m_minus_d"][
        "interventions"] == -2 and r["model_cost_usd"]["M"] == 0.6


def test_too_few_participants_is_insufficient_sample():
    ms, alloc = synth(8)
    assert ps.analyze(ms, alloc, 5400)["verdict"].startswith("INSUFFICIENT SAMPLE")


def test_censoring_ties_and_developer_exclusion():
    ms, alloc = synth(12, m_faster=False)
    ms[0]["seconds_to_S3"] = None  # p0's D session didn't finish: censored at the budget; M (2000) is faster
    ms[2]["seconds_to_S3"] = ms[3]["seconds_to_S3"] = None  # p1: both censored: a tie
    ms.append({"operator": "dev", "operator_kind": "developer-ai", "condition": "D", "seconds_to_S3": 1.0})
    r = ps.analyze(ms, alloc, 5400)
    assert r["primary"] == {**r["primary"], "m_faster": 1, "d_faster": 10, "ties_or_both_censored": 1}
    assert r["excluded_non_independent_sessions"] == 1


def test_blind_bundle_strips_identity_and_never_overwrites(tmp_path):
    s = tmp_path / "session"
    s.mkdir()
    ev = tmp_path / "attempt"
    ev.mkdir()
    (ev / "run.json").write_text(json.dumps({"app": "secret_app", "image": ["x"], "corroboration": {"ok": True},
                                             "handled": ["a"]}))
    (s / "events.jsonl").write_text(json.dumps({"seq": 3, "kind": "attempt", "evidence": str(ev)}) + "\n")
    key = ps.blind_bundle([s], tmp_path / "bundle", tmp_path / "key.json")
    (rid,) = key
    text = (tmp_path / "bundle" / f"{rid}.json").read_text()
    assert "secret_app" not in text and '"handled"' in text and key[rid].endswith("#3")
    with pytest.raises(FileExistsError):
        ps.blind_bundle([s], tmp_path / "bundle", tmp_path / "key.json")
