"""EXPERIMENTAL end-to-end state construction for the Gate 2A synthetic reservations application (unmodified).

    WorkloadSpec v2 document
    → parse, authorize, validate, plan identity           (planning_adapter.plan: unmodified frozen stages)
    → state goals → deterministic candidate rows          (state_planning.plan_state)
    → live catalog export + verification (MATCH only)     (catalog_export + catalog_verification)
    → typed construction plan                             (phase9b_bridge: typed representation + legacy projection)
    → Phase 9B construction in its own sandbox            (state_constructor.construct_state, PROVEN only)
    → typed construction in the run database, in the constructor's order
    → independent verification                            (typed.verify: exact, own session; plus full to_jsonb
                                                           equality with the constructor's independently checked
                                                           state; plus Track A's exact-type initial-state check)
    → real handler execution on the stepping engine       (harness.run_app, bound to the verified digest)
    → PostgreSQL server-log corroboration                 (corroborate)
    → independent oracle                                  (live_app.properties R1/R2, plus R5)

Every stage that doesn't succeed ends the pipeline with an explicit outcome (``Outcome.status``); nothing downstream
runs, and nothing is inserted from a refused stage. In particular, a bridge refusal (even one with a typed
representation) and any constructor classification outside ``PROVEN`` stop here. Their rows are never inserted
some other way.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import sql

from .. import catalog_export as ce
from .. import harness as hz
from .. import planning_adapter as pa
from . import phase9b, typed

STATUSES = ("ran", "refused_planning", "refused_typed", "refused_constructor", "refused_verification",
            "refused_run")


@dataclass
class Outcome:
    status: str
    stage: str
    detail: Any = None
    planning: dict[str, Any] | None = None
    construction: dict[str, Any] | None = None
    order: list[int] | None = None
    verification: dict[str, Any] = field(default_factory=dict)
    run: hz.RunResult | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {"status": self.status, "stage": self.stage, "detail": self.detail, "planning": self.planning,
               "construction": self.construction, "order": self.order, "verification": self.verification}
        if self.run is not None:
            out["run"] = {"bundle": self.run.bundle(), "violations": self.run.violations,
                          "corroboration": self.run.corroboration, "initial_digest": self.run.initial_digest}
        return out


def tv_of(value: Any) -> typed.TV:
    if value is None:
        return typed.TV("null", None)
    tag = {bool: "bool", int: "int", float: "float", str: "str"}.get(type(value))
    if tag is None:
        raise typed.TypedRefused(f"no tag for {type(value).__name__}")
    return typed.TV(tag, value)


def canonical(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True)


def constructor_order(report: dict[str, Any], seed: list[dict[str, Any]]) -> list[int]:
    """Which bridged rows the run database inserts, in which order, from a PROVEN report."""
    cls = report["classification"]
    if cls == "ORIGINAL_VALID":
        return list(range(len(seed)))
    if cls == "CONSTRUCTED_EQUIVALENT":
        return [s["row_index"] for s in report["plan"] if s["action"] == "insert"]
    if cls == "RECONCILED_EQUIVALENT":  # a subsequence of the seed, in order: match by exact canonical JSON
        out, i = [], 0
        for eff in report["effective_seed"]:
            while i < len(seed) and canonical(seed[i]) != canonical(eff):
                i += 1
            if i == len(seed):
                raise phase9b.ConstructorRefused("the reconciled seed isn't a subsequence of the bridged rows")
            out.append(i)
            i += 1
        return out
    raise phase9b.ConstructorRefused(f"{cls} isn't proven")


class ConstructedReservations(pa.PlannedReservations):
    """The planned fixture, whose reset inserts the bridge's exact typed rows in the constructor's order (rows the
    constructor found already present are not inserted)."""

    def __init__(self, p: pa.Plan, order: list[int]) -> None:
        super().__init__(p)
        planned = self.rows
        self.rows = [planned[i] for i in order]
        self.order = order


def snapshot(conn: Any, schema: str) -> dict[str, list[Any]]:
    """Every table's rows as to_jsonb, sorted the way the constructor sorts them."""
    out = {}
    tables = [r[0] for r in conn.execute("SELECT tablename::text FROM pg_tables WHERE schemaname = %s ORDER BY 1",
                                         (schema,)).fetchall()]
    for t in tables:
        rows = conn.execute(sql.SQL("SELECT to_jsonb(t) FROM {}.{} t").format(sql.Identifier(schema),
                                                                             sql.Identifier(t))).fetchall()
        out[t] = sorted((r[0] for r in rows), key=lambda r: json.dumps(r, sort_keys=True))
    return out


