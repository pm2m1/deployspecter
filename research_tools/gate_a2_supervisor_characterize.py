"""Gate A2.3: one characterization run of the frozen Gate 2A supervisor. Research-only. Needs a signed approval.

    plan          print the steps, the stop conditions and the operator's commands; touches nothing
    characterize  run it once; needs this package's manifest digest and an approval file naming it

Its only purpose is to observe what the existing supervisor does, from PostgreSQL's log, on a disposable synthetic
cluster: whether the gate2a_audit template agrees with a real log, how integer arrays are logged, whether the reader
boundary holds, and what the role catalog shows around a run. It is not Gate A2's buggy/fixed demonstration, not an
evaluation and not a detection. Nothing is scored. There is one supervised call, and it isn't retried.

One batch (SegmentedRun), every parent statement on a tagged session reconciled by Gate A1's code:

    segment 0   op0p preflight; op0p role catalog ("before the sandbox bootstrap")
    gap 1       Sandbox construction; B5 option 1 applied to its admin and monitor sessions
    segment 1   op0p role catalog; op0r seeded reset; op6i typed check; op6f typed read (the verified initial digest)
    gap 2       one SupervisedRunner.run of the pinned synthetic scenario
    segment 2   op0p role catalog ("after"); op6f final read; op9c cleanup; the whole-batch line check

The parent is a superuser on a disposable cluster. Reconciliation detects deviation; it doesn't prevent it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gate_a2_run as drv  # noqa: E402  (SegmentedRun, preconditions, Gate A1's batch and connections)

a1, v2, ROOT, DriverRefused = drv.a1, drv.v2, drv.ROOT, drv.DriverRefused
PACKAGE = "gate-a2-supervisor-characterization"
VERSION = 1
PHASE = "gate_a2_supervisor_characterization"
VARIANT = "fixed"  # the pinned scenario; no bug is sought, so the variant only has to be a reviewed one


def load_package(path: Path, expected_sha256: str) -> dict[str, Any]:
    from deployspecter.research.track_a import gate_a2_controls as c

    if v2.sha256_file(path) != expected_sha256:
        raise DriverRefused("the characterization manifest's digest isn't the one given")
    m = json.loads(path.read_text())
    if m.get("package") != PACKAGE or m.get("version") != VERSION:
        raise DriverRefused(f"not a version {VERSION} supervisor characterization manifest")
    if m.get("scope") != "supervisor_characterization_only" or m.get("never_heldout_evidence") is not True:
        raise DriverRefused("this package characterizes the supervisor only")
    policy = m.get("client_connection_policy", {})
    if "psycopg_prepare_threshold" not in policy or policy["psycopg_prepare_threshold"] is not None \
            or policy.get("gate2a_harness_admin_and_monitor") != "prepare_threshold=None after Sandbox returns":
        raise DriverRefused("the manifest doesn't declare B5 option 1 for the parent and harness sessions")
    if str(m["safety_ceilings"]["session_options"].get("extra_float_digits")) != "1":
        raise DriverRefused("the manifest doesn't pin extra_float_digits=1")
    if m.get("b2_policy", {}).get("option") != "A" or not m["b2_policy"].get("limitations"):
        raise DriverRefused("the manifest doesn't state the B2 option A exception and its limitations")
    if m.get("plain_serial") != "refused":
        raise DriverRefused("plain_serial must stay refused")
    try:
        t = c.target(m)
    except c.ControlRefused as exc:
        raise DriverRefused(str(exc)) from None
    if (str(m["container"]["host"]), str(m["container"]["port"]), m["container"]["name"], m["cluster"]["database"],
            m["cluster"]["admin_user"], m["cluster"]["authentication_method"]) != (
            t["host"], t["port"], t["container_name"], t["database"], t["admin_user"], t["authentication_method"]):
        raise DriverRefused("the manifest's container and cluster don't name its target")
    audit = m.get("gate2a_audit", {})
    if not audit.get("connectable_databases") or not audit.get("log_path") or not audit.get("tables"):
        raise DriverRefused("the manifest doesn't fix the Gate 2A audit's inputs")
    return m


def scenario(m: dict[str, Any]) -> tuple[Any, Any]:
    """The pinned synthetic scenario and its offline chain, checked against the manifest's digests."""
    _ce, _ch, eb, _pr, p, _sc = a1._modules()
    chain = p.run_offline_chain()
    s = p.live_scenario(chain, variant=VARIANT, setup_sql=drv.setup_sql(m))
    if eb.scenario_digest(s) != m["scenario"]["digest"]:
        raise DriverRefused("the scenario differs from the reviewed one")
    if list(s.actors[0].kwargs) and s.actors[0].kwargs.get("lock_row") is not True:
        raise DriverRefused("the scenario isn't the reviewed variant")
    return s, chain


