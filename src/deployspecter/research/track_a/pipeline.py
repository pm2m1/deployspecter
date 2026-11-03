"""Track A's offline chain: WorkloadSpec v2 through the typed bridge to a prepared Gate 2A scenario. Pure.

Each stage is an existing, unmodified module:

    workload_spec.parse_workload -> registry.authorize -> argument_validation.validate_arguments
    -> workload_identity.validated_plan_identity -> state_planning.plan_state (and literal_translation)
    -> catalog_verification.verify_context -> phase9b_bridge.bridge_to_phase9b

The documented alternative to Phase 9B construction: the bridge's legacy projection, which it builds only when every
value compares exactly, becomes the release ``SeedRow`` list that Gate 2A's own bounded reset inserts. That is one
strict insertion in the bridge's dependency order, with no reconciliation and no search (arm C's construction rule).

This module builds ``LiveScenario`` objects and runs nothing. A ``LiveScenario`` is inert data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from deployspecter.live import argument_validation as av
from deployspecter.live import catalog_verification as cv
from deployspecter.live import literal_translation as lt
from deployspecter.live import phase9b_bridge as pb
from deployspecter.live import registry as rg
from deployspecter.live import state_planning as sp
from deployspecter.live import workload_identity as wi
from deployspecter.live import workload_spec as ws
from deployspecter.live.runner import LiveActor, LiveScenario
from deployspecter.models import SeedRow

from . import scenario as sc

DRY_RUN = "dry_run_fixture"  # every trust object here labels itself; none is a pin of a real export


class ChainError(RuntimeError):
    """A stage didn't give the outcome the demonstration needs. Raised, never papered over."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def build_registry() -> rg.Registry:
    config = json.loads(json.dumps(sc.REGISTRY_CONFIG))
    digest = rg.registry_digest(rg.RegistryConfig.model_validate(config))
    return rg.build_registry(config, trusted=rg.TrustedRegistration(
        registrar="track_a_harness", config_label="track_a_v1", pinned_digest=digest))


def build_contracts(registry: rg.Registry) -> av.Contracts:
    config = json.loads(json.dumps(sc.CONTRACTS_CONFIG))
    config["registry_digest"] = registry.digest
    digest = av.contracts_digest(av.ContractSet.model_validate(config))
    return av.build_contracts(config, registry=registry,
                              trusted=av.TrustedContracts(reviewer="track_a_harness", pinned_digest=digest))


@dataclass
class OfflineChain:
    stages: dict[str, Any] = field(default_factory=dict)
    digests: dict[str, str] = field(default_factory=dict)
    validated: av.ValidatedWorkload | None = None
    bridged: pb.Bridged | None = None
    seed: list[SeedRow] = field(default_factory=list)


def run_offline_chain(snapshot: dict[str, Any] | None = None) -> OfflineChain:
    """Every stage up to the bridge, against the dry-run snapshot unless another is given. No database."""
    chain = OfflineChain()
    registry = build_registry()
    contracts = build_contracts(registry)
    chain.digests.update(registry=registry.digest, contracts=contracts.digest)

    spec = ws.parse_workload(json.loads(json.dumps(sc.DOCUMENT)))
    authorized = rg.authorize(spec, registry)
    chain.stages["authorize"] = {"accepted": authorized.accepted, "rejections": len(authorized.rejections)}
    if not authorized.accepted:
        raise ChainError(f"authorization refused: {authorized.rejections}")
    chain.digests["spec"] = authorized.workload.spec_digest
    validated = av.validate_arguments(authorized.workload, registry=registry, contracts=contracts)
    chain.stages["validate_arguments"] = {"accepted": validated.accepted, "rejections": len(validated.rejections)}
    if not validated.accepted:
        raise ChainError(f"argument validation refused: {validated.rejections}")
    chain.validated = validated.workload
    if av.check_validated(chain.validated, registry=registry, contracts=contracts) is not None:
        raise ChainError("the validated workload doesn't recheck")
    identity = wi.validated_plan_identity(chain.validated, registry=registry, contracts=contracts)
    chain.stages["plan_identity"] = {"supported": identity.supported}
    if not identity.supported:
        raise ChainError(f"no plan identity: {identity.reason}")
    chain.digests["plan_identity"] = identity.identity.value

    snap = snapshot if snapshot is not None else sc.catalog_snapshot_fixture()
    snap_digest = cv.snapshot_digest(cv.CatalogSnapshot.model_validate(snap))
    context = sc.context_config(registry_digest=registry.digest, schema_digest=snap_digest)
    context_trust = sp.TrustedPlanning(reviewer=DRY_RUN, pinned_digest=sp.context_digest(
        sp.SchemaContextConfig.model_validate(context)))
    snapshot_trust = cv.TrustedSnapshot(exporter=DRY_RUN, source_label="hand_written_track_a_fixture",
                                        exported_on="2026-11-04", pinned_digest=snap_digest)
    chain.digests.update(snapshot=snap_digest, context=context_trust.pinned_digest)

    planned = sp.plan_state(chain.validated, registry=registry, contracts=contracts, context=context,
                            trusted=context_trust)
    literal = lt.translate_literally(chain.validated, registry=registry, contracts=contracts, context=context,
                                     trusted=context_trust)
    comparison = lt.compare_with_planner(chain.validated, registry=registry, contracts=contracts, context=context,
                                         trusted=context_trust)
    chain.stages["plan_state"] = {"outcome": planned.outcome, "counters": planned.counters.model_dump()}
    chain.stages["translate_literally"] = {"outcome": literal.outcome}
    chain.stages["literal_vs_planner"] = {"both_accept": comparison.both_accept, "same_rows": comparison.same_rows}
    if planned.outcome != "candidate_plan":
        raise ChainError(f"planner gave {planned.outcome}")

    verification = cv.verify_context(context, registry=registry, context_trust=context_trust, snapshot=snap,
                                     snapshot_trust=snapshot_trust)
    chain.stages["verify_context"] = {"outcome": verification.outcome, "findings": len(verification.findings),
                                      "scope": verification.scope}

    bridged = pb.bridge_to_phase9b(planned.request, validated=chain.validated, registry=registry,
                                   contracts=contracts, context=context, context_trust=context_trust, snapshot=snap,
                                   snapshot_trust=snapshot_trust)
    chain.stages["bridge"] = {"outcome": bridged.outcome, "code": getattr(bridged, "code", None)}
    if not isinstance(bridged, pb.Bridged):
        raise ChainError(f"bridge refused: {getattr(bridged, 'code', bridged)}")
    chain.bridged = bridged
    chain.digests.update(request=planned.request.request_digest, bridge_typed=bridged.representation.typed_digest,
                         legacy_seed=bridged.projection.legacy_seed_sha256)
    chain.seed = [SeedRow.model_validate(row) for row in bridged.projection.rows]
    return chain