def run(target: hz.Target, document: Any, *, variant: str, schedule: list[str], sandbox: Any,
        corroborate: bool = True, catalog: str = "live") -> Outcome:
    """The whole pipeline on one document, for one variant and schedule. ``sandbox``: ``phase9b.open_sandbox``.

    ``catalog``: ``live`` verifies the trusted context against a live export of the freshly created schema. The
    export lists triggers for the **whole database** (a trigger elsewhere could write here), so any user trigger in
    another schema of ``ds_mvp`` makes planning ``indeterminate`` (fail-closed). ``dry_run`` verifies it against
    Track A's hand-written, pinned snapshot instead, as ``planning_adapter.run_planned`` does. Everything after the
    catalog check (Phase 9B, construction, verification, run, corroboration, oracle) is live in both modes."""
    from deployspecter.models import ColumnValue, SeedRow

    if catalog == "live":
        snap = pa.live_snapshot(target)
        p = pa.plan(document, snapshot=snap, snapshot_source=(pa.LIVE_EXPORTER, "disposable_ds_mvp",
                                                              datetime.date.today().isoformat()))
    elif catalog == "dry_run":
        snap = None
        p = pa.plan(document)
    else:
        raise ValueError(f"catalog must be live or dry_run, not {catalog!r}")
    out = Outcome("refused_planning", p.stage, p.detail, planning=p.to_dict())
    out.verification["catalog"] = catalog
    if p.outcome != "bridged":
        out.detail = {"outcome": p.outcome, "detail": p.detail}
        return out
    try:
        rows = pa.typed_rows(p)
        tvs = [(t, {c: tv_of(v) for c, v in vals.items()}) for t, vals in rows]
    except (pa.AdapterRefused, typed.TypedRefused) as exc:
        out.status, out.stage, out.detail = "refused_typed", "typed_rows", str(exc)
        return out
    seed = [dict(r) for r in p.bridged.projection.rows]
    if [(r["table"], {v["column"]: v["value"] for v in r["values"]}) for r in seed] != rows:
        out.status, out.stage, out.detail = "refused_typed", "projection", "the projection isn't the typed rows"
        return out
    for a, (_, b) in zip(seed, rows, strict=True):  # same values with the same Python types, not just ==
        if [type(v["value"]) for v in a["values"]] != [type(x) for x in b.values()]:
            out.status, out.stage, out.detail = "refused_typed", "projection", "a projected value changed type"
            return out
    seed_rows = [SeedRow(table=r["table"], values=[ColumnValue(column=v["column"], value=v["value"])
                                                   for v in r["values"]]) for r in seed]
    fixture = pa.PlannedReservations(p)  # loads and hash-checks the trusted application and its setup SQL
    report = phase9b.construct(sandbox, fixture.app.SETUP_SQL, seed_rows)
    out.construction = report
    if report["classification"] not in phase9b.PROVEN:
        out.status, out.stage = "refused_constructor", "construct_state"
        out.detail = {"classification": report["classification"], "explanation": report["explanation"]}
        return out
    out.order = constructor_order(report, seed)
    fx = ConstructedReservations(p, out.order)

    # independent verification, before any handler runs
    try:
        digest = fx.verify_initial(target)  # reset, read, Track A's exact-type, count-exact check
    except pa.AdapterRefused as exc:
        out.status, out.stage, out.detail = "refused_verification", "track_a_initial_state", str(exc)
        return out
    with psycopg.connect(**target.conninfo(), autocommit=True, application_name="ds_sc_verify") as conn:
        problems = []
        by_table: dict[str, list[dict[str, typed.TV]]] = {}
        for t, vals in tvs:
            by_table.setdefault(t, []).append(vals)
        for t, want in sorted(by_table.items()):
            problems += typed.verify(conn, fx.SCHEMA, t, want,
                                     expect_count=len(report["final_snapshot"].get(t, [])))
        state = snapshot(conn, fx.SCHEMA)
    out.verification.update(typed_exact=problems, constructor_snapshot_equal=state == report["final_snapshot"],
                            initial_digest=digest)
    if problems or not out.verification["constructor_snapshot_equal"]:
        out.status, out.stage = "refused_verification", "independent_verification"
        out.detail = {"typed_problems": problems, "snapshot_equal": out.verification["constructor_snapshot_equal"]}
        return out
    admin = target.admin()
    try:
        catalog_again = ce.canonical_digest(ce.export_snapshot(admin, fx.SCHEMA))
    finally:
        admin.close()
    if snap is not None and catalog_again != ce.canonical_digest(snap):
        out.status, out.stage, out.detail = "refused_verification", "catalog_drift", "the catalog changed"
        return out
    try:
        r = hz.run_app(target, fx, pa.invocations(p), None, variant=variant, schedule=schedule,
                       label=str(document.get("name", "")), corroborate=corroborate, expected_digest=digest)
    except hz.Refused as exc:
        out.status, out.stage, out.detail = "refused_run", "run_app", str(exc)
        return out
    out.run = r
    out.status, out.stage = "ran", "oracle"
    out.detail = {"violations": r.violations, "corroborated": (r.corroboration or {}).get("ok")}
    return out
