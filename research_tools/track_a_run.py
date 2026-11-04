"""Track A's bounded driver for the first synthetic PostgreSQL run. Research-only. NOT YET AUTHORIZED TO RUN.

It orchestrates exactly the reviewed execution manifest (version 2) and nothing else:

    plan    print the manifest's actions; touches nothing
    export  OP-2: two parent exports, a cross-check, parent-session reconciliation; needs an export approval
    run     OP-2b to OP-6f: pins, construction checks, Gate 2A runs, confirmation, binding; needs a run approval
            and a reviewer attestation naming the export's digest

Responsibilities are split, and the driver holds only the parent's:

* operator: starts and stops the disposable container (OP-1, OP-7). The driver never runs a command.
* parent (this driver): exports, independent checks, its own session reconciliation, orchestration, evidence.
* Gate 2A child: every application statement, through ``SupervisedRunner``'s public ``run`` and ``plain_serial`` and
  ``explore_live``. No private supervisor attribute is used.

Every external effect goes through ``Effects``. ``RealEffects`` is the only code that connects, and it is constructed
only by ``main``. Importing this module does nothing. There are no retries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_VERSION = 2
APPLICATION = "reservations"
LOCK_BY_VARIANT = {"buggy": False, "fixed": True}
PHASES = ("export", "run")


class DriverRefused(RuntimeError):
    """A prerequisite is missing or a check failed. Nothing further runs, and nothing is retried."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------ manifest and approvals


def load_manifest(path: Path, expected_sha256: str) -> dict[str, Any]:
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise DriverRefused("the manifest's digest isn't the one given")
    manifest = json.loads(path.read_text())
    if manifest.get("version") != MANIFEST_VERSION or manifest.get("manifest") != "track-a-execution-manifest":
        raise DriverRefused("not a version 2 Track A manifest")
    if manifest.get("context") != "synthetic_demonstration" or manifest.get("never_heldout_evidence") is not True:
        raise DriverRefused("the manifest isn't a synthetic demonstration")
    if manifest.get("application", {}).get("name") != APPLICATION:
        raise DriverRefused("only the synthetic reservations application is supported")
    variants = manifest.get("variants", {})
    if {k: variants[k].get("lock_row") for k in variants if k in LOCK_BY_VARIANT} != LOCK_BY_VARIANT:
        raise DriverRefused("variants must be exactly the harness-owned lock_row switch")
    return manifest


def load_approval(path: Path | None, *, manifest_sha256: str, phase: str) -> dict[str, Any]:
    if path is None:
        raise DriverRefused(f"no approval for the {phase} phase")
    approval = json.loads(path.read_text())
    if approval.get("manifest_sha256") != manifest_sha256 or approval.get("phase") != phase:
        raise DriverRefused("the approval names another manifest or phase")
    if not approval.get("approver") or not approval.get("approved_on"):
        raise DriverRefused("the approval doesn't say who approved it and when")
    return approval


def verify_sources(manifest: dict[str, Any]) -> None:
    """Every file the run depends on must still have the digest the manifest was reviewed with."""
    for path, digest in manifest["source_sha256"].items():
        if sha256_file(ROOT / path) != digest:
            raise DriverRefused(f"{path} changed since the manifest was reviewed")


# ------------------------------------------------------------------ evidence