def characterize(effects: Any, m: dict[str, Any], evidence: Any, *, construct: Callable[[], Any],
                 runner_factory: Callable[[Any], Any]) -> dict[str, Any]:
    from deployspecter.live.runner import ExplicitSchedule
    from deployspecter.research.track_a import gate2a_audit as ga

    _ce, ch, eb, _pr, p, _sc = a1._modules()
    s, chain = scenario(m)
    expected = p.expected_initial_state(chain)
    types = {tuple(k.split(".")): v for k, v in m["state_checks"]["column_types"].items()}
    tables = sorted(expected)
    seg = drv.SegmentedRun(effects, m, evidence)

    rows = seg.run("op0p", [(t, ()) for t in ch.PREFLIGHT])
    problems = ch.judge_preflight(rows, database=m["target"]["database"], user=m["target"]["admin_user"])
    evidence.write("preflight.json", {"rows": rows, "problems": problems})
    if problems:
        raise DriverRefused(f"preflight: {problems}")
    seg.observe_roles("before the sandbox bootstrap")
    box = seg.bootstrap(construct)

    seg.current.reset(ch.reset_statements(drv.setup_sql(m), list(chain.seed)))
    check = seg.op("op6i", list(eb.independent_statements(expected, types)),
                   lambda conn: eb.independent_state_check(conn, expected, types))
    read = seg.op("op6f", [(t, ()) for t in eb.final_read_statements(tables)],
                  lambda conn: eb.independent_final_read(conn, tables))
    verified = eb.verify_initial_states(expected, [read], [check])
    evidence.write("construction.json", verified)
    if verified["problems"]:
        raise DriverRefused(f"the construction check failed: {verified['problems']}")

    runner = runner_factory(box)
    trace = seg.gate2a(lambda: runner.run(s, ExplicitSchedule(list(m["scenario"]["schedule"]))), mode="stepped",
                       scenario=s, verified_initial_digest=verified["verified_initial_digest"])
    final = seg.op("op6f", [(t, ()) for t in eb.final_read_statements(tables)],
                   lambda conn: eb.independent_final_read(conn, tables))
    same = eb.state_digest(final) == eb.state_digest(trace.final_state)
    seg.coverage.add(f"run {trace.evidence.run_id}", "final state independently read after the run",
                     ga.VERIFIED if same else ga.REFUTED,
                     "a reconciled op6f read in the next segment equals the supervisor's final snapshot" if same
                     else "the reconciled final read differs from the supervisor's final snapshot")
    seg.run("op9c", ch.cleanup_statements())
    report = seg.finish()
    for close in (getattr(runner, "close", None), getattr(box, "close", None)):  # after the batch's last marker
        if close is not None:
            close()
    evidence.write("trace_summary.json", {
        "run_id": trace.evidence.run_id, "scoreable": trace.scoreable, "outcome": trace.outcome,
        "flags": [f.model_dump() for f in trace.evidence.flags], "escalation": trace.evidence.escalation,
        "reconciliation": trace.evidence.reconciliation, "quiescent": trace.evidence.quiescent,
        "role_dropped": trace.evidence.role_dropped, "session_counter_delta": trace.evidence.session_counter_delta,
        "confinement": trace.evidence.confinement, "initial_digest": eb.canonical_digest(trace.initial_state),
        "final_digest": eb.canonical_digest(trace.final_state), "final_read_agrees": same})
    failed = report["classification"] in ("REFUTED", "INDETERMINATE")
    summary = {"outcome": "NOT_CHARACTERIZED" if failed else "CHARACTERIZED_WITH_DISCLOSED_GAPS",
               "classification": report["classification"], "counts": report["counts"],
               "never_all_sql_verified": True, "retried": False,
               "evidence_kind": "supervisor characterization on a disposable synthetic cluster; not the buggy/fixed "
                                "demonstration, not an evaluation, not a detection, not held-out evidence"}
    evidence.write("summary.json", summary)
    return summary


