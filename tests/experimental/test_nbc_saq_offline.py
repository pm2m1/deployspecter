"""Offline tests of the ds-nbc/1 saq integrations (NONBLIND development): interface, frozen workloads, schema
strictness, the MUSE_CONTEXT leak check, run specs, the case oracles and the pins. No database, container or model."""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1].parent
sys.path.insert(0, str(ROOT / "research_tools" / "nbc" / "integrations"))
sys.path.insert(0, str(ROOT / "research_tools" / "nbc"))

import saq_burst_ad8d1c8 as burst_a  # noqa: E402
import saq_burst_c570ba4 as burst_b  # noqa: E402
import saq_common as sc  # noqa: E402
import saq_shapes as sh  # noqa: E402
import saq_two_queues_addee31 as twoq  # noqa: E402

MODULES = {"saq-ad8d1c8": burst_a, "saq-c570ba4": burst_b, "saq-addee31": twoq}
CASES = ROOT / "docs/experimental_mvp/nonblind_corpus/saq/cases"
FIX_WORDS = json.loads((ROOT / "tests/experimental/fixtures/saq_nbc_fix_words.json").read_text())["cases"]
# reviewed overlap of each fix's words with its MUSE_CONTEXT: saq's public API names, its docstrings' words, and
# generic English. Any other fix word appearing in the context fails the leak check.
ALLOWED_OVERLAP = {
    "saq-ad8d1c8": {"active", "async", "burst", "concurrency", "connection", "dequeue", "dequeues", "else", "from",
                    "instead", "jobs", "max_burst_jobs", "mode", "none", "only", "poll", "pool", "postgres", "queue",
                    "runs", "schedule", "start", "status", "sweep", "this", "timeout", "timers", "true", "version",
                    "with", "worker"},
    "saq-c570ba4": {"active", "async", "burst", "dequeue", "dequeue_timeout", "instead", "none", "once", "postgres",
                    "processed", "queue", "queued", "schedule", "stops", "task", "then", "this", "timeout", "true",
                    "wait", "when", "with", "worker"},
    "saq-addee31": {"connection", "failed", "from", "migrations", "other", "queue", "schema", "then", "with"},
}
ALL_FIX_HASHES = [v["fix_commit"] for v in FIX_WORDS.values()]


def words(obj: object) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", json.dumps(obj))}


@pytest.mark.parametrize("case", sorted(MODULES))
def test_exactly_three_valid_ra_det_workloads(case):
    m = MODULES[case]
    ws = m.workloads("RA-DET")
    assert len(ws) == 3 and len({json.dumps(w, sort_keys=True) for w in ws}) == 3
    assert all(m.validate(w) == [] for w in ws)
    for arm in ("RA-MD", "RA-DET-C", "RA-MS", "x"):
        with pytest.raises(ValueError):
            m.workloads(arm)


@pytest.mark.parametrize("case", sorted(MODULES))
def test_workload_schema_is_strict(case):
    assert sc.strict(MODULES[case].WORKLOAD_JSON_SCHEMA) == []


def test_strictness_checker_catches_problems():
    assert sc.strict({"type": "object", "properties": {"a": {"type": "integer"}}, "required": []})
    assert sc.strict({"oneOf": [{"type": "integer"}]})


@pytest.mark.parametrize("case", sorted(MODULES))
def test_muse_context_leaks_no_fix_material(case):
    m = MODULES[case]
    text = json.dumps(m.MUSE_CONTEXT)
    assert set(m.MUSE_CONTEXT) == {"application", "registry", "schema_ddl"}
    for h in ALL_FIX_HASHES:
        assert h not in text and h[:7] not in text
    leaked = (words(m.MUSE_CONTEXT) & set(FIX_WORDS[case]["words"])) - ALLOWED_OVERLAP[case]
    assert not leaked, leaked
    for w in ("violated", "holds", "oracle", "stranded", "buffer", "advisory", "xact", "bug", "defect", "fix",
              "regression", "race", "test_"):
        assert w not in text.lower(), w


