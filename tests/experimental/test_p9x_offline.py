"""EXPERIMENTAL ds-p9x/1, offline: the jsonb/1 contract, the loader's substitution and source pins, the tracer, and
the Chancy registry."""

from __future__ import annotations

import collections
import io
import os
import tarfile
from pathlib import Path

import pytest

from deployspecter.experimental.phase9b_ext import chancy_registry_v1 as reg
from deployspecter.experimental.phase9b_ext import jsonb_v1, loader, trace
from deployspecter.experimental.state_construction import chancy

# ------------------------------------------------------------------------------------------------ jsonb/1


@pytest.mark.parametrize("text,canonical", [
    ('{"bb": 1, "a": 2}', '{"a": 2, "bb": 1}'),  # jsonb key order: by length, then bytes
    ('{"b": 1, "a": 2}', '{"a": 2, "b": 1}'),
    ('{ "x" : [ 1 , true , null ] }', '{"x": [1, true, null]}'),
    ('"tab\\tnl\\nq\\"bs\\\\"', '"tab\\tnl\\nq\\"bs\\\\"'),
    ('"\\u0001"', '"\\u0001"'), ('"é"', '"é"'), ('"\\u00e9"', '"é"'),
    ("-0", "0"), ("[]", "[]"), ("{}", "{}"), ("null", "null"), (str(2**53 - 1), str(2**53 - 1))])
def test_canonical_text(text, canonical):
    assert jsonb_v1.canonical(jsonb_v1.parse(text)) == canonical


@pytest.mark.parametrize("text,why", [
    ('{"a": 1, "a": 2}', "duplicate"), ('{"a": {"b": 1, "b": 1}}', "duplicate"), ("NaN", "not JSON"),
    ("-Infinity", "not JSON"), ("1.0", "non-integer"), ("1e2", "non-integer"), ("1E2", "non-integer"),
    (str(2**53), "exceeds"), (str(-(2**53)), "exceeds"), ('"a\\u0000"', "u0000"), ('"\\ud800"', "surrogate"),
    ("{", "malformed"), ("[1,]", "malformed"), ("", "malformed"), ("1 2", "malformed"),
    ("[" * 40 + "]" * 40, "deeper")])
def test_refusals(text, why):
    with pytest.raises(jsonb_v1.JsonbRefused, match=why):
        jsonb_v1.parse(text)


def test_non_str_and_size_refusals():
    with pytest.raises(jsonb_v1.JsonbRefused, match="must be a str"):
        jsonb_v1.parse({"a": 1})
    with pytest.raises(jsonb_v1.JsonbRefused, match="longer"):
        jsonb_v1.parse('"' + "x" * (64 * 1024) + '"')


def test_equality_is_type_exact():
    assert jsonb_v1.equal({"a": [1, True, None]}, {"a": [1, True, None]})
    assert not jsonb_v1.equal(1, True) and not jsonb_v1.equal(0, False) and not jsonb_v1.equal(1, 1.0)
    assert not jsonb_v1.equal(None, "null") and not jsonb_v1.equal({"a": 1}, {"a": 1, "b": 2})
    assert not jsonb_v1.equal([1, 2], [2, 1])


def test_pg_normalization_disagreement_is_refused():
    assert jsonb_v1.accept('{"b": 1, "a": 2}', '{"a": 2, "b": 1}') == '{"a": 2, "b": 1}'
    with pytest.raises(jsonb_v1.JsonbRefused, match="normalizes"):
        jsonb_v1.accept('{"a": 2}', '{"a": 2.0}')


def test_readback_distinguishes_sql_null_from_json_null():
    assert jsonb_v1.readback(None, True, None, None) == []
    assert jsonb_v1.readback("null", False, "null", "null") == []
    assert jsonb_v1.readback(None, False, "null", "null")  # JSON null where SQL NULL was requested
    assert jsonb_v1.readback("null", True, None, None)  # SQL NULL where JSON null was requested
    assert jsonb_v1.readback('{"n": 1}', False, '{"n": 1.0}', "object")  # a float came back
    assert jsonb_v1.readback('{"n": 1}', False, '{"n": 1}', "array")


# ------------------------------------------------------------------------------------------------ loader, trace

def test_the_substitution_is_jsonb_aware_and_otherwise_original():
    m = loader.load()
    assert m.state_constructor._same is loader.same_v1 and m.seed_feasibility._same is loader.same_v1
    token = loader._JSONB.set({'{"n": 1}': {"n": 1}, "null": None, "true": True})
    try:
        assert loader.same_v1({"n": 1}, '{"n": 1}') and loader.same_v1('{"n": 1}', {"n": 1})
        assert not loader.same_v1({"n": 1.0}, '{"n": 1}') and not loader.same_v1({"n": True}, '{"n": 1}')
        assert not loader.same_v1("true", "true".upper()) and loader.same_v1(True, "true")
        assert not loader.same_v1(1, "true")
        assert loader.same_v1("abc", "abc") and loader.same_v1(5, "5")  # unregistered: the original _same
    finally:
        loader._JSONB.reset(token)
    assert not loader.same_v1({"n": 1}, '{"n": 1}')  # outside a construction nothing is registered: original


