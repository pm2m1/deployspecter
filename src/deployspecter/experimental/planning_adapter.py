"""EXPERIMENTAL adapter: a WorkloadSpec v2 document, through the existing typed planning chain, onto the stepping MVP.

Every planning stage is the existing module, called unmodified, in Track A's order:

    workload_spec.parse_workload -> registry.authorize -> argument_validation.validate_arguments
    -> workload_identity.validated_plan_identity -> state_planning.plan_state
    -> catalog_verification.verify_context -> phase9b_bridge.bridge_to_phase9b

Unlike Track A's ``run_offline_chain``, which raises on anything but success, ``plan`` returns one explicit outcome:
``bridged``, ``invalid``, ``unsupported``, ``infeasible``, ``indeterminate`` or ``refused``, with the stage and the
stage's own code. Nothing is repaired: a refusal at any stage stops the chain.

What the MVP takes from a ``bridged`` result is the bridge's **typed representation** (each value with its tag: int,
float, bool, str or null), not the legacy ``SeedRow`` projection. Values are inserted with their exact Python types,
so a large integer stays an integer, NULL stays NULL and ``-0.0`` keeps its sign. A tag outside those five, a table or
column the trusted context doesn't describe, or a bridge refusal (including one whose typed representation exists but
whose legacy conversion failed) is refused, not converted.

The handlers come only from the trusted registry: each registered ``operation`` must be in ``TRUSTED_OPERATIONS``,
whose module source is pinned by SHA-256. The buggy and fixed builds are the harness-owned ``lock_row`` switch from
Track A's scenario, injected after validation, never a workload argument.

Before each run the initial state is read on an independent connection and compared with the requested typed rows
(exact count, exact Python type); the run is then bound to that state's digest, and a second reset must reproduce
it. The catalog check is against Track A's hand-written dry-run snapshot only: it is not a live catalog export.

This does not use or replace Gate 2A's supervised runner, and nothing here is Gate A2 evidence.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any

from psycopg import sql

from deployspecter.live import argument_validation as av
from deployspecter.live import catalog_verification as cv
from deployspecter.live import phase9b_bridge as pb
from deployspecter.live import registry as rg
from deployspecter.live import state_planning as sp
from deployspecter.live import workload_identity as wi
from deployspecter.live import workload_spec as ws
from deployspecter.research.track_a import pipeline as tp
from deployspecter.research.track_a import scenario as sc

from . import harness as hz
from . import oracle
from .stepping import Handler, Refused, normal_sql

REPO = Path(__file__).resolve().parents[3]
# Trusted module -> source file. Only these are ever imported, and only when the source hash matches its pin.
TRUSTED_SOURCES = {
    "live_app.reservations": REPO / "tests" / "live_app" / "reservations.py",
    "live_app.properties": REPO / "tests" / "live_app" / "properties.py",
    "experimental.reservations_corrected": Path(__file__).resolve().parent / "apps" / "reservations_corrected.py",
}
TRUSTED_OPERATIONS = {"live_app.reservations:reserve_seats", "live_app.reservations:release_seats"}
EXACT_TAGS = {"int": int, "float": float, "bool": bool, "str": str}

# The statements each trusted operation sends (both builds), reviewed from its source.
_LOCKABLE = {"SELECT capacity, reserved FROM events WHERE id = %s", "SELECT reserved FROM events WHERE id = %s"}
ALLOWED_SQL = {
    "reserve_seats": ["SELECT capacity, reserved FROM events WHERE id = %s",
                      "INSERT INTO rejections (event_id, holder, seats_available) VALUES (%s, %s, %s)",
                      "UPDATE events SET reserved = %s WHERE id = %s",
                      "INSERT INTO reservations (event_id, holder, qty) VALUES (%s, %s, %s)"],
    "release_seats": ["SELECT id, event_id, qty FROM reservations WHERE holder = %s ORDER BY id",
                      "SELECT reserved FROM events WHERE id = %s",
                      "UPDATE events SET reserved = %s WHERE id = %s", "DELETE FROM reservations WHERE id = %s"],
}


class AdapterRefused(Refused):
    """The adapter won't take this step. Raised, never worked around."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# The reviewed sources' hashes (as committed in cee464a), so a later edit is refused rather than run.
PINNED_SHA256 = {
    "live_app.reservations": "8d60890d642253f4bf2672d7d48d97e8341930840791e4bacd1b6a0eba8661c1",
    "live_app.properties": "fa67687c3a58a7d9c873f1710dfc114ac26d3cbd96e3b579484c58a44e803f5b",
    "experimental.reservations_corrected": "d656ee637a7a345afddad2f6b7fca7ceb2a1e78ec64ff32511599230771828cd",
}
# The two frozen builds come from Track A's harness-owned switch; ``corrected`` is this prototype's separately named
# reference (``apps/reservations_corrected.py``). The frozen ``fixed`` build is not relabelled as correct.
CORRECTED = "corrected"