def test_burst_validation_refuses():
    good = dict(burst_a.RA_DET[0])
    assert burst_a.validate(good) == []
    for k, v in (("jobs", 0), ("jobs", 5), ("jobs", True), ("concurrency", 3), ("max_burst_jobs", -1),
                 ("dequeue_timeout_s", 0.3), ("poll_interval_s", 2.0), ("schedule_s", 2)):
        assert burst_a.validate({**good, k: v}), (k, v)
    assert burst_a.validate({**good, "sql": "x"})
    assert burst_a.validate([good])


def test_two_queues_validation_refuses():
    good = json.loads(json.dumps(twoq.RA_DET[0]))
    assert twoq.validate(good) == []
    assert twoq.validate({**good, "first": {"tables": "other", "jobs": 0}})
    assert twoq.validate({**good, "second": {"tables": "alt", "jobs": 0}})
    assert twoq.validate({**good, "poll_interval_s": 0.1})
    assert twoq.validate({**good, "extra": 1})


def test_specs_gate_and_cover():
    s = sh.burst_spec(burst_a.RA_DET[0])
    assert [a["actor"] for a in s["processes"]["A"]] == ["producer", "worker"]
    assert s["gates"] == [{"actor": "worker", "match": None,
                           "until": [{"actor": "producer", "marker": "insert", "count": 3}]}]
    assert s["covers"] == ["producer", "worker"]
    assert s["processes"]["A"][1]["args"]["max_burst_jobs"] == 1
    assert sh.burst_spec(burst_a.RA_DET[2])["processes"]["A"][1]["args"]["max_burst_jobs"] is None
    t = sh.two_queues_spec(twoq.RA_DET[0])
    assert t["gates"][0]["actor"] == "second" and t["gates"][0]["until"][0]["marker"] == "fetch"
    assert t["covers"] == ["second"]
    assert t["processes"]["A"][1]["args"]["ns"] == [10]


def test_markers_match_saq_statements():
    rx = {k: re.compile(v["regex"]) for k, v in sc.MARKERS.items()}
    assert rx["insert"].search('INSERT INTO "saq_jobs" (\n key')
    assert rx["insert"].search('INSERT INTO "saq_alt_jobs" (')
    assert not rx["insert"].search('INSERT INTO "saq_stats" (')
    assert rx["fetch"].search("WITH locked_job AS (\n SELECT key")


def _oracle(case: str):
    p = CASES / case / "oracle.py"
    return sc.load_oracle(p, hashlib.sha256(p.read_bytes()).hexdigest())


def _burst(statuses: dict[str, str], handled: list[int], cap=None, stopped="burst"):
    keys = sorted(statuses)
    final = {"tables": ["saq_jobs"], "jobs": {"saq_jobs": [
        {"key": k, "queue": "ds_q", "status": s, "attempts": 1, "result": int(k.split("-")[1]) * 2
         if s == "complete" else None, "error": None} for k, s in statuses.items()]}}
    raw = {"producer": {"tables": "default", "ns": [int(k.split("-")[1]) for k in keys], "connected": True,
                        "enqueued": keys, "db_error": None},
           "worker": {"tables": "default", "max_burst_jobs": cap, "stopped_by": stopped, "db_error": None,
                      "handled": [["default", n] for n in handled]}}
    return final, raw


@pytest.mark.parametrize("case", ["saq-ad8d1c8", "saq-c570ba4"])
def test_burst_oracle(case):
    o = _oracle(case)
    assert o.check(*_burst({"default-0": "complete", "default-1": "queued", "default-2": "queued"}, [0], 1)) == "HOLDS"
    assert o.check(*_burst({"default-0": "complete", "default-1": "complete"}, [0, 1])) == "HOLDS"
    assert o.check(*_burst({"default-0": "queued"}, [])) == "HOLDS"  # a timed-out burst worker that took nothing
    assert o.check(*_burst({"default-0": "complete", "default-1": "active", "default-2": "queued"}, [0], 1)) \
        == "VIOLATED"
    assert o.check(*_burst({"default-0": "active"}, [])) == "VIOLATED"
    assert o.check(*_burst({"default-0": "complete", "default-1": "complete"}, [0, 1], 1)) == "VIOLATED"
    assert o.check(*_burst({"default-0": "complete"}, [0, 0])) == "VIOLATED"
    assert o.check(*_burst({"default-0": "queued"}, [0])) == "VIOLATED"
    assert o.check(*_burst({"default-0": "active"}, [], stopped="guard_timeout")) == "NO_VERDICT"
    f, r = _burst({"default-0": "complete"}, [0])
    r["producer"]["db_error"] = "UndefinedTable: x"
    assert o.check(f, r) == "NO_VERDICT"


