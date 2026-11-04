"""Gate A2 driver with the Gate A2.1 and A2.2 controls (manifest version 5). Research-only. NOT EXECUTABLE.

    plan     print the steps and the execution blockers; touches nothing
    export   the reconciled export batch; refused while the manifest lists execution blockers
    run      refused: the Gate 2A section waits for the reviewed whole-batch audit

What it takes from Gate A1, imported unchanged, not copied: the reconciled parent operation (``Batch``), the one log
reader, the whole-batch session audit, the fixed-target unprepared connections (``RealEffects``), and the preflight,
reset, export, cross-check, typed-state and cleanup statement sets that Gate A1 version 4 reconciled on PostgreSQL 16.

What it adds (``gate_a2_controls``): refusal of model credentials, model modules and every libpq environment variable;
one reviewed credential source; the protected development database as a forbidden target; the server identity bound
across batches; a refused partial reset; per-attempt state records for Gate 2A runs.

Gate A2.2 adds ``SegmentedRun``: the log reader closes before each Gate 2A call and a new one opens after it returns,
so no Gate A2 session exists inside the supervisor's protected window, and each gap is audited from the persisted log
against the template in ``gate2a_audit``. It is tested only with fakes.

Nothing the command line reaches constructs Gate 2A's supervisor, sandbox or child. The supervisor's per-run reset,
snapshots and role setup stay its own and are audited, not replaced (see GATE_A2_2_SUPERVISOR_AUDIT.md). The parent
is a superuser on a disposable cluster: reconciliation detects deviation, it doesn't prevent it.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gate_a1_characterize as a1  # noqa: E402  (the verified Gate A1 batch, reader, audit and connections)

ROOT = a1.ROOT
v2 = a1.a2  # track_a_run: manifest digest, approval and evidence helpers, unchanged
MANIFEST = "gate-a2-execution-manifest"
VERSION = 5
APPLICATION = "reservations"
LOCK_BY_VARIANT = {"buggy": False, "fixed": True}
# The Gate 2A section (serial references, exploration, confirmation, batch-end export) isn't wired to the command line.
# Lifting the manifest's blockers alone can't make it run: this constant must change too, in a reviewed commit.
GATE2A_SECTION_IMPLEMENTED = False
DriverRefused = a1.DriverRefused


def _controls():
    from deployspecter.research.track_a import gate_a2_controls as c
    return c


# ------------------------------------------------------------------ manifest and preconditions


def load_manifest(path: Path, expected_sha256: str) -> dict[str, Any]:
    c = _controls()
    if v2.sha256_file(path) != expected_sha256:
        raise DriverRefused("the Gate A2 manifest's digest isn't the one given")
    m = json.loads(path.read_text())
    if m.get("manifest") != MANIFEST or m.get("version") != VERSION:
        raise DriverRefused(f"not a version {VERSION} Gate A2 manifest")
    if m.get("context") != "synthetic_demonstration" or m.get("never_heldout_evidence") is not True:
        raise DriverRefused("the manifest isn't a synthetic demonstration")
    if m.get("application", {}).get("name") != APPLICATION:
        raise DriverRefused("only the synthetic reservations application is supported")
    variants = m.get("variants", {})
    if {k: variants[k].get("lock_row") for k in variants if k in LOCK_BY_VARIANT} != LOCK_BY_VARIANT:
        raise DriverRefused("variants must be exactly the harness-owned lock_row switch")
    policy = m.get("client_connection_policy")
    if not isinstance(policy, dict) or "psycopg_prepare_threshold" not in policy \
            or policy["psycopg_prepare_threshold"] is not c.PREPARE_THRESHOLD:
        raise DriverRefused("the manifest doesn't declare Gate A2's connection policy (automatic preparation off)")
    if str(m["safety_ceilings"]["session_options"].get("extra_float_digits")) != "1":
        raise DriverRefused("the manifest doesn't pin extra_float_digits=1, which the float8 log model relies on")
    try:
        t = c.target(m)
    except c.ControlRefused as exc:
        raise DriverRefused(str(exc)) from None
    # Gate A1's RealEffects and Batch read these keys; they must name the same target.
    if (str(m["container"]["host"]), str(m["container"]["port"]), m["container"]["name"], m["cluster"]["database"],
            m["cluster"]["admin_user"], m["cluster"]["authentication_method"]) != (
            t["host"], t["port"], t["container_name"], t["database"], t["admin_user"], t["authentication_method"]):
        raise DriverRefused("the manifest's container and cluster don't name its target")
    if not isinstance(m.get("execution_blockers"), list):
        raise DriverRefused("the manifest doesn't list its execution blockers")
    audit = m.get("gate2a_audit")
    if not isinstance(audit, dict) or not audit.get("connectable_databases") or not audit.get("log_path"):
        raise DriverRefused("the manifest doesn't fix the Gate 2A audit's databases and log path")
    return m


def require_executable(manifest: Mapping[str, Any], command: str) -> None:
    blockers = manifest["execution_blockers"]
    if blockers or manifest.get("status") != "WAITING_FOR_EXECUTION_APPROVAL":
        names = ", ".join(b["id"] for b in blockers) or manifest.get("status")
        raise DriverRefused(f"Gate A2 isn't executable ({names}); no {command} is run")
    if command == "run" and not GATE2A_SECTION_IMPLEMENTED:
        raise DriverRefused("the Gate 2A section waits for the reviewed whole-batch audit; no run is made")


def preconditions(manifest: Mapping[str, Any], *, environ: Mapping[str, str], dotenv: Mapping[str, str | None],
                  modules: list[str]) -> dict[str, dict[str, str]]:
    """Every check that needs no connection. Returns the target's keywords; refuses before anything connects."""
    c = _controls()
    problems = c.environment_problems(environ) + c.dotenv_problems(dotenv) + c.module_problems(modules)
    if problems:
        raise DriverRefused("; ".join(problems))
    from psycopg.conninfo import conninfo_to_dict
    try:  # libpq's parse errors can quote part of the URL, the password included, so none is kept
        parsed = conninfo_to_dict(dotenv["DATABASE_URL"])
    except Exception:
        raise DriverRefused("DATABASE_URL couldn't be parsed as a connection string") from None
    try:
        return c.connection_keywords(parsed, manifest)
    except c.ControlRefused as exc:
        raise DriverRefused(str(exc)) from None