class EvidenceDir:
    """A new directory per run. Nothing is ever overwritten."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def create(cls, root: Path) -> EvidenceDir:
        run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(4)
        path = root / run_id
        path.mkdir(parents=True, exist_ok=False)
        return cls(path)

    def write(self, name: str, data: Any) -> None:
        target = self.path / name
        if target.exists():
            raise DriverRefused(f"{name} already exists; evidence is never overwritten")
        target.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")

    def read(self, name: str) -> Any:
        return json.loads((self.path / name).read_text())


# ------------------------------------------------------------------ effects


class Effects(Protocol):
    def preflight(self) -> list[str]: ...
    def reset(self, setup_sql: str, seed: list[Any]) -> None: ...
    def snapshot(self) -> dict[str, list[dict[str, Any]]]: ...
    def parent_connection(self, tag: str) -> Any: ...  # a context manager yielding a connection
    def log_position(self) -> None: ...
    def log_mark(self, token: str) -> None: ...
    def log_read(self) -> bytes: ...
    def plain_serial(self, scenario: Any, order: list[str]) -> dict[str, Any]: ...
    def run(self, scenario: Any, schedule: Any) -> Any: ...
    def explore(self, scenario: Any, budget: Any, check: Callable[[Any], Any]) -> Any: ...


def _imports():
    sys.path.insert(0, str(ROOT / "tests"))
    from deployspecter.research.track_a import catalog_export as ce
    from deployspecter.research.track_a import evidence_binding as eb
    from deployspecter.research.track_a import parent_reconciliation as pr
    from deployspecter.research.track_a import pipeline as p
    from deployspecter.research.track_a import replay_policy as rp
    from deployspecter.research.track_a import scenario as sc
    return ce, eb, pr, p, rp, sc


def parent_operation(effects: Effects, manifest: dict[str, Any], op: str, action: Callable[[Any], Any],
                     expected: tuple[Any, ...]) -> tuple[Any, dict[str, Any]]:
    """Run one parent action on its own tagged session, then reconcile that session from the server log."""
    _ce, _eb, pr, *_ = _imports()
    nonce = secrets.token_hex(6)
    tag, start, end = f"ds_parent_{op}_{nonce}", f"ds_parent_mark_start_{nonce}", f"ds_parent_mark_end_{nonce}"
    effects.log_position()
    effects.log_mark(start)
    with effects.parent_connection(tag) as conn:
        result = action(conn)
    effects.log_mark(end)
    rows = pr.window(effects.log_read(), start, end)
    reconciliation = pr.reconcile_parent_session(rows, tag=tag, expected=expected,
                                                 user=manifest["cluster"]["admin_user"],
                                                 database=manifest["cluster"]["database"])
    record = {"op": op, "tag": tag, "ok": reconciliation.ok, "problems": reconciliation.problems,
              "statements": reconciliation.statements_seen, "session_id": reconciliation.session_id}
    if not reconciliation.ok:
        raise DriverRefused(f"parent session {op} didn't reconcile: {reconciliation.problems}")
    return result, record


def _export_expected(ce, pr) -> tuple[Any, ...]:
    return tuple(pr.ExpectedStatement(text) for _, text in ce.EXPORT_QUERIES)


def _cross_expected(ce, pr) -> tuple[Any, ...]:
    return tuple(pr.ExpectedStatement(text) for _, text in ce.CROSS_CHECK_QUERIES)


def export_phase(effects: Effects, manifest: dict[str, Any], evidence: EvidenceDir) -> str:
    ce, _eb, pr, _p, _rp, sc = _imports()
    problems = effects.preflight()
    if problems:
        raise DriverRefused(f"Gate 2A preflight: {problems}")
    effects.reset(_setup_sql(manifest), [])
    first, rec_a = parent_operation(effects, manifest, "op2a", ce.export_snapshot, _export_expected(ce, pr))
    second, rec_b = parent_operation(effects, manifest, "op2b", ce.export_snapshot, _export_expected(ce, pr))
    cross, rec_c = parent_operation(effects, manifest, "op2c",
                                    lambda conn: ce.cross_check_read(conn, first.snapshot), _cross_expected(ce, pr))
    evidence.write("export_1.json", {"snapshot": first.snapshot, "digest": first.digest, "identity": first.identity,
                                     "structural_digest": ce.structural_digest(first.snapshot)})
    evidence.write("export_2.json", {"snapshot": second.snapshot, "digest": second.digest,
                                     "identity": second.identity})
    evidence.write("cross_check.json", {"agrees": cross.agrees, "problems": cross.problems})
    evidence.write("parent_reconciliation_export.json", [rec_a, rec_b, rec_c])
    if first.digest != second.digest or not cross.agrees:
        raise DriverRefused("the exports differ or the re-read disagrees; nothing to attest")
    return first.digest


def _setup_sql(manifest: dict[str, Any]) -> str:
    _ce, _eb, _pr, _p, _rp, sc = _imports()
    from live_app import reservations as app
    if hashlib.sha256(app.SETUP_SQL.encode()).hexdigest() != manifest["schema"]["setup_sql_sha256"] \
            or sc.SETUP_SQL_SHA256 != manifest["schema"]["setup_sql_sha256"]:
        raise DriverRefused("the setup SQL isn't the reviewed one")
    return app.SETUP_SQL


class Deadline:
    """The manifest's batch wall-clock ceiling. A safety stop only: reaching it is never a result."""

    def __init__(self, seconds: float) -> None:
        self.end = time.monotonic() + seconds

    def check(self, where: str) -> None:
        if time.monotonic() >= self.end:
            raise DriverRefused(f"batch wall-clock safety ceiling reached before {where}")