def load_trusted(module: str) -> ModuleType:
    if module not in TRUSTED_SOURCES:
        raise AdapterRefused(f"{module} isn't a trusted application module")
    path = TRUSTED_SOURCES[module]
    got = _sha256(path)
    if got != PINNED_SHA256[module]:
        raise AdapterRefused(f"{module}'s source isn't the reviewed one (sha256 {got[:12]})")
    spec = importlib.util.spec_from_file_location(f"ds_mvp_trusted_{module.replace('.', '_')}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ the chain, with explicit outcomes


@dataclass
class Plan:
    outcome: str  # bridged, invalid, unsupported, infeasible, indeterminate, refused
    stage: str
    detail: Any = None
    stages: dict[str, Any] = field(default_factory=dict)
    digests: dict[str, str] = field(default_factory=dict)
    validated: av.ValidatedWorkload | None = None
    bridged: pb.Bridged | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "stage": self.stage, "detail": self.detail, "stages": self.stages,
                "digests": self.digests}


PLANNER_OUTCOMES = {"unsupported_goal": "unsupported", "invalid_trusted_context": "invalid",
                    "infeasible_under_assumptions": "infeasible", "indeterminate": "indeterminate"}
CATALOG_OUTCOMES = {"MISMATCH": "invalid", "UNSUPPORTED": "unsupported", "INCOMPLETE": "indeterminate"}


def plan(document: Any, *, snapshot: dict[str, Any] | None = None) -> Plan:
    """Every planning stage on ``document``, offline. The first stage that doesn't succeed decides the outcome."""
    out = Plan("indeterminate", "start")
    registry = tp.build_registry()
    contracts = tp.build_contracts(registry)
    out.digests.update(registry=registry.digest, contracts=contracts.digest)
    try:
        spec = ws.parse_workload(json.loads(json.dumps(document)))
    except ws.SpecRejected as exc:
        return _stop(out, "invalid", "parse", [str(p) for p in getattr(exc, "problems", [exc])][:10])
    except (TypeError, ValueError) as exc:
        return _stop(out, "invalid", "parse", [f"{type(exc).__name__}: {str(exc)[:200]}"])
    authorized = rg.authorize(spec, registry)
    out.stages["authorize"] = {"accepted": authorized.accepted, "rejections": len(authorized.rejections)}
    if not authorized.accepted:
        return _stop(out, "invalid", "authorize", [r.model_dump() for r in authorized.rejections][:10])
    out.digests["spec"] = authorized.workload.spec_digest
    validated = av.validate_arguments(authorized.workload, registry=registry, contracts=contracts)
    out.stages["validate_arguments"] = {"accepted": validated.accepted, "rejections": len(validated.rejections)}
    if not validated.accepted:
        return _stop(out, "invalid", "validate_arguments", [r.model_dump() for r in validated.rejections][:10])
    out.validated = validated.workload
    if av.check_validated(out.validated, registry=registry, contracts=contracts) is not None:
        return _stop(out, "invalid", "validate_arguments", "the validated workload doesn't recheck")
    identity = wi.validated_plan_identity(out.validated, registry=registry, contracts=contracts)
    out.stages["plan_identity"] = {"supported": identity.supported}
    if not identity.supported:
        return _stop(out, "unsupported", "plan_identity", identity.reason)
    out.digests["plan_identity"] = identity.identity.value

    snap = snapshot if snapshot is not None else sc.catalog_snapshot_fixture()
    try:
        snap_digest = cv.snapshot_digest(cv.CatalogSnapshot.model_validate(snap))
    except Exception as exc:  # a malformed snapshot is invalid trusted input, never a partial check
        return _stop(out, "invalid", "snapshot", f"{type(exc).__name__}: {str(exc)[:200]}")
    context = sc.context_config(registry_digest=registry.digest, schema_digest=snap_digest)
    context_trust = sp.TrustedPlanning(reviewer=tp.DRY_RUN, pinned_digest=sp.context_digest(
        sp.SchemaContextConfig.model_validate(context)))
    snapshot_trust = cv.TrustedSnapshot(exporter=tp.DRY_RUN, source_label="hand_written_track_a_fixture",
                                        exported_on="2026-11-04", pinned_digest=snap_digest)
    out.digests.update(snapshot=snap_digest, context=context_trust.pinned_digest)

    planned = sp.plan_state(out.validated, registry=registry, contracts=contracts, context=context,
                            trusted=context_trust)
    out.stages["plan_state"] = {"outcome": planned.outcome,
                                "goals": [g.model_dump() for g in getattr(planned, "goals", ())]}
    if planned.outcome != "candidate_plan":
        detail = {k: getattr(planned, k) for k in ("goal_label", "code", "reason", "reasons") if hasattr(planned, k)}
        return _stop(out, PLANNER_OUTCOMES.get(planned.outcome, "indeterminate"), "plan_state", detail)

    verification = cv.verify_context(context, registry=registry, context_trust=context_trust, snapshot=snap,
                                     snapshot_trust=snapshot_trust)
    out.stages["verify_context"] = {"outcome": verification.outcome, "findings": len(verification.findings)}
    if verification.outcome != "MATCH":
        return _stop(out, CATALOG_OUTCOMES.get(verification.outcome, "indeterminate"), "verify_context",
                     [f.model_dump() for f in verification.findings][:10])

    bridged = pb.bridge_to_phase9b(planned.request, validated=out.validated, registry=registry,
                                   contracts=contracts, context=context, context_trust=context_trust, snapshot=snap,
                                   snapshot_trust=snapshot_trust)
    out.stages["bridge"] = {"outcome": bridged.outcome, "code": getattr(bridged, "code", None)}
    if not isinstance(bridged, pb.Bridged):
        return _stop(out, "refused", "bridge", {"code": bridged.code, "reason": bridged.reason,
                                                "row_id": bridged.row_id, "column": bridged.column})
    out.bridged = bridged
    out.digests.update(request=planned.request.request_digest, bridge_typed=bridged.representation.typed_digest)
    out.outcome, out.stage = "bridged", "bridge"
    return out