def test_ambiguous_registration_is_refused():
    with pytest.raises(loader.ExtensionRefused, match="ambiguous"):
        loader.seed_rows([("t", {"d": "{}", "s": "{}"})], {0: {"d"}})
    with pytest.raises(loader.ExtensionRefused, match="canonical"):
        loader.seed_rows([("t", {"d": '{"b":1}'})], {0: {"d"}})


def test_a_source_that_isnt_the_pinned_one_is_refused(monkeypatch):
    monkeypatch.setattr(loader, "PACKAGE", "deployspecter._phase9b_ext_test_pin")
    monkeypatch.setattr(loader, "PINNED", {**loader.PINNED, "state_constructor.py": "0" * 64})
    with pytest.raises(loader.ExtensionRefused, match="isn't the pinned"):
        loader.load()
    assert not any(k.startswith("deployspecter._phase9b_ext_test_pin") for k in __import__("sys").modules)


def test_the_tracer_refuses_a_bypass_or_the_wrong_instance():
    with trace.traced() as calls:
        pass
    with pytest.raises(trace.TraceRefused, match="did not run"):
        trace.require(calls, jsonb=False, proven=False)
    fake = collections.Counter({n: 1 for n in trace.REQUIRED + trace.REQUIRED_PROVEN})
    with pytest.raises(trace.TraceRefused, match="same_v1"):
        trace.require(fake, jsonb=True, proven=True)
    fake["deployspecter._phase9b.state_constructor.construct_state"] = 1
    fake["deployspecter.experimental.phase9b_ext.loader.same_v1"] = 1
    with pytest.raises(trace.TraceRefused, match="unsubstituted"):
        trace.require(fake, jsonb=True, proven=True)


# ------------------------------------------------------------------------------------------------ the registry

H = "deployspecter.experimental.thirdparty.chancy_adapter.ds_job"


def job(**kw):
    return {"handler": H, "kwargs": {"n": 0}, "queue": "ds_q", "state": "pending", "priority": 0} | kw


def wl(*jobs):
    return {"registry": "chancy-0.26.0/registry-v1", "jobs": list(jobs)}


@pytest.mark.parametrize("w,why", [
    (wl(job(handler="os.system")), "isn't registered"),
    (wl(job(handler=H.replace("ds_job", "ds_flaky"))), "isn't registered"),
    (wl(job(state="running")), "isn't a goal state"), (wl(job(state="bogus")), "isn't a goal state"),
    (wl(job(queue="elsewhere")), "no such parent"), (wl(job(kwargs={"n": 1000})), "an int in"),
    (wl(job(kwargs={"n": True})), "an int in"), (wl(job(kwargs={"n": 1, "x": 2})), "exactly"),
    (wl(job(), job()), "duplicate"), (wl(job(state="succeeded")), "at least one pending"),
    (wl(job(priority=101)), "priority"), ({"jobs": [job()]}, "shape"),
    ({"registry": "other", "jobs": [job()]}, "isn't"), (wl(*[job(kwargs={"n": i}) for i in range(9)]), "1..8"),
    (wl(job() | {"sql": "DROP TABLE x"}), "exactly handler")])
def test_registry_refusals(w, why):
    assert any(why in p for p in reg.validate(w)), reg.validate(w)


def test_registry_rows_are_the_direct_constructors_rows_for_the_same_goals():
    w = wl(job(kwargs={"n": 0}), job(kwargs={"n": 1}), job(kwargs={"n": 7}, state="succeeded"))
    assert reg.validate(w) == []
    mine = reg.rows(w)
    direct = chancy.rows(chancy.goals(reg.to_direct_workload(w)))
    assert [(t, {c: tv.to_dict() for c, tv in v.items()}) for t, v in mine] == \
           [(t, {c: tv.to_dict() for c, tv in v.items()}) for t, v in direct]


def test_application_source_hash_mismatch_is_detected(tmp_path):
    fake = tmp_path / "chancy-0.26.0.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = b"# not chancy\n"
        info = tarfile.TarInfo("chancy-0.26.0/chancy/app.py")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    fake.write_bytes(buf.getvalue())
    r = reg.verify_sources(fake)
    assert not r["ok"] and any("sdist sha256" in p for p in r["problems"]) and \
        any("chancy/app.py" in p for p in r["problems"])


@pytest.mark.skipif(not os.environ.get("DS_THIRDPARTY_SDIST_DIR"), reason="needs the pinned sdists")
def test_the_pinned_sdist_matches_the_registry():
    r = reg.verify_sources(Path(os.environ["DS_THIRDPARTY_SDIST_DIR"]) / "chancy-0.26.0.tar.gz")
    assert r["ok"], r["problems"]
