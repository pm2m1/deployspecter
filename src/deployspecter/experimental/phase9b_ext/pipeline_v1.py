"""EXPERIMENTAL ds-p9x/1: construct a typed state in a live target schema through the Phase 9B pipeline.

    export the target's catalog → screen its triggers → rebuild it in Phase 9B's sandbox and compare fingerprints
    → typed checks → Phase 9B construct_state (extension instance, traced) → PROVEN only → physical plan + hash
    → locked, fingerprint-checked typed writes on the target → change == proven final state → commit
    → independent readback

Phase 9B is the only constructor here. There is no other path to fall back to: every stage that can't proceed
returns its refusal (``status`` ``refused_<stage>``) with the reason and the constructor's own classification, and
nothing is written to the target.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from deployspecter.experimental.state_construction import phase9b, typed
from deployspecter.experimental.state_construction.typed import TV, TypedRefused

from . import catalog, execute, expr_equiv, jsonb_v1, loader, trace


@dataclass
class Outcome:
    status: str
    stage: str | None = None
    detail: Any = None
    classification: str | None = None
    export: dict[str, Any] | None = None
    triggers: dict[str, Any] | None = None
    fingerprint_sha256: str | None = None
    plan: dict[str, Any] | None = None
    trace: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    write: dict[str, Any] | None = None
    readback: dict[str, Any] | None = None
    identity: dict[str, Any] = field(default_factory=loader.identity)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("write"):
            d["write"] = {k: v for k, v in d["write"].items() if k != "baseline"} | {
                "baseline_rows": {t: len(v) for t, v in d["write"]["baseline"].items()}}
        return d


def construct_on_target(target: Any, sandbox: Any, schema: str, rows: list[tuple[str, dict[str, TV]]], *,
                        on_insert: Any = None, before_write: Any = None) -> Outcome:
    """``rows``: the requested typed rows. ``before_write`` and ``on_insert`` are test hooks (a catalog change between
    planning and execution, an interruption mid-write)."""
    admin = target.admin()
    try:
        try:
            exp = catalog.export(admin, schema)
        except catalog.CatalogRefused as exc:
            return Outcome("refused_catalog", "export", str(exc))
        screen = catalog.screen_triggers(admin, schema)
        target_fp = catalog.fingerprint(admin, schema)
        if screen["refused"]:
            return Outcome("refused_triggers", "screen", screen["refused"], export=_exp(exp), triggers=screen,
                           fingerprint_sha256=target_fp["sha256"])
        cols_by_table = {t: typed.columns(admin, schema, t) for t in {t for t, _ in rows}}
    finally:
        admin.close()
    out = Outcome("planning", export=_exp(exp), triggers=screen, fingerprint_sha256=target_fp["sha256"])
    # typed checks, before any construction
    jsonb_cols: dict[int, set[str]] = {}
    problems = []
    for i, (t, vals) in enumerate(rows):
        cols = cols_by_table.get(t) or {}
        if not cols:
            problems.append(f"row {i}: no table {t} in {schema}")
            continue
        for c, tv in vals.items():
            col = cols.get(c)
            if col is None:
                problems.append(f"row {i}: no column {t}.{c}")
                continue
            if col["data_type"] == "json":
                problems.append(f"row {i} {t}.{c}: json (not jsonb) columns are outside jsonb/1")
                continue
            try:
                if tv.tag == "jsonb" and jsonb_v1.accept(tv.value) != tv.value:  # jsonb/1 first: precise reasons
                    raise jsonb_v1.JsonbRefused("jsonb text isn't in canonical jsonb/1 form")
                typed.check_value(tv, col)
                if col["data_type"] == "jsonb" and tv.tag not in ("jsonb", "null"):
                    raise TypedRefused(f"a {tv.tag} value for a jsonb column")
            except (TypedRefused, ValueError) as exc:
                problems.append(f"row {i} {t}.{c}: {exc}")
                continue
            if col["data_type"] == "jsonb":
                jsonb_cols.setdefault(i, set()).add(c)
    if problems:
        out.status, out.stage, out.detail = "refused_types", "typed", problems
        return out
    # PostgreSQL's own normalization of every jsonb value must agree with jsonb/1's
    admin = target.admin()
    try:
        for i, (t, vals) in enumerate(rows):
            for c in jsonb_cols.get(i, ()):
                if vals[c].tag == "jsonb":
                    pg = admin.execute("SELECT %s::jsonb::text", (vals[c].value,)).fetchone()[0]
                    try:
                        jsonb_v1.accept(vals[c].value, pg)
                    except jsonb_v1.JsonbRefused as exc:
                        problems.append(f"row {i} {t}.{c}: {exc}")
    finally:
        admin.close()
    if problems:
        out.status, out.stage, out.detail = "refused_types", "pg_normalization", problems
        return out
    # the same schema in Phase 9B's sandbox
    sandbox.admin.execute("SET TIME ZONE 'UTC'")
    sandbox.reset(exp["setup_sql"], [])
    sandbox_fp = catalog.fingerprint(sandbox.admin, "ds_run")
    out.export["verified_by"] = "identical fingerprints"
    if sandbox_fp["sha256"] != target_fp["sha256"]:
        allowed = catalog.expression_only_differences(target_fp["facts"], sandbox_fp["facts"])
        if allowed is None:
            out.status, out.stage = "refused_catalog", "sandbox_fingerprint"
            out.detail = _fp_difference(target_fp["facts"], sandbox_fp["facts"])
            return out
        again = catalog.export(sandbox.admin, "ds_run")  # the sandbox's own export must rebuild it exactly
        sandbox.reset(again["setup_sql"], [])
        fixpoint = catalog.fingerprint(sandbox.admin, "ds_run")
        sandbox.reset(exp["setup_sql"], [])
        if fixpoint["sha256"] != sandbox_fp["sha256"] or \
                catalog.fingerprint(sandbox.admin, "ds_run")["sha256"] != sandbox_fp["sha256"]:
            out.status, out.stage = "refused_catalog", "deparse_fixpoint"
            out.detail = {"expression_differences": allowed, "fixpoint": False}
            return out
        admin = target.admin()
        try:
            established = expr_equiv.establish(admin, schema, sandbox.admin, "ds_run", allowed)
        finally:
            admin.close()
        out.export["expression_equivalence"] = established
        if not all(e["established"] for e in established):
            out.status, out.stage = "refused_catalog", "expression_equivalence"
            out.detail = [e for e in established if not e["established"]]
            return out
        out.export["verified_by"] = ("expression-only differences established on parsed node trees (catalog/1.1), "
                                     "sandbox re-export is a fixpoint")
        out.export["expression_differences"] = allowed
    try:
        seed, registered = loader.seed_rows([(t, {c: execute.plain(tv) for c, tv in v.items()}) for t, v in rows],
                                            jsonb_cols)
    except (loader.ExtensionRefused, jsonb_v1.JsonbRefused) as exc:
        out.status, out.stage, out.detail = "refused_types", "seed", str(exc)
        return out
    with trace.traced() as calls:
        report = loader.construct(sandbox, exp["setup_sql"], seed, registered)
    out.report, out.classification = report, report["classification"]
    try:
        out.trace = trace.require(calls, jsonb=bool(registered), proven=report["classification"] in loader.PROVEN)
    except trace.TraceRefused as exc:
        out.status, out.stage, out.detail = "refused_trace", "trace", str(exc)
        return out
    if report["classification"] not in loader.PROVEN:
        out.status, out.stage, out.detail = "refused_constructor", "construct_state", report["explanation"]
        return out
    seed_plain = [{c: execute.plain(tv) for c, tv in v.items()} for _, v in rows]
    order = execute.physical_plan(report, seed_plain)
    out.plan = execute.plan_record(rows, order, target_fp["sha256"], exp["setup_sql_sha256"], report)
    if before_write is not None:
        before_write()
    try:
        w = execute.write_and_verify(target.conninfo(), schema, rows, order, report["final_snapshot"],
                                     target_fp["sha256"], on_insert=on_insert)
    except execute.ExecutionRefused as exc:
        out.status, out.stage, out.detail = "refused_execution", "write", str(exc)
        return out
    out.write = w
    out.readback = execute.readback(target.conninfo(), schema, rows, w["baseline"], report["final_snapshot"])
    if not out.readback["verified"]:
        out.status, out.stage, out.detail = "refused_readback", "readback", out.readback["problems"]
        return out
    out.status = "constructed"
    return out


def _exp(exp: dict[str, Any]) -> dict[str, Any]:
    return {k: exp[k] for k in ("version", "setup_sql_sha256", "objects")}


def _fp_difference(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {k: {"target_only": [x for x in a[k] if x not in b[k]][:3],
                "sandbox_only": [x for x in b[k] if x not in a[k]][:3]} for k in a if a[k] != b[k]}


def open_sandbox(port: int, user: str, password: str) -> Any:
    """Phase 9B's sandbox on the disposable cluster (identity-checked; never 55432)."""
    return phase9b.open_sandbox(port, user, password)


def teardown(port: int, user: str, password: str) -> dict[str, bool]:
    return phase9b.teardown(port, user, password)


def setup_hash(setup_sql: str) -> str:
    return hashlib.sha256(setup_sql.encode()).hexdigest()
