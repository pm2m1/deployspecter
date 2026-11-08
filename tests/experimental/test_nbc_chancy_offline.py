"""Offline tests of the ds-nbc/1 chancy integrations, oracles and case specs (NONBLIND development).
No database, container or model: the live reproduction runs only through research_tools/pg_lock.py."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "docs" / "experimental_mvp" / "nonblind_corpus" / "chancy" / "cases"
PINS = json.loads((CASES.parent / "PINS.json").read_text())
sys.path.insert(0, str(ROOT / "research_tools" / "nbc"))

# Words of each fix's commit message and diff hunks that occur nowhere in its R1 package tree (lower-cased, 4+
# characters; derived once from the public mirror, frozen here). MUSE_CONTEXT may contain none of them.
FIX_ONLY_WORDS = {
    "chancy-2895c4a": """__new__ _current_execution _handle_recovery accidental acknowledgement acks_late alone assert
        assert_awaited_once assert_awaited_once_with assert_called_once assert_not_awaited asyncmock await_args
        await_args_list barrier broken brokenpipeerror call_args call_args_list cancel_execution cancels caplog
        chancy_just_app check_cancel_requested claim claim_id claimed claims cleared coexist crash delayed determines
        discarded eoferror execution_id executor_class exercise fence fencing guarantee harmless idempotent
        identified identifies identify inaccessible invalidated invalidates invoke invoking jobclaims late
        make_future matched merely mock monkeypatch moved nextset old_claim omitted oserror overlap overwrite
        overwriting ownership parametrize preventing previous_mask pthread_sigmask pytest receive_process_signal
        reclaim reconnection registration replacement requesting requeues respect restore revision rollback
        set_running_or_notify_cancel setattr side_effect sig_block sig_setmask simulate stop_after
        subsequent_handler survives test transactional unavailable unittest unreachable unrelated unsafe
        unsuccessful unused use_cursor wait_until_cancelled wakes worker_no_start wrap_future""".split(),
    "chancy-b8a77df": """_in_lock_order _push_all acquire assert aude audecasteigts authored batched by_id casteigts
        deadlock deadlock_timeout deadlockdetected deadlocks detects deterministically failing gmail grouped
        inconsistent interleaving killed kills lock_order_key lock_row locking matter opposite ordering overlap
        overlapped overlapping pair pairs paths pytest regression regression_89_a regression_89_b
        regression_89_sync_a regression_89_sync_b regression_89_sync_c replays several sorts submits sync_chancy
        takes test test_regression_89 test_regression_89_sync_push_preserves_order touching unique_job""".split(),
}
FIX_COMMITS = {"chancy-2895c4a": "2895c4a7fc173cec3d645221f63c4e51e2cf7e84",
               "chancy-b8a77df": "b8a77df3a514ce19ebcaf31453cff8da8af9f212"}


def load(path: Path) -> Any:
    s = importlib.util.spec_from_file_location(f"t_{path.stem}_{hashlib.sha256(path.read_bytes()).hexdigest()[:8]}",
                                               path)
    m = importlib.util.module_from_spec(s)
    assert s.loader is not None
    sys.modules[s.name] = m
    s.loader.exec_module(m)
    return m


def spec_of(cid: str) -> dict[str, Any]:
    return json.loads((CASES / cid / "case_spec.json").read_text())


def strict_problems(schema: Any, where: str = "$") -> list[str]:
    """Strict structured output: every object closes its properties and requires all of them; no oneOf/anyOf."""
    p = []
    if isinstance(schema, dict):
        for bad in ("oneOf", "anyOf", "allOf", "not"):
            if bad in schema:
                p.append(f"{where}: {bad}")
        if schema.get("type") == "object":
            if schema.get("additionalProperties") is not False:
                p.append(f"{where}: additionalProperties isn't false")
            if set(schema.get("required", [])) != set(schema.get("properties", {})):
                p.append(f"{where}: required doesn't list every property")
        for k, v in schema.get("properties", {}).items():
            p += strict_problems(v, f"{where}.{k}")
        if "items" in schema:
            p += strict_problems(schema["items"], f"{where}[]")
    return p


def conforms(v: Any, s: dict[str, Any]) -> bool:
    """The schema subset these integrations use."""
    t = s.get("type")
    if t == "object":
        if not isinstance(v, dict) or set(v) - set(s["properties"]) or set(s["required"]) - set(v):
            return False
        return all(conforms(v[k], s["properties"][k]) for k in v)
    if t == "array":
        return isinstance(v, list) and s.get("minItems", 0) <= len(v) <= s.get("maxItems", 10 ** 9) and \
            all(conforms(x, s["items"]) for x in v)
    if t == "integer":
        ok = isinstance(v, int) and not isinstance(v, bool)
        ok = ok and s.get("minimum", -10 ** 18) <= v <= s.get("maximum", 10 ** 18)
        return ok and ("enum" not in s or v in s["enum"])
    if t == "boolean":
        return isinstance(v, bool)
    if t == "string":
        return isinstance(v, str) and ("enum" not in s or v in s["enum"])
    raise AssertionError(f"unexpected schema type {t}")


def words(obj: Any) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", json.dumps(obj))}


# --------------------------------------------------------------------------------------------- case chancy-2895c4a


@pytest.fixture(scope="module")
def sv1() -> Any:
    return load(ROOT / spec_of("chancy-2895c4a")["integration"])


def test_sv1_reexports_the_pinned_chancy_sv(sv1):
    base = ROOT / "research_tools/confirmatory/integrations/chancy_sv.py"
    assert hashlib.sha256(base.read_bytes()).hexdigest() == sv1.BASE_SHA256
    assert sv1.workloads("RA-DET") == sv1._base.workloads("RA-DET")
    assert sv1.run is sv1._base.run and sv1.prepare is sv1._base.prepare and sv1.validate is sv1._base.validate


def test_sv1_workloads_are_three_valid_and_schema_conformant(sv1):
    ws = sv1.workloads("RA-DET")
    assert len(ws) == 3
    assert len({json.dumps(w, sort_keys=True) for w in ws}) == 3
    for w in ws:
        assert sv1.validate(w) == []
        assert conforms(w, sv1.WORKLOAD_JSON_SCHEMA)
    for arm in ("RA-MD", "RA-DET-C", "RA-MS", "anything"):
        with pytest.raises(ValueError):
            sv1.workloads(arm)


def test_sv1_schema_is_strict_and_agrees_with_validate(sv1):
    assert strict_problems(sv1.WORKLOAD_JSON_SCHEMA) == []
    samples = [{"jobs": [{"n": n, "cancel": c}], "max_attempts": a} for n in (-1, 0, 99, 100) for c in (True, False)
               for a in (0, 1, 2, 3)]
    samples += [{"jobs": [], "max_attempts": 1}, {"jobs": [{"n": 1, "cancel": True}] * 3, "max_attempts": 1},
                {"jobs": [{"n": 1, "cancel": True}], "max_attempts": 1, "x": 1}]
    for w in samples:
        assert conforms(w, sv1.WORKLOAD_JSON_SCHEMA) == (sv1.validate(w) == []), w


def test_sv1_muse_context_leaks_nothing_from_the_fix(sv1):
    ctx = sv1.MUSE_CONTEXT
    text = json.dumps(ctx)
    fix = FIX_COMMITS["chancy-2895c4a"]
    assert not any(fix[:k] in text for k in range(7, 41))
    assert not (words(ctx) & set(FIX_ONLY_WORDS["chancy-2895c4a"])), words(ctx) & set(FIX_ONLY_WORDS["chancy-2895c4a"])
    for hint in ("oracle", "violat", "bug", "defect", "regression", "race", "2895c4a", "0.26"):
        assert hint not in text.lower(), hint
    assert set(ctx) == {"application", "operations", "schema_ddl", "schema_note"}


def test_sv1_schema_ddl_is_the_pinned_r1_archives_own():
    r1 = Path(PINS["chancy-2895c4a"]["R1"]["archive"])
    if not r1.exists():
        pytest.skip("the pinned R1 archive isn't on this machine (research_tools/nbc/pin_chancy.py makes it)")
    import chancy_schema_ddl
    assert hashlib.sha256(r1.read_bytes()).hexdigest() == PINS["chancy-2895c4a"]["R1"]["sha256"]
    sv1 = load(ROOT / spec_of("chancy-2895c4a")["integration"])
    assert chancy_schema_ddl.ddl_from_archive(r1) == sv1.R1_SCHEMA_DDL


def _final(state: str, *, claim: bool | None, completed: bool) -> dict[str, Any]:
    row = {"id": "x", "n": 0, "state": state, "attempts": 1, "has_claim_id": claim, "completed": completed}
    jobs = [{"id": "x", "state": state, **({"has_claim_id": claim} if claim is not None else {})}]
    return {"jobs": jobs, "jobs_detail": [row]}


RESULTS = {"push": {"pushed": [{"n": 0, "id": "x"}], "cancelled": [{"n": 0, "rowcount": 1}]},
           "worker": {"handled": [0], "succeeded": [0], "worker_stopped": True}}


def test_2895c4a_oracle_verdicts():
    sp = spec_of("chancy-2895c4a")
    op = ROOT / sp["oracle"]["path"]
    assert hashlib.sha256(op.read_bytes()).hexdigest() == sp["oracle"]["sha256"]
    o = load(op)
    assert o.check(_final("succeeded", claim=None, completed=True), RESULTS) == "VIOLATED"  # R1 shape, H1
    assert o.check(_final("failed", claim=None, completed=False), RESULTS) == "HOLDS"  # R1 shape, H2 n/a
    assert o.check(_final("failed", claim=False, completed=True), RESULTS) == "HOLDS"  # R2 shape
    assert o.check(_final("failed", claim=True, completed=True), RESULTS) == "VIOLATED"  # R2 shape, H2
    twice = {**RESULTS, "worker": {**RESULTS["worker"], "handled": [0, 0]}}
    assert o.check(_final("succeeded", claim=None, completed=True), twice) == "NO_VERDICT"  # H0
    none = {**RESULTS, "push": {**RESULTS["push"], "cancelled": [{"n": 0, "rowcount": 0}]}}
    assert o.check(_final("succeeded", claim=None, completed=True), none) == "NO_VERDICT"
    assert o.check({"jobs": [], "jobs_detail": []}, RESULTS) == "NO_VERDICT"


@pytest.mark.parametrize("cid", sorted(p.name for p in CASES.iterdir() if (p / "case_spec.json").exists()))
def test_case_specs_are_complete_and_pinned(cid):
    sp = spec_of(cid)
    assert sp["case_id"] == cid and sp["application"] == "chancy" and sp["buggy"] == "R1"
    assert sp["label"] == "NONBLIND development"
    for k in ("upstream", "contract", "exposure"):
        assert sp[k], k
    assert sp["upstream"]["fix_commit"] == PINS[cid]["R2"]["commit"]
    assert sp["upstream"]["parent"] == PINS[cid]["R1"]["commit"]
    for slot in ("R1", "R2"):
        assert sp[slot]["sha256"] == PINS[cid][slot]["sha256"]
        assert sp[slot]["archive"].startswith("/") and sp[slot]["archive"].endswith(f"/{cid}/{slot}.tar")
    assert not Path(sp["integration"]).is_absolute() and (ROOT / sp["integration"]).exists()
    assert not Path(sp["oracle"]["path"]).is_absolute()
    assert hashlib.sha256((ROOT / sp["oracle"]["path"]).read_bytes()).hexdigest() == sp["oracle"]["sha256"]
    assert (CASES / cid / "CASE.md").exists()
    text = json.dumps(sp).lower()
    for bad in ("blind\"", "confirmatory evidence", "independent evidence"):
        assert bad not in text


# --------------------------------------------------------------------------------------------- case chancy-b8a77df


@pytest.fixture(scope="module")
def pm() -> Any:
    return load(ROOT / spec_of("chancy-b8a77df")["integration"])


def test_pushmany_workloads_are_three_valid_and_schema_conformant(pm):
    ws = pm.workloads("RA-DET")
    assert len(ws) == 3 and len({json.dumps(w, sort_keys=True) for w in ws}) == 3
    for w in ws:
        assert pm.validate(w) == [] and conforms(w, pm.WORKLOAD_JSON_SCHEMA)
    for arm in ("RA-MD", "RA-DET-C", "anything"):
        with pytest.raises(ValueError):
            pm.workloads(arm)


def test_pushmany_schema_is_strict_and_agrees_with_validate(pm):
    assert strict_problems(pm.WORKLOAD_JSON_SCHEMA) == []
    samples = [{"seed": s, "repush": r} for s in ([], [0], [0, 1, 2], [0, 1, 2, 3], [10], [-1])
               for r in ([], [9], [0, 1, 2, 3, 4, 5], [0, 1, 2, 3, 4, 5, 6], [True])]
    samples += [{"seed": [0]}, {"seed": [0], "repush": [1], "x": 0}]
    for w in samples:
        assert conforms(w, pm.WORKLOAD_JSON_SCHEMA) == (pm.validate(w) == []), w
    # distinctness is the validator's alone (no uniqueItems in strict structured output): such proposals are
    # counted as invalid by the executor
    assert pm.validate({"seed": [0, 0], "repush": [1]}) and conforms({"seed": [0, 0], "repush": [1]},
                                                                      pm.WORKLOAD_JSON_SCHEMA)


def test_pushmany_spec_gates_and_actors(pm):
    s = pm.spec({"seed": [0, 1], "repush": [1, 0]})
    (actors,) = s["processes"].values()
    assert [a["fn"] for a in actors] == ["nbc_work", "nbc_push_many", "nbc_push_many"]
    assert s["covers"] == [0, 1]
    repush_gates = [g for g in s["gates"] if g["actor"] == "repush"]
    assert {c["marker"] for g in repush_gates for c in g["until"]} == {"fetch", "claim"}


def test_pushmany_muse_context_leaks_nothing_from_the_fix(pm):
    ctx = pm.MUSE_CONTEXT
    text = json.dumps(ctx)
    fix = FIX_COMMITS["chancy-b8a77df"]
    assert not any(fix[:k] in text for k in range(7, 41))
    leaked = words(ctx) & set(FIX_ONLY_WORDS["chancy-b8a77df"])
    assert not leaked, leaked
    for hint in ("oracle", "violat", "bug", "defect", "#89", "lock", "order of rows", "sort", "0.26"):
        assert hint not in text.lower(), hint
    assert set(ctx) == {"application", "operations", "schema_ddl", "schema_note"}


def test_pushmany_schema_ddl_and_adapter_pins_are_the_pinned_archives_own(pm):
    import tarfile
    arch = {s: Path(PINS["chancy-b8a77df"][s]["archive"]) for s in ("R1", "R2")}
    if not all(a.exists() for a in arch.values()):
        pytest.skip("the pinned archives aren't on this machine (research_tools/nbc/pin_chancy.py makes them)")
    import chancy_schema_ddl
    assert chancy_schema_ddl.ddl_from_archive(arch["R1"]) == pm.R1_SCHEMA_DDL
    src = (ROOT / "src/deployspecter/experimental/thirdparty/chancy_adapter_nbc.py").read_text()
    for slot, version in (("R1", "git-6716e0d"), ("R2", "git-b8a77df")):
        with tarfile.open(arch[slot]) as t:
            for rel in ("chancy/app.py", "chancy/worker.py", "chancy/migrate.py", "chancy/executors/asyncex.py"):
                h = hashlib.sha256(t.extractfile(rel).read()).hexdigest()
                assert f'"{rel}": "{h}"' in src, (version, rel)


def test_smoke_inside_variant_selection_is_opt_in(monkeypatch):
    si = load(ROOT / "research_tools/isolated/smoke_inside.py")
    monkeypatch.delenv("DS_CHANCY_ADAPTER", raising=False)
    assert si._variant_functions("PLAN_FUNCTIONS") == {}
    monkeypatch.setenv("DS_CHANCY_ADAPTER", "os")
    with pytest.raises(SystemExit):
        si._variant_functions("PLAN_FUNCTIONS")
    assert si.VARIANT_ADAPTERS == ("chancy_adapter_nbc",)


def _pm_results(*, worker_errors=(), repush_error=None, repush_refs=None):
    def push(ns, ids, err=None):
        return {"requested": [{"n": n, "unique_key": f"u{n}"} for n in ns], "error": err, "op_errors": [],
                "refs": [] if err else [{"n": n, "id": i} for n, i in zip(ns, ids, strict=True)]}
    return {"worker": {"handled": [0, 1], "succeeded": [0, 1], "op_errors": list(worker_errors)},
            "seed": push([0, 1], ["a", "b"]),
            "repush": push([1, 0], repush_refs or ["b", "a"], repush_error)}


FINAL = {"jobs": [{"id": "a"}, {"id": "b"}],
         "jobs_detail": [{"id": "a", "n": 0, "state": "succeeded"}, {"id": "b", "n": 1, "state": "succeeded"}]}


def test_b8a77df_oracle_verdicts():
    sp = spec_of("chancy-b8a77df")
    op = ROOT / sp["oracle"]["path"]
    assert hashlib.sha256(op.read_bytes()).hexdigest() == sp["oracle"]["sha256"]
    o = load(op)
    assert o.check(FINAL, _pm_results()) == "HOLDS"
    dl = {"seq": 9, "kind": "executemany", "sqlstate": "40P01", "error_class": "DeadlockDetected", "sql_head": "UPDATE"}
    assert o.check(FINAL, _pm_results(worker_errors=[dl])) == "VIOLATED"  # the worker's batch was the victim
    assert o.check(FINAL, _pm_results(repush_error={"class": "DeadlockDetected", "sqlstate": "40P01"})) == "VIOLATED"
    assert o.check(FINAL, _pm_results(repush_refs=["a", "b"])) == "VIOLATED"  # D2: references out of order
    other = {"class": "UniqueViolation", "sqlstate": "23505"}
    assert o.check(FINAL, _pm_results(repush_error=other)) == "NO_VERDICT"
    assert o.check(FINAL, {"worker": {}, "seed": {}}) == "NO_VERDICT"
    assert o.check({"jobs": [], "jobs_detail": []}, _pm_results()) == "NO_VERDICT"
    lock_wait_only = {"seq": 3, "kind": "execute", "sqlstate": "57014", "error_class": "QueryCanceled", "sql_head": "x"}
    assert o.check(FINAL, _pm_results(worker_errors=[lock_wait_only])) == "HOLDS"  # D1 is about 40P01 only