def setup_sql(manifest: Mapping[str, Any]) -> str:
    import hashlib

    _ce, _ch, _eb, _pr, _p, sc = a1._modules()
    from live_app import reservations as app
    if hashlib.sha256(app.SETUP_SQL.encode()).hexdigest() != manifest["schema"]["setup_sql_sha256"] \
            or sc.SETUP_SQL_SHA256 != manifest["schema"]["setup_sql_sha256"]:
        raise DriverRefused("the setup SQL isn't the reviewed one")
    return app.SETUP_SQL


# ------------------------------------------------------------------ reconciled parent batches


def reset_action(statements: list[tuple[str, tuple]]) -> Callable[[Any], None]:
    """A reset on its tagged session. A statement that fails stops the batch where it is: no repair, no retry."""
    def action(conn: Any) -> None:
        for i, (text, params) in enumerate(statements):
            try:
                if params:
                    conn.execute(text, params)
                else:
                    conn.execute(text)
            except Exception as exc:
                raise DriverRefused(f"the reset stopped at statement {i + 1} of {len(statements)} "
                                    f"({type(exc).__name__}); the schema is left as it is, for the operator") from None
    return action


class ParentBatch:
    """One Gate A1 batch: markers around it, reconciled operations inside, the whole-batch audit at the end."""

    def __init__(self, effects: Any, manifest: Mapping[str, Any], evidence: Any, name: str) -> None:
        self.effects, self.manifest, self.evidence, self.name = effects, manifest, evidence, name
        self.batch = a1.Batch(effects, dict(manifest))
        nonce = secrets.token_hex(6)
        self.start, self.end = f"ds_parent_mark_batch_start_{nonce}", f"ds_parent_mark_batch_end_{nonce}"
        self.db, self.user = manifest["target"]["database"], manifest["target"]["admin_user"]
        effects.log_position("batch")
        effects.log_mark(self.start)

    def op(self, name: str, statements: list[tuple[str, tuple]], action: Callable[[Any], Any]) -> Any:
        return self.batch.op(name, self.db, statements, action)

    def run(self, name: str, statements: list[tuple[str, tuple]]) -> Any:
        return self.op(name, statements, a1._execute(statements))

    def reset(self, statements: list[tuple[str, tuple]]) -> None:
        self.op("op0r", statements, reset_action(statements))

    def close(self) -> dict[str, Any]:
        _ce, ch, *_ = a1._modules()
        self.effects.log_mark(self.end)
        raw, rows = self.batch.read_window(self.start, self.end, "batch")
        self.reader = self.batch.established_reader(raw, self.start, self.end, self.db)
        audit = ch.audit_batch(rows, reconciled_tags=self.batch.tags, user=self.user, databases={self.db},
                               reader=self.reader)
        self.evidence.write(f"parent_reconciliation_{self.name}.json", self.batch.records)
        record = {"ok": audit.ok, "problems": audit.problems, "sessions": audit.sessions}
        self.evidence.write(f"batch_audit_{self.name}.json", record)
        if not audit.ok:
            raise DriverRefused(f"the {self.name} batch audit failed: {audit.problems[:3]}")
        return record