def _stop(out: Plan, outcome: str, stage: str, detail: Any) -> Plan:
    out.outcome, out.stage, out.detail = outcome, stage, detail
    return out


# ------------------------------------------------------------------ typed rows, exactly


def typed_rows(p: Plan) -> list[tuple[str, dict[str, Any]]]:
    """(table, {column: exact Python value}) in the bridge's dependency order. Refuses anything it can't keep exact."""
    if p.outcome != "bridged" or p.bridged is None:
        raise AdapterRefused(f"the plan wasn't bridged ({p.outcome} at {p.stage})")
    entities = {e["table"]: {c["column"] for c in e["columns"]} for e in
                sc.context_config(registry_digest="", schema_digest="")["entities"]}
    rows = []
    for row in p.bridged.representation.rows:
        if row.table not in entities:
            raise AdapterRefused(f"the bridge asked for a row in {row.table}, which the context doesn't describe")
        values: dict[str, Any] = {}
        for v in row.values:
            if v.column not in entities[row.table]:
                raise AdapterRefused(f"{row.table}.{v.column} isn't a context column")
            if v.type == "null":
                if v.value is not None:
                    raise AdapterRefused(f"{row.table}.{v.column}: a null tag with a value")
                values[v.column] = None
                continue
            want = EXACT_TAGS.get(v.type)
            if want is None:
                raise AdapterRefused(f"{row.table}.{v.column}: unsupported value tag {v.type!r}")
            if type(v.value) is not want:  # bool is not an int here, and an int is not a float
                raise AdapterRefused(f"{row.table}.{v.column}: a {v.type} tag holding {type(v.value).__name__}")
            if want is float and not math.isfinite(v.value):
                raise AdapterRefused(f"{row.table}.{v.column}: a non-finite float")
            values[v.column] = v.value
        rows.append((row.table, values))
    return rows


def expected_rows(p: Plan) -> dict[str, list[dict[str, tuple[str, Any]]]]:
    """Track A's typed expectation (by table), for its exact-type initial-state comparison."""
    chain = tp.OfflineChain(bridged=p.bridged)
    return tp.expected_initial_state(chain)


# ------------------------------------------------------------------ the fixture the harness runs