def live_scenario(chain: OfflineChain, *, variant: str, setup_sql: str) -> LiveScenario:
    """Inert data for Gate 2A's supervised runner. The variant switch is added here, after validation."""
    if variant not in sc.VARIANTS:
        raise ChainError(f"unknown variant {variant!r}")
    if _sha(setup_sql) != sc.SETUP_SQL_SHA256:
        raise ChainError("setup SQL isn't the reviewed live_app.reservations schema")
    if chain.validated is None or chain.bridged is None:
        raise ChainError("the offline chain hasn't completed")
    actors = []
    for actor in chain.validated.authorized.actors:
        kwargs = {a.name: a.value for a in actor.arguments}
        if set(kwargs) & set(sc.VARIANTS[variant]):
            raise ChainError("a workload argument collides with the harness-owned variant switch")
        actors.append(LiveActor(actor.actor, actor.operation, kwargs={**kwargs, **sc.VARIANTS[variant]}))
    return LiveScenario(f"track_a:{variant}", setup_sql, list(chain.seed), actors)


def expected_initial_state(chain: OfflineChain) -> dict[str, list[dict[str, tuple[str, Any]]]]:
    """The typed rows the bridge asked for, by table. Every other table in the context is expected empty."""
    assert chain.bridged is not None
    tables: dict[str, list[dict[str, tuple[str, Any]]]] = {"events": [], "reservations": [], "rejections": []}
    for row in chain.bridged.representation.rows:
        tables.setdefault(row.table, []).append({v.column: (v.type, v.value) for v in row.values})
    return tables


def _typed_match(expected: tuple[str, Any], actual: Any) -> bool:
    kind, value = expected
    if kind == "null":
        return actual is None
    exact = {"int": int, "float": float, "bool": bool, "str": str}[kind]
    return type(actual) is exact and actual == value


def check_initial_state(expected: dict[str, list[dict[str, tuple[str, Any]]]],
                        observed: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Exact-count, exact-type comparison of a read state with the expected rows. Empty list means it matches.

    Columns the bridge omitted (database defaults) aren't compared, and are named in the result's caller. A missing
    table, an extra row, a missing row, or a value of the wrong Python type is a problem.
    """
    problems = []
    for table, rows in expected.items():
        if table not in observed:
            problems.append(f"{table}: not read")
            continue
        remaining = list(observed[table])
        for row in rows:
            match = next((i for i, r in enumerate(remaining)
                          if all(c in r and _typed_match(v, r[c]) for c, v in row.items())), None)
            if match is None:
                problems.append(f"{table}: no row matching {row}")
            else:
                remaining.pop(match)
        if remaining:
            problems.append(f"{table}: {len(remaining)} unrequested row(s)")
    for table in sorted(set(observed) - set(expected)):
        if observed[table]:
            problems.append(f"{table}: rows in a table the context doesn't describe")
    return problems