def _preflight(batch: ParentBatch) -> list[list[tuple]]:
    _ce, ch, *_ = a1._modules()
    rows = batch.run("op0p", [(t, ()) for t in ch.PREFLIGHT])
    problems = ch.judge_preflight(rows, database=batch.db, user=batch.user)
    batch.evidence.write(f"preflight_{batch.name}.json", {"rows": rows, "problems": problems})
    if problems:
        raise DriverRefused(f"preflight: {problems}")
    return rows


def _identity_problems(identity: Mapping[str, Any], manifest: Mapping[str, Any]) -> list[str]:
    t = manifest["target"]
    problems = []
    if identity.get("cluster_name") != t["cluster_name"] or identity.get("database") != t["database"]:
        problems.append("the export isn't from the manifest's disposable cluster and test database")
    if not 160000 <= int(identity.get("server_version_num") or 0) < 170000:
        problems.append("the export isn't from PostgreSQL 16")
    if not identity.get("system_identifier") or not identity.get("postmaster_start_time"):
        problems.append("the export has no server instance identity")
    return problems


def export_batch(effects: Any, manifest: Mapping[str, Any], evidence: Any) -> dict[str, Any]:
    """OP-2: preflight, an empty reset, two exports, the cross-check, cleanup. Gate A1 v4's operations, unchanged."""
    ce, ch, *_ = a1._modules()
    setup = setup_sql(manifest)
    batch = ParentBatch(effects, manifest, evidence, "export")
    _preflight(batch)
    batch.reset(ch.reset_statements(setup, []))
    export_sql = [(t, ()) for _, t in ce.EXPORT_QUERIES]
    first = batch.op("op2a", export_sql, ce.export_snapshot)
    second = batch.op("op2b", export_sql, ce.export_snapshot)
    cross = batch.op("op2c", [(t, ()) for _, t in ce.CROSS_CHECK_QUERIES],
                     lambda conn: ce.cross_check_read(conn, first.snapshot))
    batch.run("op9c", ch.cleanup_statements())
    evidence.write("export_1.json", {"snapshot": first.snapshot, "digest": first.digest, "identity": first.identity,
                                     "structural_digest": ce.structural_digest(first.snapshot)})
    evidence.write("export_2.json", {"snapshot": second.snapshot, "digest": second.digest,
                                     "identity": second.identity})
    evidence.write("cross_check.json", {"agrees": cross.agrees, "problems": cross.problems})
    batch.close()
    problems = _identity_problems(first.identity, manifest)
    if first.identity != second.identity:
        problems.append("the two exports came from different server instances")
    if first.digest != second.digest or not cross.agrees:
        problems.append("the exports differ or the re-read disagrees; nothing to attest")
    if problems:
        raise DriverRefused("; ".join(problems))
    return {"digest": first.digest, "identity": first.identity}


@dataclass
class Construction:
    """What the Gate 2A section will need: verified, pinned, and bound to one server instance."""
    chain: Any
    scenarios: dict[str, Any]
    prepared: dict[str, str]
    expected: dict[str, Any]
    tables: list[str]
    verified_initial_digest: str
    identity: dict[str, Any]