class PlannedReservations:
    """``live_app.reservations`` (Gate 2A's synthetic application, unmodified) as a stepping fixture whose initial
    state is the bridge's typed rows. Duck-types the module interface ``harness.run_app`` takes."""

    SCHEMA = sc.SCHEMA  # ds_run, the schema the trusted context and dry-run snapshot describe, in the MVP database
    AUTOCOMMIT = False  # as Gate 2A's actor sessions

    def __init__(self, p: Plan) -> None:
        self.plan = p
        self.rows = typed_rows(p)
        self.app = load_trusted("live_app.reservations")
        if hashlib.sha256(self.app.SETUP_SQL.encode()).hexdigest() != sc.SETUP_SQL_SHA256:
            raise AdapterRefused("setup SQL isn't the reviewed live_app.reservations schema")
        self.properties = load_trusted("live_app.properties")
        self.expected = expected_rows(p)

    def registry(self, variant: str) -> dict[tuple[str, str], Handler]:
        if variant not in sc.VARIANTS and variant != CORRECTED:
            raise ValueError(f"unknown variant {variant!r}")
        switch = sc.VARIANTS.get(variant, sc.VARIANTS["fixed"])  # corrected reserves exactly as lock_row=True
        corrected = load_trusted("experimental.reservations_corrected") if variant == CORRECTED else None
        out = {}
        for ep in sc.REGISTRY_CONFIG["entrypoints"]:
            operation = ep["operation"]
            if operation not in TRUSTED_OPERATIONS:
                raise AdapterRefused(f"{operation} isn't a trusted operation")
            module, name = operation.split(":")
            fn = getattr(self.app, name)
            allowed = set(ALLOWED_SQL[name]) | {s + " FOR UPDATE" for s in ALLOWED_SQL[name] if s in _LOCKABLE}
            bound = partial(fn, **switch)
            if corrected is not None and name == "release_seats":
                fn, allowed = corrected.release_seats, set(corrected.ALLOWED_RELEASE)
                bound = partial(fn)
            bound.__name__, bound.__module__ = name, fn.__module__  # type: ignore[attr-defined]
            for version in ep["versions"]:
                out[(version, ep["id"])] = Handler(version, ep["id"], bound, frozenset(normal_sql(s) for s in allowed),
                                                   {a["name"]: object for a in ep["arguments"]})
        return out

    def reset(self, conn: Any, _seed: Any = None) -> None:
        with conn.transaction():
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(self.SCHEMA)))
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.SCHEMA)))
            conn.execute(sql.SQL("SET LOCAL search_path = {}").format(sql.Identifier(self.SCHEMA)))
            conn.execute(self.app.SETUP_SQL)
            for table, values in self.rows:
                cols = list(values)
                conn.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)),
                    sql.SQL(", ").join(sql.Placeholder() * len(cols))), [values[c] for c in cols])

    def read_state(self, conn: Any) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            for table in ("events", "reservations", "rejections"):
                cur = conn.execute(sql.SQL("SELECT * FROM {}.{} ORDER BY id").format(
                    sql.Identifier(self.SCHEMA), sql.Identifier(table)))
                names = [d.name for d in cur.description]
                out[table] = [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
        return out

    def check(self, state: dict[str, list[dict[str, Any]]]) -> list[str]:
        return list(self.properties.check_invariants(state))

    def check_run(self, initial: dict[str, list[dict[str, Any]]], final: dict[str, list[dict[str, Any]]],
                  results: dict[str, Any]) -> list[str]:
        """R1 and R2 from the frozen properties, plus one EXPERIMENTAL check written from the declared release
        semantics ("release_seats releases one reservation the holder has"), independent of either build's code:

        R5  reservations after = reservations before + callers told "reserved" - callers told "released"
        """
        out = self.check(final)
        told = [(r or {}).get("status") for r in results.values()]
        want = len(initial["reservations"]) + told.count("reserved") - told.count("released")
        if len(final["reservations"]) != want:
            out.append(f"R5 {told.count('released')} caller(s) told released and {told.count('reserved')} told "
                       f"reserved, but reservations went from {len(initial['reservations'])} to "
                       f"{len(final['reservations'])}")
        return out

    def verify_initial(self, target: hz.Target) -> str:
        """Reset, read independently, compare exactly with the requested typed rows; the digest the run binds to."""
        admin = target.admin()
        try:
            self.reset(admin)
            state = self.read_state(admin)
        finally:
            admin.close()
        problems = tp.check_initial_state(self.expected, state)
        if problems:
            raise AdapterRefused(f"the initial state isn't the planned one: {problems}")
        return oracle.digest(state)


def invocations(p: Plan) -> list[dict[str, Any]]:
    assert p.validated is not None
    out = []
    for actor in p.validated.authorized.actors:
        args = {a.name: a.value for a in actor.arguments}
        if set(args) & {k for v in sc.VARIANTS.values() for k in v}:
            raise AdapterRefused("a workload argument collides with the harness-owned variant switch")
        out.append({"actor": actor.actor, "app_version": actor.application_version, "handler": actor.entrypoint,
                    "args": args})
    return out


def run_planned(target: hz.Target, document: Any, *, variant: str, schedule: list[str], corroborate: bool = False,
                **kw: Any) -> tuple[Plan, hz.RunResult | None, str | None]:
    """Plan; if bridged, verify the initial state and run the trusted handlers on the stepping engine."""
    p = plan(document)
    if p.outcome != "bridged":
        return p, None, None
    fixture = PlannedReservations(p)
    digest = fixture.verify_initial(target)
    r = hz.run_app(target, fixture, invocations(p), None, variant=variant, schedule=schedule,
                   label=str(document.get("name", "")), corroborate=corroborate, expected_digest=digest, **kw)
    return p, r, digest