def test_two_queues_oracle():
    o = _oracle("saq-addee31")
    first = {"tables": "default", "ns": [0], "connected": True, "enqueued": ["default-0"], "db_error": None}
    second = {"tables": "alt", "ns": [10], "connected": True, "enqueued": ["alt-10"], "db_error": None}
    final = {"tables": ["saq_alt_jobs", "saq_jobs"], "jobs": {"saq_jobs": [{"key": "default-0"}],
                                                              "saq_alt_jobs": [{"key": "alt-10"}]}}
    assert o.check(final, {"first": first, "second": second}) == "HOLDS"
    bad = {**second, "enqueued": [], "db_error": 'UndefinedTable: relation "saq_alt_jobs" does not exist'}
    assert o.check({"tables": ["saq_jobs"], "jobs": {"saq_jobs": [{"key": "default-0"}]}},
                   {"first": first, "second": bad}) == "VIOLATED"
    assert o.check({**final, "jobs": {"saq_jobs": [{"key": "default-0"}]}}, {"first": first, "second": second}) \
        == "VIOLATED"
    assert o.check(final, {"first": {**first, "connected": False}, "second": second}) == "NO_VERDICT"


def test_prepare_refuses_unpinned_trees(tmp_path):
    a = tmp_path / "x.tar"
    a.write_bytes(b"not a pinned tree")
    for m in MODULES.values():
        with pytest.raises(ValueError):
            m.prepare(a, hashlib.sha256(a.read_bytes()).hexdigest(), tmp_path / "w")


def test_pins_agree_across_integrations_specs_and_pin_script():
    import pin_saq
    for case, m in MODULES.items():
        spec = json.loads((CASES / case / "case_spec.json").read_text())
        assert spec["integration"] == f"research_tools/nbc/integrations/{Path(m.__file__).name}"
        assert spec["label"] == "NONBLIND development" and spec["buggy"] == "R1"
        for slot in ("R1", "R2"):
            commit, _tree, digest = pin_saq.PINS[case][slot]
            assert spec[slot]["sha256"] == digest and digest in m.REVS
            assert m.REVS[digest] == f"git-{commit[:7]}"
            assert spec[slot]["archive"].endswith(f"/nbc_archives/saq/{case}/{slot}.tar")
        assert spec["upstream"]["fix_commit"] == pin_saq.PINS[case]["R2"][0] == FIX_WORDS[case]["fix_commit"]
        op = ROOT / spec["oracle"]["path"]
        assert hashlib.sha256(op.read_bytes()).hexdigest() == spec["oracle"]["sha256"]


def test_archive_sources_reads_data_only(tmp_path):
    t = tmp_path / "t.tar"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for rel in sc.EXERCISED:
            data = rel.encode()
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    t.write_bytes(buf.getvalue())
    got = sc.archive_sources(t)
    assert got == {rel: hashlib.sha256(rel.encode()).hexdigest() for rel in sc.EXERCISED}


def test_adapter_refuses_without_declared_pins(monkeypatch):
    from deployspecter.experimental.stepping import Refused
    from deployspecter.experimental.thirdparty import saq_coord_adapter as sa
    monkeypatch.delenv("DS_SAQ_REV", raising=False)
    with pytest.raises(Refused):
        sa.installed_version()
    assert set(sa.FUNCTIONS) == {"enqueue", "burst_work", "run_worker"}
    assert sa.EXERCISED == sc.EXERCISED