def construction_batch(effects: Any, manifest: Mapping[str, Any], evidence: Any,
                       attestation: Mapping[str, Any]) -> Construction:
    """The run phase's parent part: pins, the same server, the same catalog, the bridged state verified twice."""
    ce, ch, eb, _pr, p, sc = a1._modules()
    c = _controls()
    exp1, exp2 = evidence.read("export_1.json"), evidence.read("export_2.json")
    if not all(r["ok"] for r in evidence.read("parent_reconciliation_export.json")) \
            or not evidence.read("batch_audit_export.json")["ok"]:
        raise DriverRefused("the export batch didn't reconcile")
    statements = [t for _, t in ce.EXPORT_QUERIES]
    first = ce.ExportResult(exp1["snapshot"], exp1["digest"], exp1["identity"], statements)
    second = ce.ExportResult(exp2["snapshot"], exp2["digest"], exp2["identity"], statements)
    cross_record = evidence.read("cross_check.json")
    cross = ce.CrossCheck(cross_record["agrees"], cross_record["problems"], [t for _, t in ce.CROSS_CHECK_QUERIES])
    pin = ce.pin_from_evidence(first, second, cross, ce.Attestation(**attestation))
    registry = p.build_registry()
    context = sc.context_config(registry_digest=registry.digest, schema_digest=pin.pinned_digest)
    ce.pin_context(context, registry=registry, snapshot=first.snapshot, snapshot_trust=pin,
                   reviewer=attestation["reviewer"])
    if eb.check_no_time_types(first.snapshot) or c.snapshot_type_problems(first.snapshot):
        raise DriverRefused("the schema has columns whose Gate 2A read and reconciled read aren't comparable")
    chain = p.run_offline_chain(first.snapshot)
    for key, digest in manifest["invariant_digests"].items():
        if chain.digests[key] != digest:
            raise DriverRefused(f"{key} differs from the reviewed manifest")
    setup = setup_sql(manifest)
    scenarios = {v: p.live_scenario(chain, variant=v, setup_sql=setup) for v in LOCK_BY_VARIANT}
    prepared = {v: eb.scenario_digest(s) for v, s in scenarios.items()}
    if prepared != manifest["scenario_digests"]:
        raise DriverRefused("the prepared scenarios differ from the reviewed ones")
    expected = p.expected_initial_state(chain)
    types = {tuple(k.split(".")): v for k, v in manifest["state_checks"]["column_types"].items()}
    tables = sorted(expected)

    batch = ParentBatch(effects, manifest, evidence, "construction")
    _preflight(batch)
    batch.reset(ch.reset_statements(setup, []))
    again = batch.op("op2a", [(t, ()) for t in statements], ce.export_snapshot)
    problems = []
    if again.identity != first.identity:
        problems.append("the run batch isn't on the server instance the reviewer attested")
    if ce.structural_digest(again.snapshot) != exp1["structural_digest"]:
        problems.append("the catalog differs structurally from the attested export")
    if problems:
        raise DriverRefused("; ".join(problems))
    seeded = ch.reset_statements(setup, list(chain.seed))
    checks, reads = [], []
    for _ in range(manifest["state_checks"]["repeats"]):
        batch.reset(seeded)
        checks.append(batch.op("op6i", list(eb.independent_statements(expected, types)),
                               lambda conn: eb.independent_state_check(conn, expected, types)))
        reads.append(batch.op("op6f", [(t, ()) for t in eb.final_read_statements(tables)],
                              lambda conn: eb.independent_final_read(conn, tables)))
    batch.run("op9c", ch.cleanup_statements())
    verified = eb.verify_initial_states(expected, reads, checks)
    evidence.write("construction.json", {**verified, "repeats": len(reads), "identity": again.identity,
                                         "independent_digests": [x.digests for x in checks],
                                         "read_digests": [eb.state_digest(r) for r in reads]})
    batch.close()
    if verified["problems"]:
        raise DriverRefused(f"construction verification failed: {verified['problems']}")
    return Construction(chain, scenarios, prepared, expected, tables, verified["verified_initial_digest"],
                        again.identity)


# ------------------------------------------------------------------ B3: reader segments around Gate 2A calls


