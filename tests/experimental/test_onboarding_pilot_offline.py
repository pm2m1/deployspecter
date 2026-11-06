"""EXPERIMENTAL, offline: the onboarding pilot harness (onb-pilot/1): validator (S1), correctness (S2, S3), session
log and measures, Muse proposal mapping and schema, analysis."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from deployspecter.experimental.onboarding_pilot import analysis, correctness, muse_assist, session, spec

PINS = {"sdist": "x-1.0.tar.gz", "sdist_sha256": "a" * 64, "version": "1.0"}
SPEC = {"app": "x", "version": "1.0", "sdist": "x-1.0.tar.gz", "sdist_sha256": "a" * 64, "package": "x",
        "install_metadata": False, "adapter": "deployspecter.experimental.onboarding_pilot.adapters.x",
        "schema": "onb_x", "seam": "pool",
        "wheels": [{"name": "y", "version": "2", "filename": "y-2-py3-none-any.whl", "sha256": "b" * 64}],
        "tasks": {"t": {"args": {"n": "int"}}}}
WL = {"jobs": [{"key": "a", "task": "t", "args": {"n": 1}}, {"key": "b", "task": "t", "args": {"n": 2}}]}


def test_a_valid_spec_and_workload():
    assert spec.validate(SPEC, WL, PINS) == []


@pytest.mark.parametrize("change,why", [
    ({"sdist_sha256": "c" * 64}, "isn't the pinned"), ({"version": "2.0"}, "isn't the pinned"),
    ({"adapter": "os.system"}, "adapter"), ({"adapter": "deployspecter.experimental.onboarding_pilot.adapters.X;"},
                                            "adapter"),
    ({"schema": "public"}, "schema"), ({"seam": "socket"}, "seam"),
    ({"wheels": [{"name": "y", "version": "2", "filename": "y.tar.gz", "sha256": "b" * 64}]}, "wheels"),
    ({"tasks": {"t": {"args": {"n": "str"}}}}, "tasks"), ({"tasks": {}}, "tasks"), ({"package": "../etc"}, "package"),
    ({"extra": 1}, "exactly the keys")])
def test_spec_refusals(change, why):
    assert any(why in p for p in spec.validate_spec(SPEC | change, PINS))


@pytest.mark.parametrize("wl,why", [
    ({"jobs": []}, "1..8"), ({"jobs": [{"key": "a", "task": "zz", "args": {"n": 1}}]}, "isn't declared"),
    ({"jobs": [{"key": "a", "task": "t", "args": {"n": 1000}}]}, "0..999"),
    ({"jobs": [{"key": "a", "task": "t", "args": {"n": True}}]}, "0..999"),
    ({"jobs": [{"key": "a", "task": "t", "args": {}}]}, "exactly"),
    ({"jobs": [{"key": "a", "task": "t", "args": {"n": 1}}] * 2}, "unique"),
    ({"jobs": [{"key": "a", "task": "t", "args": {"n": 1}, "sql": "x"}]}, "exactly key"), ({}, "workload")])
def test_workload_refusals(wl, why):
    assert any(why in p for p in spec.validate_workload(wl, SPEC))


GOOD_RUN = {"corroboration": {"ok": True, "params_mismatched": 0}, "isolation_ok": True,
            "teardown": {"container_removed": True}, "handled": ["a", "b"],
            "phases": {"install": {"exception": None}, "enqueue": {"exception": None}, "work": {"exception": None}}}


def test_s2_and_s3():
    assert correctness.s2(GOOD_RUN) == [] and correctness.s3(GOOD_RUN, WL) == []
    assert correctness.s3(GOOD_RUN | {"handled": ["a", "a", "b"]}, WL)
    assert correctness.s3(GOOD_RUN | {"handled": ["a"]}, WL)
    assert correctness.s3(GOOD_RUN | {"handled": ["a", "b", "c"]}, WL)
    assert correctness.s2(GOOD_RUN | {"corroboration": {"ok": True, "params_mismatched": 1}})
    assert correctness.s2(GOOD_RUN | {"isolation_ok": False}) and correctness.s2(GOOD_RUN | {"teardown": {"x": False}})
    bad = GOOD_RUN | {"phases": {"install": {"exception": None}, "enqueue": {"exception": "Boom"}}}
    assert any("enqueue raised" in p for p in correctness.s3(bad, WL)) and any("work didn't run" in p
                                                                               for p in correctness.s3(bad, WL))


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    (r / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(r), "add", "a.py"], check=True)
    subprocess.run(["git", "-C", str(r), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
                   check=True)
    return r


def test_session_chain_milestones_and_measures(repo, tmp_path):
    s = tmp_path / "s"
    session.start(s, app="x", condition="M", operator="op", repo=repo, paths=["a.py"], operator_kind="developer-ai",
                  now=100.0)
    session.append(s, "intervention", detail="read docs", now=110.0)
    session.append(s, "proposal", source="muse", problems=["bad"], now=120.0)
    session.append(s, "proposal", source="operator", problems=[], now=130.0)
    session.append(s, "milestone", name="S1", evidence="spec", now=130.0)
    session.snapshot(s, now=131.0)
    (repo / "a.py").write_text("x = 1\ny = 2\nz = 3\n")
    session.snapshot(s, now=140.0)
    session.append(s, "attempt", s2_problems=["x"], s3_problems=["x"], now=150.0)
    session.append(s, "attempt", s2_problems=[], s3_problems=[], now=160.0)
    session.append(s, "milestone", name="S2", evidence="run", now=160.0)
    session.append(s, "model_call", tokens_in=10, tokens_out=20, est_usd=0.01, now=125.0)
    session.append(s, "end", now=200.0)
    m = session.measure(s)
    assert m["seconds_to_S1"] == 30.0 and m["seconds_to_S2"] == 60.0 and m["seconds_to_S3"] is None
    assert m["seconds_total"] == 100.0 and m["interventions"] == 1 and m["proposals_rejected"] == 1
    assert m["muse_proposals_rejected"] == 1 and m["attempts"] == 2 and m["attempts_failed"] == 1
    assert m["adapter_lines_final"] == 3 and m["adapter_lines_revised"] == 2 and m["est_usd"] == 0.01
    assert m["s3_pass_rate_on_s2_runs"] == 1.0
    with pytest.raises(session.SessionRefused, match="ended"):
        session.append(s, "intervention", detail="late")
    with pytest.raises(session.SessionRefused):
        session.start(s, app="x", condition="D", operator="op", repo=repo, paths=[], operator_kind="developer-ai")


def test_session_refusals_and_tampering(repo, tmp_path):
    s = tmp_path / "s"
    with pytest.raises(session.SessionRefused, match="start"):
        session.append(s, "intervention", detail="x")
    session.start(s, app="x", condition="D", operator="op", repo=repo, paths=[], operator_kind="developer-ai")
    with pytest.raises(session.SessionRefused, match="evidence"):
        session.append(s, "milestone", name="S1")
    session.append(s, "milestone", name="S1", evidence="e")
    with pytest.raises(session.SessionRefused, match="already"):
        session.append(s, "milestone", name="S1", evidence="e")
    with pytest.raises(session.SessionRefused, match="kind"):
        session.append(s, "bogus")
    lines = (s / "events.jsonl").read_text().splitlines()
    e = json.loads(lines[1])
    e["t"] = 0
    (s / "events.jsonl").write_text("\n".join([lines[0], json.dumps(e)]) + "\n")
    assert session.verify(s) and pytest.raises(session.SessionRefused, session.measure, s)


def test_muse_schema_is_strict_and_proposals_map_to_the_harness_format():
    def strict(o):
        if o.get("type") == "object":
            assert o.get("additionalProperties") is False and set(o["required"]) == set(o["properties"])
            for v in o["properties"].values():
                strict(v)
        if o.get("type") == "array":
            strict(o["items"])
    strict(muse_assist.SCHEMA)
    p = {"task": {"name": "t", "args": ["n"]}, "workload": [{"key": "a", "n": 1}, {"key": "b", "n": 2}]}
    assert muse_assist.to_workload(p) == WL
    assert "execute" in muse_assist.INSTRUCTIONS


def test_analysis_is_descriptive_and_censors():
    m = {"app": "x", "condition": "D", "operator_kind": "developer-ai", "ended": True, "seconds_to_S1": 5.0,
         "seconds_to_S2": None, "seconds_to_S3": None}
    t = analysis.table([m], 3600)
    assert "descriptive only" in t["verdict"] and "censored" in t["rows"]["D:x"]["seconds_to_S2"]


def test_the_calibration_adapter_names_the_contract():
    src = (Path(__file__).resolve().parents[2] / "src/deployspecter/experimental/onboarding_pilot/adapters/"
           "calib_chancy.py").read_text()
    assert all(f"async def {k}(" in src for k in spec.CONTRACT["coroutines"]) and "CALIBRATION ONLY" in src
