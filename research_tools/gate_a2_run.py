"""Gate A2 driver with the Gate A2.1 controls (manifest version 3). Research-only. NOT EXECUTABLE.

    plan     print the steps and the execution blockers; touches nothing
    export   the reconciled export batch; refused while the manifest lists execution blockers
    run      refused: the Gate 2A section waits for the reviewed whole-batch audit

What it takes from Gate A1, imported unchanged, not copied: the reconciled parent operation (``Batch``), the one log
reader, the whole-batch session audit, the fixed-target unprepared connections (``RealEffects``), and the preflight,
reset, export, cross-check, typed-state and cleanup statement sets that Gate A1 version 4 reconciled on PostgreSQL 16.

What it adds (``gate_a2_controls``): refusal of model credentials, model modules and every libpq environment variable;
one reviewed credential source; the protected development database as a forbidden target; the server identity bound
across batches; a refused partial reset; per-attempt state records for Gate 2A runs.

Nothing the command line reaches constructs Gate 2A's supervisor, sandbox or child. Its per-run reset, initial read
and role setup run on its own unreconciled admin session, and its supervisor terminates every client session it doesn't
own, a Gate A2 log reader included (see GATE_A2_1_TRUST_BOUNDARY.md). The parent is a superuser on a disposable
cluster: reconciliation detects deviation, it doesn't prevent it.
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
VERSION = 3
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
        audit = ch.audit_batch(rows, reconciled_tags=self.batch.tags, user=self.user, databases={self.db},
                               reader=self.batch.established_reader(raw, self.start, self.end, self.db))
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


# ------------------------------------------------------------------ the real effects (constructed only by main)


class RealEffects(a1.RealEffects):  # pragma: no cover - needs an approved, live disposable cluster
    """Gate A1's connections, exactly, after Gate A2's preconditions. Opens no Gate 2A object."""

    def __init__(self, manifest: dict[str, Any]) -> None:
        import os

        from dotenv import dotenv_values
        preconditions(manifest, environ=dict(os.environ), dotenv=dotenv_values(ROOT / ".env"),
                      modules=list(sys.modules))
        super().__init__(manifest)


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