def gate2a_context(trace: Any, *, mode: str, scenario: Any, manifest: Mapping[str, Any], harness_pids: set[int],
                   first_run: bool) -> Any:
    """The audit context for one Gate 2A call, from the reviewed manifest, the prepared scenario, the harness sessions'
    connection info and the supervisor's in-process trace. Nothing comes from the log being audited."""
    from deployspecter.research.track_a import gate2a_audit as ga

    ceilings = manifest["safety_ceilings"]["gate2a"]
    live, sup = ceilings["LiveLimits"], ceilings["SupervisorLimits"]
    poll_s = float(sup.get("poll_s", 0.01))
    window = (float(sup["handshake_s"]) + float(live["run_s"]) + float(live.get("abort_grace_s", 3.0)) + 1.0
              + 3 * float(sup["escalation_grace_s"]) + float(sup["kill_wait_s"]))
    return ga.RunContext(
        run_id=trace.evidence.run_id, mode=mode, setup_sql=scenario.setup_sql, seed=ga.seed_tuples(scenario.seed),
        tables=tuple(sorted(manifest["gate2a_audit"]["tables"])), database=manifest["target"]["database"],
        harness_pids=frozenset(harness_pids), child_sessions=dict(trace.evidence.expected_sessions),
        first_run=first_run, connectable_databases=frozenset(manifest["gate2a_audit"]["connectable_databases"]),
        reset_lock_ms=int(sup["reset_lock_ms"]), reset_statement_ms=int(sup["reset_statement_ms"]),
        role_settings={"search_path": ga.RUN_SCHEMA, "statement_timeout": int(live["statement_ms"]),
                       "lock_timeout": int(live["lock_ms"]),
                       "idle_in_transaction_session_timeout": int(live["idle_in_transaction_ms"]),
                       "deadlock_timeout": int(live["deadlock_ms"])},
        log_path=manifest["gate2a_audit"]["log_path"],
        escalation=tuple(e["step"] for e in trace.evidence.escalation if not e.get("skipped_for_test")),
        max_polls=int(window / poll_s) + 10, max_log_reads=int(float(sup["log_wait_s"]) / 0.002) + 2,
        max_sweep_rounds=int(float(sup["quiescence_s"]) / 0.005) + 2,
        flags=tuple(f.detail for f in trace.evidence.flags),
        expected_rows=tuple((table, tuple(sorted(row.items()))) for table, rows in manifest["initial_rows"].items()
                            for row in rows))


