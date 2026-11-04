"""Gate A1: minimal PostgreSQL characterization driver. Research-only. Runs only with a signed owner approval.

    plan          print the package's steps; touches nothing
    characterize  run Gate A1 once; needs the Gate A1 manifest digest and an approval file naming it

Separate from the buggy/fixed execution and replay (``track_a_run.py``, Gate A2). It constructs no Gate 2A
supervisor and no release ``Sandbox``, runs no child, no application code and no command. Every statement runs on
a tagged parent session reconciled from the server log, and a final audit attributes every session in the batch.

The parent is a superuser on a disposable cluster. Reconciliation detects deviation; it doesn't prevent it.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import secrets
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import track_a_run as a2  # noqa: E402  (reuses only its manifest, approval and evidence helpers)

ROOT = a2.ROOT
PACKAGE = "gate-a1-characterization"
VERSION = 2
POLL_INTERVAL_S = 0.05  # wait_gone's poll interval; with backend_exit_wait_s it bounds the polls per window
DriverRefused = a2.DriverRefused


class Effects(Protocol):
    def parent_connection(self, tag: str, database: str) -> Any: ...
    def log_position(self, key: str = "op") -> None: ...
    def log_mark(self, token: str) -> None: ...
    def log_read(self, key: str = "op") -> bytes: ...
    def wait_gone(self, tag: str) -> bool: ...


def load_package(path: Path, expected_sha256: str) -> dict[str, Any]:
    if a2.sha256_file(path) != expected_sha256:
        raise DriverRefused("the Gate A1 manifest's digest isn't the one given")
    package = json.loads(path.read_text())
    if package.get("package") != PACKAGE or package.get("version") != VERSION:
        raise DriverRefused(f"not a version {VERSION} Gate A1 manifest")
    if package.get("scope") != "characterization_only" or package.get("never_heldout_evidence") is not True:
        raise DriverRefused("Gate A1 is characterization only")
    return package


def reader_poll_limit(package: dict[str, Any]) -> int:
    """At most this many end-of-session polls can fall inside one operation window."""
    return int(float(package["safety_ceilings"]["backend_exit_wait_s"]) / POLL_INTERVAL_S) + 2


def _modules():
    sys.path.insert(0, str(ROOT / "tests"))
    from deployspecter.research.track_a import catalog_export as ce
    from deployspecter.research.track_a import characterization as ch
    from deployspecter.research.track_a import evidence_binding as eb
    from deployspecter.research.track_a import parent_reconciliation as pr
    from deployspecter.research.track_a import pipeline as p
    from deployspecter.research.track_a import scenario as sc
    return ce, ch, eb, pr, p, sc


class Batch:
    """Runs reconciled parent operations, keeps their records, and bounds the batch in time."""

    def __init__(self, effects: Effects, package: dict[str, Any]) -> None:
        self.effects, self.package = effects, package
        self.records: list[dict[str, Any]] = []
        self.tags: set[str] = set()
        self.reader: Any = None  # the one log-reader session, established from the first operation's markers
        self.end = time.monotonic() + float(package["safety_ceilings"]["batch_wall_clock_s"])

    def op(self, name: str, database: str, statements: list[tuple[str, tuple]], action: Callable[[Any], Any]) -> Any:
        _ce, _ch, _eb, pr, _p, _sc = _modules()
        if time.monotonic() >= self.end:
            raise DriverRefused(f"batch wall-clock safety ceiling reached before {name}")
        nonce = secrets.token_hex(6)
        tag, start, end = f"ds_parent_{name}_{nonce}", f"ds_parent_mark_start_{nonce}", f"ds_parent_mark_end_{nonce}"
        self.effects.log_position()
        self.effects.log_mark(start)
        with self.effects.parent_connection(tag, database) as conn:
            result = action(conn)
        if not self.effects.wait_gone(tag):  # its disconnection must be logged before the end marker
            raise DriverRefused(f"parent session {name} didn't end")
        self.effects.log_mark(end)
        raw, rows = self.read_window(start, end)
        reader = self.established_reader(raw, start, end, database)
        expected = tuple(pr.ExpectedStatement(t, p) for t, p in statements)
        rec = pr.reconcile_parent_session(rows, tag=tag, expected=expected, user=self.package["cluster"]["admin_user"],
                                          database=database,
                                          authentication=self.package["cluster"]["authentication_method"],
                                          reader=reader, reader_poll_limit=reader_poll_limit(self.package))
        record = {"op": name, "tag": tag, "ok": rec.ok, "problems": rec.problems, "statements": rec.statements_seen}
        self.records.append(record)
        self.tags.add(tag)
        if not rec.ok:
            raise DriverRefused(f"parent session {name} didn't reconcile: {rec.problems}")
        return result

    def established_reader(self, raw: bytes, start: str, end: str, database: str) -> Any:
        """The session that wrote these markers. It must be the same log reader for the whole batch."""
        _ce, _ch, _eb, pr, _p, _sc = _modules()
        try:
            reader = pr.reader_session(*pr.marker_rows(raw, start, end), user=self.package["cluster"]["admin_user"],
                                       database=database)
        except pr.ParentLogError as exc:
            raise DriverRefused(f"the log reader can't be identified from the markers: {exc}") from None
        if self.reader is None:
            self.reader = reader
        elif (reader.session, reader.pid) != (self.reader.session, self.reader.pid):
            raise DriverRefused("the markers were written by a different log-reader session")
        return reader

    def read_window(self, start: str, end: str, key: str = "op") -> tuple[bytes, list[list[str]]]:
        """Wait, boundedly, for the logging collector to write the end marker. Any other log problem fails at once."""
        _ce, _ch, _eb, pr, _p, _sc = _modules()
        until = time.monotonic() + float(self.package["safety_ceilings"]["log_wait_s"])
        while True:
            raw = self.effects.log_read(key)
            try:
                return raw, pr.window(raw, start, end)
            except pr.EndMarkerMissing:
                if time.monotonic() >= until:
                    raise
                time.sleep(0.05)


def _execute(statements: list[tuple[str, tuple]]) -> Callable[[Any], list[list[tuple]]]:
    def action(conn: Any) -> list[list[tuple]]:
        out = []
        for text, params in statements:
            cursor = conn.execute(text, params) if params else conn.execute(text)
            out.append(list(cursor.fetchall()) if text.lstrip().upper().startswith("SELECT") else [])
        return out
    return action


def characterize(effects: Effects, package: dict[str, Any], evidence: a2.EvidenceDir) -> dict[str, Any]:
    ce, ch, eb, pr, p, sc = _modules()
    from live_app import reservations as app

    if hashlib.sha256(app.SETUP_SQL.encode()).hexdigest() != package["schema"]["setup_sql_sha256"]:
        raise DriverRefused("the setup SQL isn't the reviewed one")
    db, user = package["cluster"]["database"], package["cluster"]["admin_user"]
    batch = Batch(effects, package)
    nonce = secrets.token_hex(6)
    batch_start, batch_end = f"ds_parent_mark_batch_start_{nonce}", f"ds_parent_mark_batch_end_{nonce}"
    effects.log_position("batch")
    effects.log_mark(batch_start)

    pre = list(ch.PREFLIGHT)
    preflight = batch.op("op0p", db, [(t, ()) for t in pre], _execute([(t, ()) for t in pre]))
    problems = ch.judge_preflight(preflight, database=db, user=user)
    evidence.write("preflight.json", {"rows": preflight, "problems": problems})
    if problems:
        raise DriverRefused(f"preflight: {problems}")

    empty = ch.reset_statements(app.SETUP_SQL, [])
    batch.op("op0r", db, empty, _execute(empty))
    export_sql = [(t, ()) for _, t in ce.EXPORT_QUERIES]
    first = batch.op("op2a", db, export_sql, ce.export_snapshot)
    second = batch.op("op2b", db, export_sql, ce.export_snapshot)
    cross = batch.op("op2c", db, [(t, ()) for _, t in ce.CROSS_CHECK_QUERIES],
                     lambda conn: ce.cross_check_read(conn, first.snapshot))
    registry = p.build_registry()
    context = sc.context_config(registry_digest=registry.digest, schema_digest=first.digest)
    unpinned = ce.cv.TrustedSnapshot(exporter=ce.EXPORTER, source_label=ce.SOURCE_LABEL, exported_on="2000-01-01",
                                     pinned_digest=first.digest)
    verdict = ce.cv.verify_context(context, registry=registry, context_trust=ce.sp.TrustedPlanning(
        reviewer="gate_a1_characterization", pinned_digest=ce.sp.context_digest(
            ce.sp.SchemaContextConfig.model_validate(context))), snapshot=first.snapshot, snapshot_trust=unpinned)
    catalog = {"export_1": first.digest, "export_2": second.digest, "structural": ce.structural_digest(first.snapshot),
               "exports_agree": first.digest == second.digest, "cross_check": cross.problems,
               "cross_check_agrees": cross.agrees, "verify_context": verdict.outcome,
               "findings": [f.code for f in verdict.findings],
               "time_typed_columns": eb.check_no_time_types(first.snapshot),
               "identity": first.identity, "pinned": False}
    evidence.write("catalog.json", {**catalog, "snapshot": first.snapshot})

    chain = p.run_offline_chain(first.snapshot) if verdict.outcome == "MATCH" else None
    states = []
    if chain is not None:
        expected = p.expected_initial_state(chain)
        types = {tuple(k.split(".")): v for k, v in package["state_checks"]["column_types"].items()}
        seeded = ch.reset_statements(app.SETUP_SQL, list(chain.seed))
        tables = sorted(expected)
        for _ in range(package["state_checks"]["repeats"]):
            batch.op("op0r", db, seeded, _execute(seeded))
            check = batch.op("op6i", db, list(eb.independent_statements(expected, types)),
                             lambda conn: eb.independent_state_check(conn, expected, types))
            read = batch.op("op6f", db, [(t, ()) for t in eb.final_read_statements(tables)],
                            lambda conn: eb.independent_final_read(conn, tables))
            states.append({"independent_problems": eb.judge_independent(check, expected), "digests": check.digests,
                           "read_problems": p.check_initial_state(expected, read),
                           "read_digest": eb.state_digest(read)})
    evidence.write("states.json", states)

    bindings = ch.binding_statements()
    results = batch.op("op7b", db, bindings, _execute(bindings))
    plain, jsonb = results[-3], [r[0] for r in results[-2]]
    judged = ch.judge_bindings(plain, jsonb)
    evidence.write("bindings.json", {"plain_failures": judged.plain_failures, "d1_refuted": judged.d1_refuted,
                                     "jsonb_findings": judged.jsonb_findings})

    cleanup = ch.cleanup_statements()
    batch.op("op9c", db, cleanup, _execute(cleanup))
    effects.log_mark(batch_end)
    raw, rows = batch.read_window(batch_start, batch_end, "batch")
    audit = ch.audit_batch(rows, reconciled_tags=batch.tags, user=user, databases={db},
                           reader=batch.established_reader(raw, batch_start, batch_end, db))
    evidence.write("parent_reconciliation.json", batch.records)
    evidence.write("batch_audit.json", {"ok": audit.ok, "problems": audit.problems, "sessions": audit.sessions})

    findings = {
        "catalog_match": catalog["verify_context"] == "MATCH" and catalog["exports_agree"] and cross.agrees,
        "state_reads_exact": bool(states) and all(not s["independent_problems"] and not s["read_problems"]
                                                  for s in states)
                             and len({s["read_digest"] for s in states}) == 1,
        "bindings_hold": judged.assumptions_hold,
        "every_session_attributed": audit.ok,
        "every_parent_session_reconciled": all(r["ok"] for r in batch.records),
    }
    outcome = "CHARACTERIZED" if all(findings.values()) else "CHARACTERIZED_WITH_FINDINGS"
    summary = {"outcome": outcome, "findings": findings, "evidence_kind": "synthetic characterization; not an "
               "evaluation, not a detection, not held-out evidence", "retried": False}
    evidence.write("summary.json", summary)
    return summary


class RealEffects:  # pragma: no cover - never constructed in tests; needs an approved, live disposable cluster
    def __init__(self, package: dict[str, Any]) -> None:
        import psycopg
        from psycopg.conninfo import conninfo_to_dict

        from deployspecter.config import load_settings
        from deployspecter.live.serverlog import ServerLog
        from deployspecter.research.track_a import characterization as ch

        self.psycopg, self.settings, self.package = psycopg, load_settings(), package
        if self.settings.model_api_key:
            raise DriverRefused("MODEL_API_KEY is set (possibly from .env); Gate A1 refuses to run with it")
        try:  # libpq's parse errors can quote part of the URL, the password included, so none is kept
            parsed = conninfo_to_dict(self.settings.test_dsn)
        except Exception:
            raise DriverRefused("DATABASE_URL couldn't be parsed as a connection string") from None
        try:  # checked before any connection, so a wrong DATABASE_URL never receives the password
            self.target = ch.connection_target(parsed, package)
        except ValueError as exc:
            raise DriverRefused(str(exc)) from None
        timeouts = package["safety_ceilings"]["session_options"]
        self.options = " ".join(f"-c {k}={v}" for k, v in timeouts.items())
        self.log_conn = psycopg.connect(**self.target, autocommit=True,
                                        application_name="ds_parent_log_reader", options=self.options)
        info, want = self.log_conn.info, self.target
        if (info.host, str(info.port), info.dbname, info.user) != (want["host"], want["port"], want["dbname"],
                                                                   want["user"]):
            self.log_conn.close()
            raise DriverRefused("the connection isn't to the manifest's fixed local target")
        query = lambda q, prm=None: self.log_conn.execute(q, prm).fetchone()  # noqa: E731
        self.logs = {"op": ServerLog(query), "batch": ServerLog(query)}

    @contextlib.contextmanager
    def parent_connection(self, tag, database):
        if database != "deployspecter_test":
            raise DriverRefused("Gate A1 connects only to the test database")
        with self.psycopg.connect(**self.target, autocommit=True, application_name=tag,
                                  options=self.options) as conn:
            yield conn

    def log_position(self, key="op"):
        self.logs[key].position()

    def log_mark(self, token):
        from deployspecter.research.track_a import parent_reconciliation as pr
        self.log_conn.execute(pr.marker_sql(token))

    def log_read(self, key="op"):
        return self.logs[key].read()

    def wait_gone(self, tag):
        from deployspecter.research.track_a import characterization as ch
        until = time.monotonic() + float(self.package["safety_ceilings"]["backend_exit_wait_s"])
        while time.monotonic() < until:
            if self.log_conn.execute(ch.GONE_SQL, (tag,)).fetchone()[0] == 0:
                return True
            time.sleep(POLL_INTERVAL_S)
        return False

    def close(self):
        self.log_conn.close()


def record_approval(evidence: Any, path: Path, raw: bytes) -> None:
    """Keep the approval byte for byte, so its signature can still be checked against the evidence copy."""
    try:
        with open(evidence.path / "approval.json", "xb") as out:  # exclusive create: evidence is never overwritten
            out.write(raw)
    except FileExistsError:
        raise DriverRefused("approval.json already exists; evidence is never overwritten") from None
    signature = Path(f"{path}.sig")
    evidence.write("approval_provenance.json", {
        "source_path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
        "copied_byte_for_byte": True,
        "signature_file": ({"path": str(signature.resolve()), "sha256": hashlib.sha256(signature.read_bytes())
                            .hexdigest()} if signature.is_file() else None),
        "signature_checked_by_driver": False,
        "note": "the driver checks the manifest digest and phase only; the owner verifies the signature"})


def main(argv: list[str], effects_factory: Callable[[dict[str, Any]], Effects] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gate_a1_characterize")
    parser.add_argument("command", choices=("plan", "characterize"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--approval", type=Path)
    args = parser.parse_args(argv)
    package = load_package(args.manifest, args.manifest_sha256)
    if args.command == "plan":
        _ce, ch, *_ = _modules()
        for step in package["steps"]:
            print(step)
        print("\noperator commands (run by the operator, never by this driver):")
        for command in ch.operator_commands(package):
            print(f"  {command}")
        return 0
    raw_approval = args.approval.read_bytes() if args.approval is not None else b""
    approval = a2.load_approval(args.approval, manifest_sha256=args.manifest_sha256, phase="gate_a1")
    if args.approval.read_bytes() != raw_approval or json.loads(raw_approval) != approval:
        raise DriverRefused("the approval file changed while it was being read")
    a2.verify_sources(package)
    evidence = a2.EvidenceDir.create(ROOT / package["artifacts_root"])
    record_approval(evidence, args.approval, raw_approval)
    effects = None
    try:
        effects = (effects_factory or RealEffects)(package)
        summary = characterize(effects, package, evidence)
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
    print(f"Gate A1: {summary['outcome']}")
    return 0 if summary["outcome"] == "CHARACTERIZED" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