class RealCharacterization:  # pragma: no cover - needs an approved, live disposable cluster
    """Gate A2's segment effects, the manifest-built Sandbox and the frozen supervisor with the manifest's limits."""

    def __init__(self, m: dict[str, Any]) -> None:
        import os

        from dotenv import dotenv_values

        from deployspecter.research.track_a import gate_a2_controls as c
        self.keywords = drv.preconditions(m, environ=dict(os.environ), dotenv=dotenv_values(ROOT / ".env"),
                                          modules=list(sys.modules))
        self.effects = drv.SegmentEffects(m)
        self.settings = c.harness_settings(self.keywords)
        self.m = m

    def construct(self) -> Any:
        from deployspecter.database import Sandbox
        return Sandbox(self.settings)

    def runner(self, box: Any) -> Any:
        from deployspecter.live.runner import LiveLimits
        from deployspecter.live.supervisor import SupervisedRunner, SupervisorLimits
        g = self.m["safety_ceilings"]["gate2a"]
        return SupervisedRunner(box, LiveLimits(**g["LiveLimits"]), SupervisorLimits(**g["SupervisorLimits"]))


def main(argv: list[str], factory: Callable[[dict[str, Any]], Any] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gate_a2_supervisor_characterize")
    parser.add_argument("command", choices=("plan", "characterize"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--approval", type=Path)
    args = parser.parse_args(argv)
    m = load_package(args.manifest, args.manifest_sha256)
    if args.command == "plan":
        _ce, ch, *_ = a1._modules()
        for step in m["steps"]:
            print(step)
        print("\nstop conditions:")
        for stop in m["stop_conditions"]:
            print(f"  {stop}")
        print("\noperator commands (run by the operator, never by this driver):")
        for command in ch.operator_commands(m):
            print(f"  {command}")
        return 0
    if m.get("status") != "WAITING_FOR_SEPARATE_EXECUTION_APPROVAL":
        raise DriverRefused(f"the package isn't waiting for approval ({m.get('status')})")
    raw = args.approval.read_bytes() if args.approval is not None else b""
    approval = v2.load_approval(args.approval, manifest_sha256=args.manifest_sha256, phase=PHASE)
    if args.approval.read_bytes() != raw or json.loads(raw) != approval:
        raise DriverRefused("the approval file changed while it was being read")
    v2.verify_sources(m)
    evidence = v2.EvidenceDir.create(ROOT / m["artifacts_root"])
    a1.record_approval(evidence, args.approval, raw)
    real = None
    try:
        real = (factory or RealCharacterization)(m)
        summary = characterize(real.effects, m, evidence, construct=real.construct, runner_factory=real.runner)
    except BaseException as exc:  # including an operator's interrupt: always leave a record, never retry
        kind = ("refused_fail_closed" if isinstance(exc, DriverRefused) else
                "interrupted_stop" if isinstance(exc, KeyboardInterrupt) else "unknown_effect_stop")
        evidence.write("failure.json", {"classification": kind, "error": type(exc).__name__,
                                        "detail": str(exc)[:2000], "retried": False,
                                        "cleanup": "no further SQL is issued; the operator must run docker stop"})
        raise
    finally:
        if real is not None:
            getattr(real.effects, "close", lambda: None)()
    print(f"Gate A2 supervisor characterization: {summary['outcome']} ({summary['classification']})")
    return 0 if summary["outcome"] == "CHARACTERIZED_WITH_DISCLOSED_GAPS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
