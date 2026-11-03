"""Breaks each guard on purpose in a scratch copy, and checks that a test notices.

Usage: uv run python tools/live_stepping_mutants.py SCRATCH_DIR [OUTPUT.txt]

Only an actual test failure counts as caught. A setup error is HARNESS_ERROR, and an unmodified control runs first and
last, because otherwise every mutant could look caught for the wrong reason. A break may leave the disposable cluster
dirty (a role or prepared transaction it failed to clean up), so it's cleaned between runs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
TIME_LIMIT_S = 240
STEPPING = "tests/test_live_stepping.py"
SUPERVISED = "tests/test_live_supervised.py"

CONTROL = ("CONTROL: no change", "runner.py", "import queue", "import queue", [STEPPING, SUPERVISED], "")

# name, file, text to replace, replacement, test file, -k filter
MUTANTS = [
    # --- the scheduling engine, carried over from earlier work
    ("blocked statements never count as settled (false stall)", "runner.py",
     "if all(graph.get(self.trace.connections[a.spec.name]) for a in in_db) and self.events.empty():", "if False:",
     [STEPPING], "row_lock_variant"),
    ("traffic guard removed", "driver.py",
     "if channel is not None and not (channel.on_actor_thread() and (channel.granted or channel.aborting)):",
     "if False:", [STEPPING], "TestRefused"),
    ("actors silently serialized: last actor always continues", "runner.py",
     "chosen, requested, source = self.schedule.choose(enabled, finished, self.last)",
     "chosen, requested, source = self.schedule.choose(enabled, finished, self.last); "
     "chosen = self.last if self.last in enabled else chosen", [STEPPING], "overlapping_reads"),
    ("gate ignored: operations run without waiting", "driver.py", "if not self._grant.wait(self.hard_limit_s):",
     "if False:", [STEPPING], "serial_order_takes"),
    ("recording consumes the application's result", "driver.py", 'cursor.scroll(0, "absolute")', "pass",
     [STEPPING], "serial_order_takes"),
    ("failed-transaction COMMIT recorded as committed", "runner.py",
     'and done.txn_before == "INTRANS" and not lost', "and not lost", [STEPPING], "commit_of_a_failed"),
    ("savepoint rollback not tracked", "runner.py", 'pending.effect = "savepoint_rolled_back"', "pass",
     [STEPPING], "nested_transaction"),
    ("multi-statement SQL accepted", "driver.py", "if count > 1:", "if False:", [STEPPING], "TestRefused"),
    ("role change accepted by the driver", "driver.py", 'if "ROLE" in words or "SESSION AUTHORIZATION" in joined:',
     "if False:", [STEPPING], "TestRefused"),
    ("swallowed abort can commit", "driver.py", "self.force_rollback = True", "pass",
     [STEPPING], "abort_swallowed"),
    ("open transaction at return not closed in a step", "runner.py",
     "if conn.closed or channel.aborting or status in _NOTHING_TO_ROLL_BACK:", "if True:",
     [STEPPING], "statement_timeout_is_classified"),
    ("woken write numbered before the commit that woke it", "runner.py",
     "sorted(self.epoch_commits, key=lambda c: (c[0].admitted_decision != current, c[0].seq))",
     "sorted(self.epoch_commits, key=lambda c: c[0].seq)", [STEPPING], "commit_is_numbered"),
    ("explicit pipeline blocks accepted", "driver.py",
     'self._refuse_on_actor("pipeline", "pipeline mode batches statements below the scheduling boundary")', "pass",
     [STEPPING], "pipeline_is_refused_by_name"),
    # --- 1. restricted login
    ("child inherits the supervisor's environment", "supervisor.py",
     "env = {k: os.environ[k] for k in _ENV_KEPT if k in os.environ}", "env = dict(os.environ)",
     [SUPERVISED], "child_holds_no_credentials"),
    ("session privileges not verified", "supervisor.py", "            if wrong:\n", "            if False:\n",
     [SUPERVISED], "wider_privilege"),
    ("database grants not hardened", "supervisor.py",
     'self._admin(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))', "pass",
     [SUPERVISED], "temporary_tables"),
    ("connection limit not set", "supervisor.py", "len(actors) + 1 + self.hooks.extra_slots", "99",
     [SUPERVISED], "connection_limit_refuses"),
    ("run role not dropped", "supervisor.py",
     'self._admin(sql.SQL("DROP ROLE {}").format(role))', "pass", [SUPERVISED], "role_is_dropped"),
    ("entrypoint resolved only inside the actor", "worker.py", 'entrypoint=_resolve(a["entrypoint"])',
     'entrypoint=a["entrypoint"]', [SUPERVISED], "will_not_import"),
    ("a run on an unprepared cluster starts anyway", "supervisor.py", "            if problems:  # no application",
     "            if False:  # no application", [SUPERVISED], "TestUnpreparedCluster"),
    ("scoreable ignores evidence", "trace.py", "evidence.supervised and not evidence.flags and not self.inconclusive",
     "True", [SUPERVISED], "test_never_scored or engine_only"),
    ("stale run roles not swept", "supervisor.py", "        stale = self._sweep_stale_roles()\n",
     "        stale = []\n", [SUPERVISED], "role_left_by_a_crash"),
    ("child not confined", "supervisor.py",
     "if not (self.hooks.confine and sys.platform == \"darwin\" and os.path.exists(SANDBOX_EXEC)):",
     "if True:", [SUPERVISED], "confined_away"),
    ("repository root on the child's import path", "supervisor.py",
     "if path.exists() and (not inside_home or any(a == path or a in path.parents for a in allowed)):",
     "if path.exists():", [SUPERVISED], "import_path_holds_no_private or stays_off_the_childs"),
    ("role verifier written to the log", "supervisor.py",
     "self._admin(\"SET log_statement = 'none'\")  # keeps", "self._admin(\"SELECT 1\")  # keeps",
     [SUPERVISED], "verifier_does_not_reach"),
    ("incomplete privilege checks accepted", "supervisor.py", "        if set(checks) != expected_sessions:\n",
     "        if False:\n", [SUPERVISED], "missing_privilege_check"),
    ("registered sessions not checked against the role", "supervisor.py",
     "        if wrong or len(set(pids)) != len(pids):\n", "        if False:\n",
     [SUPERVISED], "belong_to_the_run_role"),
    ("handled harness timeouts can be scored", "runner.py", "        if record.error_category in _HARNESS_LIMITS:\n",
     "        if record.error_category == \"connection_lost\":\n", [SUPERVISED], "handled_timeout_cannot"),
    ("parameter coverage not reported", "serverlog.py", "            _parameter_coverage(matched, report)\n",
     "            pass\n", [SUPERVISED], "parameter_coverage_is_reported"),
    # --- 2. unmanaged sessions
    ("live activity poll removed", "supervisor.py",
     "self._check_activity(ctx, harness, ready, seen_unknown, flag)", "pass",
     [SUPERVISED], "live_activity_poll"),
    ("session counter ignored", "supervisor.py", "if evidence.session_counter_delta > len(actors) + 1:", "if False:",
     [SUPERVISED], "session_counter"),
    ("forged result accepted", "supervisor.py",
     "if named != registered or {a.actor for a in child.actors} != set(ctx.actor_pids):", "if False:",
     [SUPERVISED], "forged_result"),
    ("prepared transactions left to commit later", "supervisor.py",
     'self._admin(sql.SQL("ROLLBACK PREPARED {}").format(sql.Literal(gid)))', "pass",
     [SUPERVISED], "prepared_transaction_cannot"),
    # --- 3. log reconciliation
    ("unknown sessions not counted", "serverlog.py", "report.unknown_sessions.append(", "(lambda *_: None)(",
     [SUPERVISED], "TestReconcilerUnit or stolen or connection_limit"),
    ("unexpected statements not counted", "serverlog.py",
     'report.unexpected_statements.append(f"{expected_pids[pid]}: {text[:80]}")', "pass",
     [SUPERVISED], "repeated_identical"),
    ("logged parameters not compared", "serverlog.py", 'if wanted is None or not detail.startswith("Parameters:"):',
     "if True:", [SUPERVISED], "parameter_mismatch or different_values"),
    ("cross-session order not checked", "serverlog.py", "if matched.seq < last_seq:", "if False:",
     [SUPERVISED], "cross_session_order"),
    ("missing statements not counted", "serverlog.py",
     "if item.required and consumed.get((pid, index), 0) < item.repeat:", "if False:", [SUPERVISED],
     "claimed_statement or logging_switched"),
    ("session start and end not accounted", "serverlog.py",
     "if sum(m.startswith(prefix) for m in messages) != 1:", "if False:",
     [SUPERVISED], "session_end_must or session_seen_twice"),
    ("monitor session may run anything", "serverlog.py", "if text in MONITOR_ALLOWED:", "if True:",
     [SUPERVISED], "monitor_may_only"),
    ("bad column count tolerated", "serverlog.py", "if len(row) != CSV_COLUMNS:", "if False:",
     [SUPERVISED], "wrong_column_count or unparsable_line"),
    ("truncated log tolerated", "serverlog.py", "if stat[0] < self.offset:", "if False:",
     [SUPERVISED], "truncated_log"),
    ("deleted log tolerated", "serverlog.py", "if not started and data:", "if False:",
     [SUPERVISED], "start_marker_missing"),
    ("per-database logging overrides not checked", "supervisor.py", "        if overrides:\n",
     "        if False:\n", [SUPERVISED], "logging_off_before"),
    ("cancel-like connections always accepted", "serverlog.py", "if not (probe and halted):", "if not probe:",
     [SUPERVISED], "cancel_connection_is_accepted"),
    # --- 4. external lock holders
    ("outside lock holders ignored", "runner.py", "if not strangers:", "if True:",
     [SUPERVISED], "TestExternalLockHolders"),
    # --- 5. surviving threads and processes
    ("abort step does nothing", "supervisor.py", 'self._write_child(proc, "abort")', "pass",
     [SUPERVISED], "child_abort_request"),
    ("cancel step does nothing", "supervisor.py", 'function = "pg_cancel_backend" if step == "cancel" else',
     'function = "pg_backend_pid" if step == "cancel" else', [SUPERVISED], "each_step"),
    ("terminate step does nothing", "supervisor.py",
     'else "pg_terminate_backend"\n', 'else "pg_cancel_backend"\n', [SUPERVISED], "each_step or really_lost"),
    ("kill step does nothing", "supervisor.py", "os.killpg(proc.pid, signal.SIGKILL)", "pass",
     [SUPERVISED], "gil or left_in_the_process_group"),
    ("teardown leaves sessions running", "supervisor.py", "for row in rows:\n                self._one(",
     "for row in []:\n                self._one(", [SUPERVISED], "open_session_cannot"),
    ("child survives a lost database", "supervisor.py", "            failure = exc  # the child still has to die",
     "            raise  # the child still has to die", [SUPERVISED], "killed_when_the_database"),
    ("surviving processes not scanned for", "supervisor.py", "        self._kill_survivors(ctx, flag)\n", "",
     [SUPERVISED], "surviving_detached"),
    ("the child's leaked threads aren't flagged", "supervisor.py", "        if child.leaked_threads:\n",
     "        if False:\n", [SUPERVISED], "never_reaches_a_gate"),
    # --- 6. lost cancels and disconnections
    ("lost COMMIT treated as known", "runner.py", 'ambiguous = lost and (record.kind == "commit" or autocommit_write)',
     "ambiguous = False", [SUPERVISED], "TestLostAcknowledgment or during_commit"),
    ("application can hide a connection loss", "runner.py",
     "        if record.error_category in _HARNESS_LIMITS:\n", "        if False:\n",
     [SUPERVISED], "catching_the_loss"),
    # --- 7. bounded reset and teardown
    ("admin calls have no limit", "supervisor.py", "return future.result(timeout=seconds or self.supervisor.db_call_s)",
     "return future.result()", [SUPERVISED], "bounded_call_raises"),
    ("reset has no lock limit", "supervisor.py", 'self._admin(f"SET lock_timeout = {int(s.reset_lock_ms)}")', "pass",
     [SUPERVISED], "outlasts"),
    ("outside sessions not ended before reset", "supervisor.py",
     "        stuck = self._end_outside_sessions(s.quiescence_s)\n"
     "        if stuck:\n            self.poisoned = f\"sessions would not end",
     "        stuck = []\n        if stuck:\n            self.poisoned = f\"sessions would not end",
     [SUPERVISED], "leftover_session"),
    ("poisoned runner keeps going", "supervisor.py", "        if self.poisoned:\n            raise RuntimeError",
     "        if False:\n            raise RuntimeError", [SUPERVISED], "will_not_end_poison or outlasts"),
    # --- 8. opaque executemany
    ("batches run as repeated execute", "driver.py",
     "lambda: super(SteppedCursor, self).executemany(query, counter, returning=returning),",
     "lambda: [psycopg.Cursor.execute(self, query, p) for p in counter],",
     [SUPERVISED], "test_cell_against_plain or server_error_under_autocommit"),
    ("client-side batch error not treated as unknown", "runner.py",
     'record.kind == "opaque_batch" and record.status == "error" and autocommit_write and not record.sqlstate',
     "False", [SUPERVISED], "client_error_under_autocommit"),
    ("batch parameters consumed up front", "driver.py", "self._source = iter(source)",
     "self._source = iter(list(source))", [SUPERVISED], "iterator_stays_lazy"),
    ("transaction control accepted as a batch", "driver.py",
     'if classified.refused or classified.kind != "sql":', "if False:", [SUPERVISED], "refused_forms"),
    ("exploration claims coverage inside a batch", "explore.py",
     "result.complete_schedule_coverage = result.exhausted and result.opaque_runs == 0",
     "result.complete_schedule_coverage = result.exhausted", [SUPERVISED], "does_not_claim_coverage"),
    ("batch not counted in the reconciler", "serverlog.py", "repeat=op.batch_count or 0))", "repeat=0))",
     [SUPERVISED], "success_is_one_step"),
]


def database_url() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1]
    raise SystemExit("DATABASE_URL is not in .env")


def clean_cluster() -> None:
    dsn = database_url().rsplit("/", 1)[0] + "/deployspecter_test"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("ALTER DATABASE deployspecter_test RESET log_statement")
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()"
        )
        for (gid,) in conn.execute("SELECT gid FROM pg_prepared_xacts").fetchall():
            conn.execute(f"ROLLBACK PREPARED '{gid}'")
        for (name,) in conn.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE 'ds_run_%'").fetchall():
            conn.execute(f'DROP OWNED BY "{name}"')
            conn.execute(f'DROP ROLE "{name}"')


def run_tests(copy: Path, files: list[str], filter_: str) -> tuple[str, str]:
    env = dict(os.environ, PYTHONPATH=str(copy), PYTHONDONTWRITEBYTECODE="1", DATABASE_URL=database_url())
    command = ["uv", "run", "--no-sync", "pytest", *files, "-q", "-x", "--no-header", "-p", "no:cacheprovider"]
    if filter_:
        command += ["-k", filter_]
    try:
        done = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=TIME_LIMIT_S)
    except subprocess.TimeoutExpired:
        return "HUNG", f"no result within {TIME_LIMIT_S}s"
    lines = done.stdout.splitlines()
    failed = [line for line in lines if line.startswith("FAILED")]
    errors = [line for line in lines if line.startswith("ERROR")]
    detail = (failed or errors or lines[-1:] or [""])[0].replace("tests/", "")[:110]
    if failed:
        return "KILLED", detail
    return ("HARNESS_ERROR" if errors or done.returncode else "SURVIVED"), detail


def evaluate(copy: Path, mutant: tuple) -> tuple[str, str]:
    _, filename, before, after, files, filter_ = mutant
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(ROOT / "src" / "deployspecter", copy / "deployspecter",
                    ignore=shutil.ignore_patterns("__pycache__"))
    path = copy / "deployspecter" / "live" / filename
    text = path.read_text()
    if text.count(before) != 1:
        raise SystemExit(f"mutation site not unique or missing: {mutant[0]}")
    path.write_text(text.replace(before, after))
    clean_cluster()
    return run_tests(copy, files, filter_)


def main() -> int:
    copy = Path(sys.argv[1]) / "mutant"
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    lines: list[str] = []

    def note(line: str) -> None:
        print(line, flush=True)
        lines.append(line)

    for mutant in MUTANTS:  # fail early on a stale mutation site, before spending any time
        if (ROOT / "src/deployspecter/live" / mutant[1]).read_text().count(mutant[2]) != 1:
            raise SystemExit(f"mutation site not unique or missing: {mutant[0]}")
    first, detail = evaluate(copy, CONTROL)
    note(f"{first} | {CONTROL[0]} (first) | {detail}")
    results = []
    for mutant in MUTANTS:
        result, detail = evaluate(copy, mutant)
        results.append(result)
        note(f"{result} | {mutant[0]} | {detail}")
    last, detail = evaluate(copy, CONTROL)
    note(f"{last} | {CONTROL[0]} (last) | {detail}")
    shutil.rmtree(copy, ignore_errors=True)
    clean_cluster()
    caught = results.count("KILLED")
    note(f"controls passed: {first == last == 'SURVIVED'}; caught {caught} of {len(MUTANTS)}")
    if output:
        output.write_text("\n".join(lines) + "\n")
    return 0 if first == last == "SURVIVED" and caught == len(MUTANTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