def run_phase(effects: Effects, manifest: dict[str, Any], evidence: EvidenceDir, attestation: dict[str, Any]) -> dict:
    ce, eb, pr, p, rp, sc = _imports()
    deadline = Deadline(float(manifest["safety_ceilings"]["batch_wall_clock_s"]))
    from deployspecter.live.explore import LiveBudget
    from deployspecter.live.runner import ExplicitSchedule
    from live_app import properties as props

    if (evidence.path / "bundle.json").exists():
        raise DriverRefused("this evidence directory already holds a run")
    exp1, exp2 = evidence.read("export_1.json"), evidence.read("export_2.json")
    first = ce.ExportResult(exp1["snapshot"], exp1["digest"], exp1["identity"], [t for _, t in ce.EXPORT_QUERIES])
    second = ce.ExportResult(exp2["snapshot"], exp2["digest"], exp2["identity"], [t for _, t in ce.EXPORT_QUERIES])
    if not all(r["ok"] for r in evidence.read("parent_reconciliation_export.json")):
        raise DriverRefused("an export session didn't reconcile")
    cross_record = evidence.read("cross_check.json")
    cross = ce.CrossCheck(cross_record["agrees"], cross_record["problems"], [t for _, t in ce.CROSS_CHECK_QUERIES])
    pin = ce.pin_from_evidence(first, second, cross, ce.Attestation(**attestation))
    registry = p.build_registry()
    context = sc.context_config(registry_digest=registry.digest, schema_digest=pin.pinned_digest)
    ce.pin_context(context, registry=registry, snapshot=first.snapshot, snapshot_trust=pin,
                   reviewer=attestation["reviewer"])
    if eb.check_no_time_types(first.snapshot):
        raise DriverRefused("time-typed columns: Gate 2A's parent read would mask them")

    chain = p.run_offline_chain(first.snapshot)
    for key, digest in manifest["invariant_digests"].items():
        if chain.digests[key] != digest:
            raise DriverRefused(f"{key} differs from the reviewed manifest")
    setup = _setup_sql(manifest)
    scenarios = {v: p.live_scenario(chain, variant=v, setup_sql=setup) for v in LOCK_BY_VARIANT}
    prepared = {v: eb.scenario_digest(s) for v, s in scenarios.items()}
    if prepared != manifest["scenario_digests"]:
        raise DriverRefused("the prepared scenarios differ from the reviewed ones")
    expected = p.expected_initial_state(chain)
    types = {tuple(k.split(".")): v for k, v in manifest["operations"]["OP-6i"]["column_types"].items()}
    tables = sorted(expected)
    reconciliations: list[dict[str, Any]] = []

    def independent_initial(conn):
        return eb.independent_state_check(conn, expected, types)

    def ind_expected():
        return tuple(pr.ExpectedStatement(t, prm) for t, prm in eb.independent_statements(expected, types))

    checks, parent_reads = [], []
    for _ in range(manifest["operations"]["OP-6i"]["repeats"]):
        deadline.check("construction verification")
        effects.reset(setup, list(chain.seed))
        parent_reads.append(effects.snapshot())
        check, rec = parent_operation(effects, manifest, "op6i", independent_initial, ind_expected())
        checks.append(check)
        reconciliations.append(rec)
    construction = eb.verify_initial_states(expected, parent_reads, checks)
    if construction["problems"]:
        raise DriverRefused(f"construction verification failed: {construction['problems']}")

    def final_read(trace) -> dict[str, Any]:
        deadline.check("an independent final read")
        state, rec = parent_operation(effects, manifest, "op6f", lambda conn: eb.independent_final_read(conn, tables),
                                      tuple(pr.ExpectedStatement(t) for t in eb.final_read_statements(tables)))
        reconciliations.append(rec)
        return state

    def oracle(state):
        return props.check_invariants(state)

    runs: list[dict[str, Any]] = []

    def observe(variant):
        def check(trace):
            independent = final_read(trace)
            if eb.state_digest(independent) != eb.state_digest(trace.final_state):
                raise DriverRefused(f"independent final read disagrees with the parent read on {variant}")
            record = eb.run_record(trace, variant=variant, scenario_sha=prepared[variant], oracle=oracle)
            record["initial_problems"] = p.check_initial_state(expected, trace.initial_state)
            runs.append(record)
            return record["requirement_ids"]
        return check

    serial = {}
    for order in (["a", "b"], ["b", "a"]):
        deadline.check("a serial reference run")
        result = effects.plain_serial(scenarios["buggy"], order)
        if result.get("flags"):
            raise DriverRefused("a serial reference run was flagged")
        serial["".join(order)] = props.canonical(result["final_state"])
    budget = LiveBudget(**manifest["operations"]["OP-3"]["budget"])
    explored = {}
    for variant in ("buggy", "fixed"):
        deadline.check(f"the {variant} exploration")
        explored[variant] = effects.explore(scenarios[variant], budget, observe(variant))
    buggy_runs = [r for r in runs if r["variant"] == "buggy"]
    candidate = next((r for r in buggy_runs if {"R1", "R2"} & set(r["requirement_ids"])), None)
    fixed_runs = [r for r in runs if r["variant"] == "fixed"]

    replay: dict[str, Any] = {"result": None}
    if candidate is not None:
        target = next(t for t in explored["buggy"].traces if t.evidence.run_id == candidate["run_id"])
        base = rp.Attempt(rp.server_choice_signature(target), target.fingerprint(), target.outcome,
                          frozenset(candidate["requirement_ids"]),
                          eb.canonical_digest(props.canonical(target.final_state)),
                          target.inconclusive)
        independent_each = []

        def next_attempt(_i):
            deadline.check("a confirmation attempt")
            trace = effects.run(scenarios["buggy"], ExplicitSchedule(list(target.chosen_schedule), strict=True))
            independent = final_read(trace)
            same = eb.state_digest(independent) == eb.state_digest(trace.final_state)
            independent_each.append(same and sorted({v.split()[0] for v in oracle(independent)}) ==
                                    sorted({v.split()[0] for v in oracle(trace.final_state)}))
            return rp.Attempt(rp.server_choice_signature(trace), trace.fingerprint(), trace.outcome,
                              frozenset(v.split()[0] for v in oracle(trace.final_state)),
                              eb.canonical_digest(props.canonical(trace.final_state)),
                              trace.inconclusive or not trace.scoreable)

        confirmation = rp.confirm(base, next_attempt)
        replay = {"result": confirmation.result, "classes": confirmation.classes, "attempts": confirmation.attempts,
                  "independent_oracle_each_attempt": all(independent_each)}

    deadline.check("the batch-end export")
    final_export, rec = parent_operation(effects, manifest, "op2d", ce.export_snapshot, _export_expected(ce, pr))
    reconciliations.append(rec)
    if ce.structural_digest(final_export.snapshot) != exp1["structural_digest"]:
        raise DriverRefused("the batch-end catalog differs structurally from the pinned one")

    binding = eb.bind(chain=chain, prepared=prepared, runs=runs,
                      verified_initial_digest=construction["verified_initial_digest"])
    bundle = {
        "chain": {"stages": chain.stages, "rechecked": True},
        "digests": {**chain.digests, "setup_sql": manifest["schema"]["setup_sql_sha256"],
                    "app_source": manifest["source_sha256"]["tests/live_app/reservations.py"],
                    "oracle_source": manifest["source_sha256"]["tests/live_app/properties.py"],
                    "harness_source": manifest["source_sha256"]["research_tools/track_a_run.py"], "binding": binding},
        "catalog_export": {"source_label": pin.source_label, "verify_context": "MATCH", "exports_agree": True,
                           "cross_check_agrees": True, "cluster_name": first.identity["cluster_name"],
                           "coverage_complete": True, "statements_reconciled": all(r["ok"] for r in reconciliations),
                           "time_typed_columns": []},
        "initial_states": {**construction, "runs_checked": len(runs),
                           "problems": construction["problems"] + [x for r in runs for x in r["initial_problems"]]},
        "runs": runs, "serial_states": serial,
        "oracle": {"source_sha256": sha256_file(ROOT / "tests/live_app/properties.py"),
                   "pinned_sha256": manifest["source_sha256"]["tests/live_app/properties.py"],
                   "imports_tool_or_app": False, "reads": "parent_final_state_and_independent_read"},
        "buggy_exploration": {"candidate_requirements": candidate["requirement_ids"] if candidate else [],
                              "candidate_run_id": candidate["run_id"] if candidate else None},
        "fixed_exploration": {"same_workload_digest": chain.digests["spec"],
                              "target_violations": [r["run_id"] for r in fixed_runs
                                                    if {"R1", "R2"} & set(r["requirement_ids"])],
                              "exhausted": bool(explored["fixed"].exhausted),
                              "complete_schedule_coverage": bool(explored["fixed"].complete_schedule_coverage),
                              "inconclusive_runs": sum(r["inconclusive"] for r in fixed_runs)},
        "replay": replay, "parent_reconciliation": reconciliations,
    }
    # Computed, never assumed: anything not accounted for keeps G8 from passing.
    bundle["unknown_effects"] = [r["run_id"] for r in runs if r["flags"] or r["opaque_steps"] or r["refusals"]
                                 or not r["scoreable"]] + [x["op"] for x in reconciliations if not x["ok"]]
    bundle["missing_evidence"] = [name for name, present in (
        ("buggy_candidate", candidate is not None), ("replay", replay.get("result") is not None),
        ("fixed_runs", bool(fixed_runs)), ("serial_states", len(serial) == 2),
        ("construction_checks", len(checks) == manifest["operations"]["OP-6i"]["repeats"])) if not present]
    return bundle