class SegmentedRun:
    """Gate A1 batches as reader segments, with each Gate 2A call in the gap between two of them.

    The timeline of one call:

    1. A reconciled role-catalog observation in the current segment (B2 option A, "before").
    2. The segment closes (its end marker, then Gate A1's audit of it) and its reader disconnects.
    3. Gate 2A runs: its outside-session sweep, reset, snapshot, role, child, teardown and its own log reconciliation.
       No Gate A2 session exists until the call returns or raises.
    4. A new reader connects and starts the next segment. It reads the log from the batch's first byte offset, carried
       forward explicitly, until its own start marker is in a complete log (bounded wait).
    5. The gap is audited (gate2a_audit); then a role-catalog observation ("after") is judged against step 1.

    Each call ends as completed (a scoreable trace and a verified gap), flagged (a trace Gate 2A flagged), failed (the
    call raised) or interrupted_unknown_effects (an operator's interrupt). Anything but completed stops the batch:
    after a read-only "after" observation and a coverage record, never a retry.
    """

    def __init__(self, effects: Any, manifest: Mapping[str, Any], evidence: Any) -> None:
        from deployspecter.research.track_a import gate2a_audit as ga
        from deployspecter.research.track_a import gate2a_role_catalog as rc

        self.ga, self.rc, self.effects, self.manifest, self.evidence = ga, rc, effects, manifest, evidence
        self.coverage = ga.Coverage()
        self.segments: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.roles: dict[str, Any] = {}
        self.contracts: list[dict[str, Any]] = []
        self.last_roles: Any = None
        self.harness: Any = None
        self.first_run = True
        self.stopped: str | None = None
        from deployspecter.live.serverlog import LogUnverified
        try:
            effects.log_position("whole")  # the batch's one offset; every later reader starts from it
        except LogUnverified as exc:
            raise DriverRefused(f"the batch log has no position to start from: {exc}") from None
        self.current = self.new_segment(0)

    def new_segment(self, k: int) -> ParentBatch:
        return ParentBatch(self.effects, self.manifest, self.evidence, f"segment_{k:03d}")

    def op(self, name: str, statements: list[tuple[str, tuple]], action: Callable[[Any], Any]) -> Any:
        return self.current.op(name, statements, action)

    def run(self, name: str, statements: list[tuple[str, tuple]]) -> Any:
        return self.current.run(name, statements)

    def observe_roles(self, label: str) -> Any:
        """A reconciled, read-only role-catalog read in the current segment; never inside Gate 2A's window."""
        rc = self.rc
        observation = self.op(rc.OP, rc.statements(), lambda conn: rc.read(conn, label))
        self.roles[label] = observation
        self.last_roles = observation
        return observation

    def close_segment(self) -> None:
        self.current.close()
        self.segments.append({"start": self.current.start, "end": self.current.end,
                              "reader": self.current.reader.session})
        self.effects.reader_close()

    def reopen_and_read(self) -> list[Any]:
        from deployspecter.live.serverlog import LogUnverified
        try:
            self.effects.reader_open()
        except Exception as exc:
            raise DriverRefused(f"the next reader couldn't connect ({type(exc).__name__}); the gap isn't audited "
                                "and the batch stops, nothing is retried") from None
        self.current = self.new_segment(len(self.segments))
        until = a1.time.monotonic() + float(self.manifest["safety_ceilings"]["log_wait_s"])
        while True:  # a bounded wait for the logging collector, as Gate A1 waits for its end markers
            try:
                rows = self.ga.parse_log(self.effects.log_read("whole"))
            except self.ga.AuditError:
                rows = []  # a row still being written
            except LogUnverified as exc:
                raise DriverRefused(f"the batch log can't be read from its offset: {exc}") from None
            if any(r.message == f"statement: SELECT '{self.current.start}'" for r in rows):
                return rows
            if a1.time.monotonic() >= until:
                raise DriverRefused("the new segment's start marker never reached a complete log: an incomplete "
                                    "flush")
            a1.time.sleep(0.05)

    def gap_rows(self, rows: list[Any]) -> tuple[list[Any], str]:
        ga = self.ga
        try:
            after = ga.marker_row(rows, self.segments[-1]["end"])
            before = ga.marker_row(rows, self.current.start)
        except ga.AuditError as exc:
            raise DriverRefused(f"a segment boundary isn't in the log exactly once: {exc}") from None
        if not after.start < before.start:
            raise DriverRefused("the segment boundaries are out of order in the log")
        return [r for r in rows if after.start < r.start < before.start], before.session

    def role_conditions(self, hardened_after: bool) -> Any:
        return self.rc.Conditions(self.manifest["target"]["admin_user"],
                                  self.manifest["gate2a_audit"]["connectable_databases"], hardened_after)

    def bootstrap(self, construct: Callable[[], Any]) -> Any:
        """Construct Gate 2A's Sandbox in a gap of its own, apply B5 option 1 to it, then audit what that logged (B6).
        A role-catalog observation before it is required, and one after it is judged against it."""
        c = _controls()
        if self.last_roles is None:
            raise DriverRefused("no role-catalog observation was made before the sandbox bootstrap")
        before = self.last_roles
        self.close_segment()
        box, raised = None, None
        try:
            box = construct()
            want = {**{k: self.manifest["target"][k] for k in ("host", "hostaddr", "port")},
                    "dbname": self.manifest["target"]["database"], "user": self.manifest["target"]["admin_user"]}
            problems = c.apply_harness_policy(box, want)
            if problems:
                raise DriverRefused("B5 preparation policy: " + "; ".join(problems))
            self.harness = self.ga.Harness(box.admin.info.backend_pid, box.monitor.info.backend_pid,
                                           self.manifest["target"]["admin_user"],
                                           self.manifest["target"]["database"],
                                           self.manifest["target"]["initial_database"])
        except BaseException as exc:
            raised = exc
        rows = self.reopen_and_read()
        gap, next_reader = self.gap_rows(rows)
        cov = self.ga.Coverage()
        if raised is not None:
            cov.add(f"gap {len(self.segments)} (sandbox bootstrap)", "the sandbox was constructed", self.ga.REFUTED
                    if isinstance(raised, DriverRefused) else self.ga.INDETERMINATE, f"{type(raised).__name__}: "
                    f"{str(raised)[:200]}")
            self.calls.append({"call": "bootstrap", "outcome": "failed" if isinstance(raised, Exception)
                               else "interrupted_unknown_effects", "error": type(raised).__name__})
            if box is not None:
                getattr(box, "close", lambda: None)()
            self.stop_call(cov, raised, "the sandbox bootstrap")
        self.ga.audit_bootstrap_gap(gap, index=len(self.segments), prev_reader=self.segments[-1]["reader"],
                                    next_reader=next_reader, harness=self.harness, cov=cov)
        after = self.observe_roles("after the sandbox bootstrap")
        self.rc.judge_bootstrap(before, after, self.role_conditions(False), f"gap {len(self.segments)} (sandbox "
                                "bootstrap)", cov)
        self.calls.append({"call": "bootstrap", "outcome": "completed" if not cov.fails_closed else "refused"})
        self.conclude(cov, "the sandbox bootstrap")
        return box

    def gate2a(self, call: Callable[[], Any], *, mode: str, scenario: Any,
               verified_initial_digest: str | None) -> Any:
        """One Gate 2A call (``runner.run``) in its own gap. Never retried."""
        if self.harness is None:
            raise DriverRefused("Gate 2A's sandbox wasn't constructed in an audited gap")
        if mode != "stepped":
            # plain_serial's public result has no run id and no registered sessions, so its gap could only be bound
            # to a run id read from the log being audited. Refused, not weakened.
            raise DriverRefused("a plain_serial call can't be bound to a supervisor-reported run id")
        if self.last_roles is None:
            raise DriverRefused("no role-catalog observation was made before the Gate 2A call")
        before, hardened = self.last_roles, self.first_run
        self.close_segment()
        trace, error, raised = None, None, None
        try:
            result = trace = call()
        except BaseException as exc:  # an interrupt too: the reader reopens only to keep the evidence, then it stops
            result, error, raised = None, type(exc).__name__, exc
        rows = self.reopen_and_read()
        gap, next_reader = self.gap_rows(rows)
        cov = self.ga.Coverage()
        ctx = None if trace is None or not trace.evidence.run_id else gate2a_context(
            trace, mode=mode, scenario=scenario, manifest=self.manifest,
            harness_pids={self.harness.admin_pid, self.harness.monitor_pid}, first_run=self.first_run)
        self.first_run = False
        halted = trace is not None and (trace.inconclusive or any(a.outcome == "aborted" for a in trace.actors))
        call_record = self.ga.Call(
            ctx, error, reconciled=trace is not None and trace.evidence.reconciliation != {},
            flag_kinds=() if trace is None else tuple(f.kind for f in trace.evidence.flags), halted=halted,
            trace_initial=None if trace is None else trace.initial_state,
            verified_initial_digest=verified_initial_digest)
        admin = self.ga.audit_run_gap(gap, index=len(self.segments), prev_reader=self.segments[-1]["reader"],
                                      next_reader=next_reader, harness=self.harness, call=call_record, cov=cov)
        if admin is not None:
            self.contracts.append(self.rc.interval_contract(admin, cov))
        outcome = ("interrupted_unknown_effects" if raised is not None and not isinstance(raised, Exception)
                   else "failed" if raised is not None
                   else "flagged" if trace.evidence.flags or not trace.scoreable else "completed")
        self.calls.append({"call": f"gate2a {len(self.calls)}", "outcome": outcome, "error": error,
                           "run_id": None if trace is None else trace.evidence.run_id})
        if raised is not None or outcome != "completed" or cov.fails_closed:
            self.stop_call(cov, raised, "a Gate 2A call", before=before, hardened=hardened)
        after = self.observe_roles(f"after run {trace.evidence.run_id}")
        self.rc.judge(before, after, self.role_conditions(hardened), f"run {trace.evidence.run_id}", cov)
        self.conclude(cov, "a Gate 2A call")
        return result

    def stop_call(self, cov: Any, raised: BaseException | None, what: str, *, before: Any = None,
              hardened: bool = False) -> None:
        """Keep the evidence of a stopped call: one read-only role-catalog observation from the new segment (it may
        show a role an interrupted teardown left behind), the coverage so far, then refuse. Nothing is retried."""
        after = None
        try:
            after = self.observe_roles(f"after a stopped {what}")
        except Exception as exc:  # the observation itself failing stops nothing further; it is only recorded
            cov.add("batch", "role catalog after the stop", self.ga.INDETERMINATE, f"{type(exc).__name__}")
        if before is not None:
            self.rc.judge(before, after, self.role_conditions(hardened), "after the stop", cov)
        self.coverage.extend(cov)
        self.stopped = what
        self.evidence.write("gate2a_coverage.json", self.report())
        if raised is not None and (not isinstance(raised, Exception) or isinstance(raised, DriverRefused)):
            raise raised  # an operator's interrupt stays one; a refusal keeps its own reason
        if raised is not None:
            raise DriverRefused(f"{what} raised {type(raised).__name__}; the batch stops, nothing is retried") \
                from None
        bad = [c for c in cov.claims if c.status in (self.ga.REFUTED, self.ga.INDETERMINATE)]
        raise DriverRefused(f"{what} didn't verify: " + "; ".join(f"{c.claim}: {c.basis}" for c in bad[:3]))

    def conclude(self, cov: Any, what: str) -> None:
        if cov.fails_closed:
            self.stop_call(cov, None, what)
        self.coverage.extend(cov)

    def report(self) -> dict[str, Any]:
        return {"segments": self.segments, "calls": self.calls, "stopped": self.stopped,
                "role_catalog": self.rc.observations_from(self.roles), "b2_interval": self.contracts,
                **self.coverage.report()}

    def finish(self) -> dict[str, Any]:
        """Close the last segment, then check every session's line numbers across the whole batch."""
        self.current.close()
        self.segments.append({"start": self.current.start, "end": self.current.end,
                              "reader": self.current.reader.session})
        rows = self.ga.parse_log(self.effects.log_read("whole"))
        first = self.ga.marker_row(rows, self.segments[0]["start"])
        last = self.ga.marker_row(rows, self.segments[-1]["end"])
        self.ga.line_completeness([r for r in rows if first.start <= r.start <= last.start], self.coverage)
        self.ga.standing_claims(self.coverage)
        report = self.report()
        self.evidence.write("gate2a_coverage.json", report)
        return report


