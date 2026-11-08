"""Offline tests for the ds-nbc/1 pgqueuer integration ``pgqueuer-cancel-sv/1`` and its case oracle (NONBLIND)."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
INTEG = REPO / "research_tools/nbc/integrations/pgqueuer_cancel.py"
CASE = REPO / "docs/experimental_mvp/nonblind_corpus/pgqueuer/cases/pgqueuer-8dfadf4"
FIX_TEXT = Path(__file__).with_name("fixtures") / "nbc_pgqueuer_8dfadf4_fix_text.txt"
FIX = "8dfadf4ea3a6fb6d60ac970d799faf8338f9d03f"


def load(path: Path, name: str) -> Any:
    s = importlib.util.spec_from_file_location(name, path)
    assert s is not None and s.loader is not None
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def pc() -> Any:
    return load(INTEG, "nbc_pgq_cancel_under_test")


@pytest.fixture(scope="module")
def oracle() -> Any:
    return load(CASE / "oracle.py", "nbc_pgq_oracle_under_test")


def test_ra_det_has_exactly_three_valid_workloads_and_other_arms_refuse(pc: Any) -> None:
    ws = pc.workloads("RA-DET")
    assert len(ws) == 3 and len({json.dumps(w, sort_keys=True) for w in ws}) == 3
    assert all(pc.validate(w) == [] for w in ws)
    for arm in ("RA-DET-C", "RA-MD", "RA-MS", "X"):
        with pytest.raises(ValueError):
            pc.workloads(arm)


@pytest.mark.parametrize("bad", [
    None, [], {}, {"jobs": [], "workers": 1, "concurrency_limit": 1, "handler_steps": 0},
    {"jobs": [{"n": 0, "priority": 0, "cancel": True}], "workers": 3, "concurrency_limit": 1, "handler_steps": 0},
    {"jobs": [{"n": 0, "priority": 0, "cancel": True}], "workers": True, "concurrency_limit": 1, "handler_steps": 0},
    {"jobs": [{"n": 0, "priority": 0, "cancel": 1}], "workers": 1, "concurrency_limit": 1, "handler_steps": 0},
    {"jobs": [{"n": 0, "priority": 0, "cancel": True}] * 2, "workers": 1, "concurrency_limit": 1,
     "handler_steps": 0},
    {"jobs": [{"n": 10, "priority": 0, "cancel": True}], "workers": 1, "concurrency_limit": 1, "handler_steps": 0},
    {"jobs": [{"n": 0, "priority": 0, "cancel": True, "sql": "x"}], "workers": 1, "concurrency_limit": 1,
     "handler_steps": 0},
    {"jobs": [{"n": 0, "priority": 0, "cancel": True}], "workers": 1, "concurrency_limit": 3, "handler_steps": 0},
    {"jobs": [{"n": 0, "priority": 0, "cancel": True}], "workers": 1, "concurrency_limit": 1, "handler_steps": 3},
    {"jobs": [{"n": i, "priority": 0, "cancel": False} for i in range(4)], "workers": 1, "concurrency_limit": 1,
     "handler_steps": 0},
    {"jobs": [{"n": 0, "priority": 0, "cancel": True}], "workers": 1, "concurrency_limit": 1, "handler_steps": 0,
     "gates": []},
])
def test_invalid_workloads_are_refused(pc: Any, bad: Any) -> None:
    assert pc.validate(bad)


def walk(schema: dict[str, Any]) -> list[dict[str, Any]]:
    out = [schema]
    for v in schema.get("properties", {}).values():
        out += walk(v)
    if isinstance(schema.get("items"), dict):
        out += walk(schema["items"])
    return out


def test_workload_schema_is_strict(pc: Any) -> None:
    text = json.dumps(pc.WORKLOAD_JSON_SCHEMA)
    assert "oneOf" not in text and "anyOf" not in text and "allOf" not in text
    for node in walk(pc.WORKLOAD_JSON_SCHEMA):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert sorted(node["required"]) == sorted(node["properties"])


def test_the_schema_admits_exactly_what_validate_admits(pc: Any) -> None:
    """Every enum boundary of the schema is valid to ``validate``."""
    for workers in (1, 2):
        for lim in (0, 1, 2):
            for steps in (0, 1, 2):
                w = {"jobs": [{"n": 9, "priority": 9, "cancel": False}], "workers": workers,
                     "concurrency_limit": lim, "handler_steps": steps}
                assert pc.validate(w) == []


def ngrams(text: str, k: int = 5) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9_]+", text.lower())
    return {tuple(words[i:i + k]) for i in range(len(words) - k + 1)}


def test_muse_context_leaks_nothing_of_the_fix(pc: Any) -> None:
    ctx = json.dumps(pc.MUSE_CONTEXT, sort_keys=True)
    assert set(pc.MUSE_CONTEXT) == {"application", "operations", "documented_semantics", "schema_ddl"}
    assert "CREATE TABLE pgqueuer (" in pc.MUSE_CONTEXT["schema_ddl"]
    for s in (FIX, FIX[:7], "1032", "1031", "f258cbc"):
        assert s not in ctx, s
    prose = json.dumps({k: v for k, v in pc.MUSE_CONTEXT.items() if k != "schema_ddl"}).lower()
    for w in ("oracle", r"violat\w*", "defect", r"fix\w*", "bug", "regression", r"test_\w*", "slots?",
              "stays in the queue", "worker logs", "keeps? its"):
        assert not re.search(rf"\b{w}\b", prose), w
    assert re.search(r"\bconcurrency_limit\b", prose)  # the check itself bites
    lines = FIX_TEXT.read_text().splitlines()
    message = [x for x in lines if not x.startswith(("+", "-"))]
    added = [x[1:] for x in lines if x.startswith("+")]
    removed = [x[1:] for x in lines if x.startswith("-")]
    # the parent's own text (removed lines) and its schema are release material, not the fix
    allowed = ngrams(" ".join(removed)) | ngrams(pc.MUSE_CONTEXT["schema_ddl"])
    fix_grams = (ngrams(" ".join(message)) | ngrams(" ".join(added))) - allowed
    assert not (fix_grams & ngrams(ctx)), sorted(fix_grams & ngrams(ctx))[:5]


def test_spec_gates_only_order_meaningful_operations(pc: Any) -> None:
    w = pc.workloads("RA-DET")[2]
    s = pc.spec(w)
    actors = [a["actor"] for a in s["processes"]["A"]]
    assert actors == ["setup", "w1", "w2", "prod"]
    assert s["covers"] == [0, 1, 2]
    gated = {(g["actor"], g["match"]) for g in s["gates"]}
    assert gated == {("w1", None), ("w2", None), ("prod", None), ("prod", pc.LOG_JOB_RX), ("w2", pc.DEQUEUE_RX)}
    prod = next(a for a in s["processes"]["A"] if a["actor"] == "prod")
    assert prod["args"] == {"jobs": [{"n": 0, "priority": 0}, {"n": 1, "priority": 0}, {"n": 2, "priority": 0}],
                            "cancel": [0]}
    one = pc.spec({**w, "workers": 1, "jobs": [{"n": 3, "priority": 1, "cancel": False}]})
    assert [a["actor"] for a in one["processes"]["A"]] == ["setup", "w1", "prod"]
    assert all(g["match"] != pc.LOG_JOB_RX for g in one["gates"])


def tar_of(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def test_declaration_refuses_a_wrong_hash_an_unreviewed_driver_or_a_stray_member(pc: Any, tmp_path: Path) -> None:
    import hashlib
    a = tmp_path / "R.tar"
    a.write_bytes(tar_of({"pgqueuer/adapters/drivers/psycopg.py": b"x", "pgqueuer/adapters/persistence/qb.py": b""}))
    with pytest.raises(ValueError, match="sha256"):
        pc.declaration(a, "0" * 64)
    with pytest.raises(ValueError, match="reviewed"):
        pc.declaration(a, hashlib.sha256(a.read_bytes()).hexdigest())
    b = tmp_path / "S.tar"
    b.write_bytes(tar_of({"pgqueuer/x.py": b"", "etc/passwd": b""}))
    with pytest.raises(ValueError, match="unexpected member"):
        pc.declaration(b, hashlib.sha256(b.read_bytes()).hexdigest())


def test_tree_digest_matches_the_adapters_algorithm(pc: Any, tmp_path: Path) -> None:
    sys.path.insert(0, str(REPO / "src"))
    from deployspecter.experimental.thirdparty import pgqueuer_hist_adapter as ha
    files = {"pgqueuer/adapters.py": b"1", "pgqueuer/adapters/x.py": b"2", "pgqueuer/a b.py": b"3"}
    for name, data in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(data)
    assert ha.tree_digest(tmp_path) == pc.tree_digest(files)


def runs(*rows: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"seq": i + 1, "job_id": int(e.split(":")[1]), "n": int(e.split(":")[1]), "worker": e.split(":")[0],
             "exec": e, "phase": p} for i, (e, p) in enumerate(rows)]


def results(limit: int, workers: int = 2) -> dict[str, Any]:
    r: dict[str, Any] = {f"w{i + 1}": {"worker": f"w{i + 1}", "concurrency_limit": limit} for i in range(workers)}
    r["prod"] = {"actor": "prod", "cancelled": [0]}
    return r


TABLES = ["ds_pgq_runs", "pgqueuer", "pgqueuer_log"]


def test_oracle_holds_for_serial_executions(oracle: Any) -> None:
    fs = {"tables": TABLES, "runs": runs(("w1:0:1", "start"), ("w1:0:1", "step"), ("w1:0:1", "end"),
                                         ("w2:1:1", "start"), ("w2:1:1", "end"))}
    assert oracle.check(fs, results(1)) == "HOLDS"


def test_oracle_is_violated_by_an_overlap_under_limit_one(oracle: Any) -> None:
    fs = {"tables": TABLES, "runs": runs(("w1:0:1", "start"), ("w2:1:1", "start"), ("w1:0:1", "end"),
                                         ("w2:1:1", "end"))}
    assert oracle.check(fs, results(1)) == "VIOLATED"
    assert oracle.explain(fs, results(1))["peak_running"] == 2
    assert oracle.check(fs, results(2)) == "HOLDS"
    assert oracle.check(fs, results(0)) == "HOLDS"  # unlimited


def test_oracle_counts_an_overlap_inside_one_worker(oracle: Any) -> None:
    fs = {"tables": TABLES, "runs": runs(("w1:0:1", "start"), ("w1:1:2", "start"), ("w1:1:2", "end"),
                                         ("w1:0:1", "end"))}
    assert oracle.check(fs, results(1, workers=1)) == "VIOLATED"


@pytest.mark.parametrize("fs,res", [
    ({"tables": ["pgqueuer"], "runs": []}, results(1)),
    ({"tables": TABLES, "runs": []}, results(1)),
    ({"tables": TABLES, "runs": runs(("w1:0:1", "start"))}, results(1)),
    ({"tables": TABLES, "runs": runs(("w1:0:1", "start"), ("w1:0:1", "end"))},
     {"w1": {"worker": "w1", "concurrency_limit": 1}, "w2": {"worker": "w2", "concurrency_limit": 2}}),
])
def test_oracle_gives_no_verdict_on_incomplete_evidence(oracle: Any, fs: dict[str, Any], res: dict[str, Any]) -> None:
    assert oracle.check(fs, res) == "NO_VERDICT"


def test_the_oracle_does_not_mention_the_fix_code() -> None:
    text = (CASE / "oracle.py").read_text()
    for s in ("q.status <> 'picked'", "queue_manager_id IS NULL AND", "js.queue_manager_id", "jobs.py", "cancel("):
        assert s not in text


def test_the_case_spec_matches_the_oracle_and_integration() -> None:
    import hashlib
    spec = json.loads((CASE / "case_spec.json").read_text())
    assert spec["label"] == "NONBLIND development"
    assert spec["oracle"]["sha256"] == hashlib.sha256((REPO / spec["oracle"]["path"]).read_bytes()).hexdigest()
    assert (REPO / spec["integration"]).resolve() == INTEG.resolve()
    assert spec["upstream"]["fix_commit"] == FIX and spec["buggy"] == "R1"
    blob = json.dumps(spec)
    assert "BLIND\"" not in blob and "confirmatory\"" not in blob.lower()