# ------------------------------------------------------------------ the real effects (constructed only by main)


class RealEffects:  # pragma: no cover - never constructed in tests; running it needs an approved, live cluster
    def __init__(self, manifest: dict[str, Any]) -> None:
        import psycopg

        from deployspecter.config import load_settings
        from deployspecter.database import Sandbox
        from deployspecter.live.explore import explore_live
        from deployspecter.live.runner import LiveLimits
        from deployspecter.live.serverlog import ServerLog
        from deployspecter.live.supervisor import SupervisedRunner, SupervisorLimits

        self.psycopg, self.explore_live = psycopg, explore_live
        self.settings = load_settings()
        self.box = Sandbox(self.settings).__enter__()
        limits = manifest["safety_ceilings"]
        self.runner = SupervisedRunner(self.box, LiveLimits(**limits["LiveLimits"]),
                                       SupervisorLimits(**limits["SupervisorLimits"]))
        self.log_conn = psycopg.connect(self.settings.test_dsn, autocommit=True,
                                        application_name="ds_parent_log_reader")
        self.log = ServerLog(lambda q, prm=None: self.log_conn.execute(q, prm).fetchone())

    def preflight(self):
        return self.runner.preflight_problems()

    def reset(self, setup_sql, seed):
        self.box.reset(setup_sql, seed)

    def snapshot(self):
        return self.box.snapshot()

    @contextmanager
    def parent_connection(self, tag):
        with self.psycopg.connect(self.settings.test_dsn, autocommit=True, application_name=tag) as conn:
            yield conn

    def log_position(self):
        self.log.position()

    def log_mark(self, token):
        from deployspecter.research.track_a import parent_reconciliation as pr
        self.log_conn.execute(pr.marker_sql(token))

    def log_read(self):
        return self.log.read()

    def plain_serial(self, scenario, order):
        return self.runner.plain_serial(scenario, order)

    def run(self, scenario, schedule):
        return self.runner.run(scenario, schedule)

    def explore(self, scenario, budget, check):
        return self.explore_live(self.runner, scenario, budget, check=check)

    def close(self):
        self.log_conn.close()
        self.box.__exit__(None, None, None)