# ------------------------------------------------------------------ the real effects (constructed only by main)


class RealEffects(a1.RealEffects):  # pragma: no cover - needs an approved, live disposable cluster
    """Gate A1's connections, exactly, after Gate A2's preconditions. Opens no Gate 2A object."""

    def __init__(self, manifest: dict[str, Any]) -> None:
        import os

        from dotenv import dotenv_values
        preconditions(manifest, environ=dict(os.environ), dotenv=dotenv_values(ROOT / ".env"),
                      modules=list(sys.modules))
        super().__init__(manifest)


class SegmentEffects(RealEffects):  # pragma: no cover - needs an approved, live disposable cluster
    """Gate A1's connections, plus a reader that can disconnect and reconnect between segments. Gate A1's log queries
    look ``self.log_conn`` up on every call, so a new reader carries the batch's one ``ServerLog`` offset forward."""

    def __init__(self, manifest: dict[str, Any]) -> None:
        from deployspecter.live.serverlog import ServerLog
        super().__init__(manifest)
        self.logs["whole"] = ServerLog(lambda q, prm=None: self.log_conn.execute(q, prm).fetchone())

    def reader_close(self) -> None:
        self.log_conn.close()

    def reader_open(self) -> None:
        conn = self.psycopg.connect(**self.target, autocommit=True, prepare_threshold=a1.PREPARE_THRESHOLD,
                                    application_name="ds_parent_log_reader", options=self.options)
        try:
            a1.require_unprepared(conn)
            info, want = conn.info, self.target
            if (info.host, str(info.port), info.dbname, info.user) != (want["host"], want["port"], want["dbname"],
                                                                       want["user"]):
                raise DriverRefused("the new reader isn't connected to the manifest's fixed local target")
        except BaseException:
            conn.close()
            raise
        self.log_conn = conn


# ------------------------------------------------------------------ command line


def main(argv: list[str], effects_factory: Callable[[dict[str, Any]], Any] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gate_a2_run")
    parser.add_argument("command", choices=("plan", "export", "run"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--approval", type=Path)
    args = parser.parse_args(argv)
    manifest = load_manifest(args.manifest, args.manifest_sha256)
    if args.command == "plan":
        for step in manifest["steps"]:
            print(step)
        print("\nexecution blockers:")
        for b in manifest["execution_blockers"]:
            print(f"  {b['id']}: {b['summary']}")
        return 0
    require_executable(manifest, args.command)
    raw = args.approval.read_bytes() if args.approval is not None else b""
    approval = v2.load_approval(args.approval, manifest_sha256=args.manifest_sha256, phase="gate_a2_export")
    if args.approval.read_bytes() != raw or json.loads(raw) != approval:
        raise DriverRefused("the approval file changed while it was being read")
    v2.verify_sources(manifest)
    evidence = v2.EvidenceDir.create(ROOT / manifest["artifacts_root"])
    a1.record_approval(evidence, args.approval, raw)
    effects = None
    try:
        effects = (effects_factory or RealEffects)(manifest)
        result = export_batch(effects, manifest, evidence)
    except BaseException as exc:  # including an operator's interrupt: always leave a record, never retry
        kind = ("refused_fail_closed" if isinstance(exc, DriverRefused) else
                "interrupted_stop" if isinstance(exc, KeyboardInterrupt) else "unknown_effect_stop")
        evidence.write("failure.json", {"classification": kind, "error": type(exc).__name__,
                                        "detail": str(exc)[:2000], "retried": False,
                                        "cleanup": "no further SQL is issued; the operator must run docker stop"})
        raise
    finally:
        if effects is not None:
            getattr(effects, "close", lambda: None)()
    print(f"export digest for the reviewer to attest: {result['digest']}\nrun directory: {evidence.path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