# ------------------------------------------------------------------ command line


@dataclass
class Arguments:
    command: str
    manifest: Path
    manifest_sha256: str
    approval: Path | None
    attestation: Path | None
    run_dir: Path | None


def parse(argv: list[str]) -> Arguments:
    parser = argparse.ArgumentParser(prog="track_a_run", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("plan", "export", "run"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--attestation", type=Path)
    parser.add_argument("--run-dir", type=Path)
    a = parser.parse_args(argv)
    return Arguments(a.command, a.manifest, a.manifest_sha256, a.approval, a.attestation, a.run_dir)


def main(argv: list[str], effects_factory: Callable[[dict[str, Any]], Effects] | None = None) -> int:
    args = parse(argv)
    manifest = load_manifest(args.manifest, args.manifest_sha256)
    if args.command == "plan":
        for op, spec in manifest["operations"].items():
            print(f"{op}: {spec['action']}")
        return 0
    approval = load_approval(args.approval, manifest_sha256=args.manifest_sha256, phase=args.command)
    verify_sources(manifest)
    factory = effects_factory or RealEffects
    root = ROOT / manifest["artifacts_root"]
    if args.command == "export":
        evidence = EvidenceDir.create(root)
        evidence.write("approval_export.json", approval)
        effects = factory(manifest)
        try:
            digest = export_phase(effects, manifest, evidence)
        except Exception as exc:
            evidence.write("failure.json", {"classification": "refused_fail_closed" if isinstance(exc, DriverRefused)
                                            else "unknown_effect_stop", "error": type(exc).__name__,
                                            "detail": str(exc)[:2000], "retried": False})
            raise
        finally:
            getattr(effects, "close", lambda: None)()
        print(f"export digest for the reviewer to attest: {digest}\nrun directory: {evidence.path}")
        return 0
    if args.run_dir is None or args.attestation is None:
        raise DriverRefused("the run phase needs --run-dir from the export and --attestation")
    if args.run_dir.resolve().parent != root.resolve():
        raise DriverRefused("the run directory isn't under the manifest's evidence root")
    evidence = EvidenceDir(args.run_dir)
    evidence.write("approval_run.json", approval)
    attestation = json.loads(args.attestation.read_text())
    evidence.write("attestation.json", attestation)
    effects = factory(manifest)
    try:
        bundle = run_phase(effects, manifest, evidence, attestation)
    except Exception as exc:
        unknown = not isinstance(exc, DriverRefused)
        evidence.write("failure.json", {"classification": "unknown_effect_stop" if unknown else "refused_fail_closed",
                                        "error": type(exc).__name__, "detail": str(exc)[:2000], "retried": False})
        raise
    finally:
        getattr(effects, "close", lambda: None)()
    from deployspecter.research.track_a import acceptance as acc
    verdict, judgements = acc.judge(bundle)
    evidence.write("bundle.json", bundle)
    evidence.write("acceptance.json", {"verdict": verdict, "gates": [j.__dict__ for j in judgements]})
    print(f"acceptance: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
