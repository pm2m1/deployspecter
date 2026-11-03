"""Measurements behind GATE2_AUDIT.md. Synthetic data, disposable database, no model requests, no LangGraph code.

Usage: uv run python tools/live_stepping_gate2_audit.py OUTPUT.json --container NAME

The container must run PostgreSQL with: -c log_connections=on -c log_statement=all -c log_line_prefix='%a|%p|'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import psycopg

from deployspecter.config import load_settings
from deployspecter.database import Sandbox
from deployspecter.live.driver import SteppedCursor, _query_text
from deployspecter.live.explore import LiveBudget, explore_live
from deployspecter.live.runner import ExplicitSchedule, LiveActor, LiveLimits, LiveRunner, LiveScenario, open_session
from deployspecter.models import ColumnValue, SeedRow

SETUP = """
CREATE TABLE t (id integer PRIMARY KEY, v text NOT NULL CHECK (v <> 'bad'));
INSERT INTO t VALUES (2, 'seed');
"""
INSERT = "INSERT INTO t (id, v) VALUES (%s, %s)"
CASES = {
    "all_succeed": (INSERT, [(10, "a"), (11, "b"), (12, "c")], False),
    "conflict_in_middle": (INSERT, [(10, "a"), (2, "dup"), (12, "c")], False),
    "check_fails_last": (INSERT, [(10, "a"), (11, "b"), (12, "bad")], False),
    "unadaptable_parameter_in_middle": (INSERT, [(10, "a"), (object(), "x"), (12, "c")], False),
    "wrong_arity_in_middle": (INSERT, [(10, "a"), (11,), (12, "c")], False),
    "on_conflict_do_nothing": (f"{INSERT} ON CONFLICT DO NOTHING", [(10, "a"), (2, "dup"), (12, "c")], False),
    "returning_collected": (f"{INSERT} RETURNING id", [(10, "a"), (11, "b")], True),
    "returning_not_collected": (f"{INSERT} RETURNING id", [(10, "a"), (11, "b")], False),
    "update_many_rows": ("UPDATE t SET v = %s WHERE id >= %s", [("x", 0), ("y", 2)], False),
    "empty_sequence": (INSERT, [], False),
    "generator_raises_after_first": (INSERT, "generator", False),
}
MODES = ["autocommit_bare", "autocommit_in_transaction", "not_autocommit_commit_anyway", "autocommit_in_pipeline_block"]


def params_of(case: str):
    query, params, returning = CASES[case]
    if params != "generator":
        return query, params, returning

    def generate():
        yield (10, "a")
        raise ValueError("application iterator failed")

    return query, generate(), returning


def as_batch(cur, query, params, returning):
    cur.executemany(query, params, returning=returning)
    results = []
    if returning:
        while True:
            results.append(cur.fetchall())
            if not cur.nextset():
                break
    return {"rowcount": cur.rowcount, "results": results, "statusmessage": cur.statusmessage}


def as_repeated(cur, query, params, returning):
    total = 0
    results = []
    last_count = None
    for item in params:
        cur.execute(query, item)
        last_count = cur.rowcount
        total += max(cur.rowcount, 0)
        if returning:
            results.append(cur.fetchall())
    return {"rowcount": last_count if last_count is not None else -1, "rowcount_if_summed": total,
            "results": results, "statusmessage": cur.statusmessage}


def run_cell(box, limits, mode: str, case: str, runner) -> dict:
    box.reset(SETUP, [])
    autocommit = mode != "not_autocommit_commit_anyway"
    conn = open_session(box.settings, "cell", limits, autocommit=autocommit, connection_class=psycopg.Connection)
    outcome: dict = {"exception": None}
    query, params, returning = params_of(case)
    try:
        cur = conn.cursor()
        try:
            if mode == "autocommit_in_transaction":
                with conn.transaction():
                    outcome.update(runner(cur, query, params, returning))
            elif mode == "autocommit_in_pipeline_block":
                with conn.pipeline():
                    outcome.update(runner(cur, query, params, returning))
                    cur.execute(INSERT, (99, "after"))
            else:
                outcome.update(runner(cur, query, params, returning))
        except Exception as exc:
            outcome["exception"] = [type(exc).__name__, getattr(exc, "sqlstate", None)]
        outcome["txn_after_call"] = psycopg.pq.TransactionStatus(conn.pgconn.transaction_status).name
        if mode == "not_autocommit_commit_anyway":
            try:
                conn.commit()
            except Exception as exc:
                outcome["commit_exception"] = [type(exc).__name__, getattr(exc, "sqlstate", None)]
        outcome["prepared_statements"] = _prepared_count(conn)
    finally:
        conn.close()
    outcome["final_ids"] = sorted(r["id"] for r in box.snapshot()["t"] if r["id"] != 2)
    return outcome


def _prepared_count(conn) -> int | None:
    try:
        return conn.execute("SELECT count(*) FROM pg_prepared_statements").fetchone()[0]
    except psycopg.Error:
        return None


PUT_CASES = {
    "all_succeed": ([(10, "a"), (11, "b")], (99, "m"), None),
    "batch_conflict": ([(10, "a"), (2, "dup")], (99, "m"), None),
    "final_statement_conflict": ([(10, "a"), (11, "b")], (2, "dup"), None),
    "unadaptable_batch_parameter": ([(10, "a"), (object(), "x")], (99, "m"), None),
    "unadaptable_final_parameter": ([(10, "a"), (11, "b")], (object(), "m"), None),
    "python_error_after_batch": ([(10, "a"), (11, "b")], None, ValueError("application error")),
}


def put_shape(box, limits, wrapper: str, case: str) -> dict:
    """The shape of PostgresSaver.put: a batch, then one statement, in the library's pipeline or its fallback."""
    box.reset(SETUP, [])
    conn = open_session(box.settings, "put", limits, autocommit=True, connection_class=psycopg.Connection)
    batch, final, error = PUT_CASES[case]
    outcome: dict = {"exception": None}

    def body(cur):
        cur.executemany(INSERT, batch)
        if error:
            raise error
        cur.execute(INSERT, final)

    def body_repeated(cur):
        for item in batch:
            cur.execute(INSERT, item)
        if error:
            raise error
        cur.execute(INSERT, final)

    try:
        cur = conn.cursor()
        try:
            if wrapper == "pipeline":
                with conn.pipeline():
                    body(cur)
            elif wrapper == "fallback_transaction":
                with conn.transaction():
                    body(cur)
            else:
                body_repeated(cur)
        except Exception as exc:
            outcome["exception"] = [type(exc).__name__, getattr(exc, "sqlstate", None)]
        outcome["txn_after_call"] = psycopg.pq.TransactionStatus(conn.pgconn.transaction_status).name
    finally:
        conn.close()
    outcome["final_ids"] = sorted(r["id"] for r in box.snapshot()["t"] if r["id"] != 2)
    return outcome


def put_comparison(box, limits) -> list[dict]:
    rows = []
    for case in PUT_CASES:
        wrappers = ("pipeline", "fallback_transaction", "repeated_autocommit")
        cells = {w: put_shape(box, limits, w, case) for w in wrappers}
        rows.append({"case": case, **cells})
    return rows


def _plain_statement(conn, name):
    conn.execute("UPDATE q SET seen = seen || %s WHERE id = 1", (name,))


def plain_wakeup_control(box, limits, runs: int) -> dict:
    """Four plain sessions, same order, no controller: is the variation PostgreSQL's or the harness's?"""
    orders: Counter = Counter()
    for _ in range(runs):
        box.reset(QUEUE, [])
        conns = {n: open_session(box.settings, f"p{n}", limits, connection_class=psycopg.Connection) for n in "abcd"}
        conns["a"].execute("UPDATE q SET seen = seen || 'a' WHERE id = 1")
        threads = {}
        for name in "bcd":
            thread = threading.Thread(target=_plain_statement, args=(conns[name], name))
            thread.start()
            threads[name] = thread
            pid = conns[name].info.backend_pid
            while not box.blockers([pid])[pid]:
                time.sleep(0.0005)
        conns["a"].commit()
        waiting = set(threads)
        while waiting:  # commit whichever statement finished, because only it holds the row now
            for name in sorted(waiting):
                if not threads[name].is_alive():
                    conns[name].commit()
                    waiting.discard(name)
                    break
            else:
                time.sleep(0.0005)
        for conn in conns.values():
            conn.close()
        orders[box.snapshot()["q"][0]["seen"]] += 1
    return {"runs": runs, "wakeup_orders": dict(orders)}


def _deadlock_second(name, conn, value, row, results):
    try:
        conn.execute("UPDATE c SET v = %s WHERE id = %s", (value, row))
        results[name] = "ok"
        conn.commit()
    except psycopg.errors.DeadlockDetected:
        results[name] = "victim"
        conn.rollback()


def plain_deadlock_control(box, limits, runs: int) -> dict:
    victims: Counter = Counter()
    for _ in range(runs):
        box.reset(COUNTERS, [])
        a = open_session(box.settings, "pa", limits, connection_class=psycopg.Connection)
        b = open_session(box.settings, "pb", limits, connection_class=psycopg.Connection)
        a.execute("UPDATE c SET v = 10 WHERE id = 1")
        b.execute("UPDATE c SET v = 20 WHERE id = 2")
        results: dict[str, str] = {}
        first = threading.Thread(target=_deadlock_second, args=("a", a, 10, 2, results))
        first.start()
        pid = a.info.backend_pid
        while not box.blockers([pid])[pid]:
            time.sleep(0.0005)
        other = threading.Thread(target=_deadlock_second, args=("b", b, 20, 1, results))
        other.start()
        first.join(5)
        other.join(5)
        a.close()
        b.close()
        victims[next((k for k, v in results.items() if v == "victim"), None)] += 1
    return {"runs": runs, "victims": dict(victims)}


def strict_deadlock_replay(runner, runs: int) -> dict:
    """Replaying one recorded trace's exact decisions. Does a different victim show up as divergence or mismatch?"""
    scen = LiveScenario("deadlock", COUNTERS, [], [
        LiveActor("a", set_both, {"first": 1, "second": 2, "value": 10}),
        LiveActor("b", set_both, {"first": 2, "second": 1, "value": 20}),
    ])
    first = runner.run(scen, ExplicitSchedule(["a", "b", "a", "b", "a", "b"]))
    wanted_victim = next(r.actor for r in first.records if r.sqlstate == "40P01")
    outcomes: Counter = Counter()
    exact = same_outcome_state = same_victim = 0
    for _ in range(runs):
        again = runner.run(scen, ExplicitSchedule(first.chosen_schedule, strict=True))
        outcomes[again.outcome] += 1
        exact += again.fingerprint() == first.fingerprint()
        same_outcome_state += again.outcome == first.outcome and again.final_state == first.final_state
        same_victim += next((r.actor for r in again.records if r.sqlstate == "40P01"), None) == wanted_victim
    return {"runs": runs, "recorded_victim": wanted_victim, "outcomes": dict(outcomes), "exact_trace_matches": exact,
            "same_outcome_and_final_state": same_outcome_state, "same_victim": same_victim}


def semantics_matrix(box, limits) -> dict:
    rows = []
    for mode in MODES:
        for case in CASES:
            batch = run_cell(box, limits, mode, case, as_batch)
            repeated = run_cell(box, limits, mode, case, as_repeated)
            fields = ("final_ids", "exception", "commit_exception", "txn_after_call", "rowcount", "results")
            differs = [field for field in fields if batch.get(field) != repeated.get(field)]
            rows.append({"mode": mode, "case": case, "differs": differs, "batch": batch, "repeated": repeated})
    summary = {
        mode: {
            "cells": sum(r["mode"] == mode for r in rows),
            "committed_state_differs": [r["case"] for r in rows if r["mode"] == mode and "final_ids" in r["differs"]],
            "exception_differs": [r["case"] for r in rows if r["mode"] == mode and "exception" in r["differs"]],
            "rowcount_differs": [r["case"] for r in rows if r["mode"] == mode and "rowcount" in r["differs"]],
            "results_differ": [r["case"] for r in rows if r["mode"] == mode and "results" in r["differs"]],
            "txn_status_differs": [r["case"] for r in rows if r["mode"] == mode and "txn_after_call" in r["differs"]],
        }
        for mode in MODES
    }
    return {"summary": summary, "cells": rows}


# ---------------------------------------------------------------- what others can see while a batch is blocked


VISIBILITY_SETUP = """
CREATE TABLE r (id integer PRIMARY KEY, v integer NOT NULL);
INSERT INTO r VALUES (1, 0), (2, 0);
"""


def visibility(box, limits, runner_kind: str) -> dict:
    box.reset(VISIBILITY_SETUP, [])
    settings = box.settings
    holder = open_session(settings, "holder", limits, connection_class=psycopg.Connection)
    observer = open_session(settings, "observer", limits, autocommit=True, connection_class=psycopg.Connection)
    worker = open_session(settings, "worker", limits, autocommit=True, connection_class=psycopg.Connection)
    holder.execute("UPDATE r SET v = 9 WHERE id = 2")
    query = "UPDATE r SET v = %s WHERE id = %s"
    params = [(5, 1), (5, 2)]

    def work():
        cur = worker.cursor()
        if runner_kind == "executemany":
            cur.executemany(query, params)
        else:
            for item in params:
                cur.execute(query, item)

    thread = threading.Thread(target=work)
    thread.start()
    pid = worker.info.backend_pid
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not box.blockers([pid])[pid]:
        time.sleep(0.002)
    seen = observer.execute("SELECT v FROM r WHERE id = 1").fetchone()[0]
    try:
        observer.execute("SELECT 1 FROM r WHERE id = 1 FOR UPDATE NOWAIT")
        row_one_locked = False
    except psycopg.errors.LockNotAvailable:
        row_one_locked = True
    holder.rollback()
    thread.join(5)
    for conn in (holder, observer, worker):
        conn.close()
    return {"row1_visible_to_others_while_blocked": seen, "row1_still_locked_while_blocked": row_one_locked}


# ---------------------------------------------------------------- options A and B, in scratch cursor classes only


class OpaqueBatchCursor(SteppedCursor):
    """Option A: the whole call is one admitted step. Only a probe here; the prototype still refuses executemany."""

    def executemany(self, query, params_seq, *, returning=False):
        return self._channel().perform(
            "sql", _query_text(query, self._conn), None,
            lambda: psycopg.Cursor.executemany(self, query, params_seq, returning=returning), cursor=self,
        )


class SteppedBatchCursor(SteppedCursor):
    """Option B: one admitted step per parameter set."""

    def executemany(self, query, params_seq, *, returning=False):
        for item in params_seq:
            self.execute(query, item)


def batch_actor(cursor_class, in_transaction: bool):
    def actor(conn):
        conn.cursor_factory = cursor_class
        cur = conn.cursor()
        if in_transaction:
            with conn.transaction():
                cur.executemany("UPDATE r SET v = 5 WHERE id = %s", [(1,), (2,)])
        else:
            cur.executemany("UPDATE r SET v = 5 WHERE id = %s", [(1,), (2,)])

    return actor


def reader(conn):
    first = conn.execute("SELECT v FROM r WHERE id = 1").fetchone()[0]
    second = conn.execute("SELECT v FROM r WHERE id = 2").fetchone()[0]
    return [first, second]


def snapshot_reader(conn):
    """Sees one snapshot, so a torn pair can only come from the batch itself."""
    return conn.execute("SELECT array_agg(v ORDER BY id) FROM r").fetchone()[0]


def explore_option(runner, cursor_class, in_transaction: bool) -> dict:
    scen = LiveScenario("batch", VISIBILITY_SETUP, [], [
        LiveActor("a", batch_actor(cursor_class, in_transaction), autocommit=True),
        LiveActor("b", snapshot_reader, autocommit=True),
    ])
    result = explore_live(runner, scen, LiveBudget(max_runs=200))
    seen = Counter(json.dumps(t.results().get("b")) for t in result.traces)
    return {
        "interleavings": result.runs, "exhausted": result.exhausted, "outcomes": dict(result.outcomes),
        "reader_saw": dict(seen),
        "final_states": sorted({json.dumps(t.final_state["r"]) for t in result.traces}),
    }


LOG_SETUP = """
CREATE TABLE r (id integer PRIMARY KEY, v integer);
CREATE TABLE log (id serial PRIMARY KEY);
INSERT INTO r VALUES (1, NULL), (2, NULL);
"""
TORN = "UPDATE r SET v = (SELECT count(*) FROM log) WHERE id = %s AND (SELECT pg_sleep(%s)) IS NOT NULL"


def torn_batch_in_plain_psycopg(box, limits) -> dict:
    """A commit lands between two statements of one real executemany, in an explicit transaction. Nothing is stepped."""
    box.reset(LOG_SETUP, [])
    worker = open_session(box.settings, "worker", limits, connection_class=psycopg.Connection)
    other = open_session(box.settings, "other", limits, autocommit=True, connection_class=psycopg.Connection)

    def work():
        with worker.transaction():
            worker.cursor().executemany(TORN, [(1, 0.4), (2, 0.0)])

    thread = threading.Thread(target=work)
    thread.start()
    time.sleep(0.1)  # inside the first statement, after its snapshot
    other.execute("INSERT INTO log DEFAULT VALUES")
    thread.join(5)
    worker.close()
    other.close()
    return {"rows": box.snapshot()["r"]}


def torn_reachability(runner) -> dict:
    def batch(cursor_class):
        def actor(conn):
            conn.cursor_factory = cursor_class
            with conn.transaction():
                conn.cursor().executemany(TORN, [(1, 0), (2, 0)])

        return actor

    def inserter(conn):
        conn.execute("INSERT INTO log DEFAULT VALUES")

    found = {}
    for name, cursor_class in (("A_opaque", OpaqueBatchCursor), ("B_stepped", SteppedBatchCursor)):
        scen = LiveScenario("torn", LOG_SETUP, [], [
            LiveActor("a", batch(cursor_class)), LiveActor("b", inserter, autocommit=True),
        ])
        result = explore_live(runner, scen, LiveBudget(max_runs=200))
        found[name] = {
            "interleavings": result.runs, "outcomes": dict(result.outcomes),
            "final_row_values": sorted({json.dumps([r["v"] for r in t.final_state["r"]]) for t in result.traces}),
        }
    return found


def opaque_blocked_batch(runner) -> dict:
    """Option A with a real lock wait: does the blocked batch let the lock holder go on, and does it replay?"""

    def holder(conn):
        with conn.transaction():
            conn.execute("UPDATE r SET v = 7 WHERE id = 2")
            conn.execute("UPDATE r SET v = 8 WHERE id = 2")

    scen = LiveScenario("opaque_lock", VISIBILITY_SETUP, [], [
        LiveActor("a", batch_actor(OpaqueBatchCursor, False), autocommit=True),
        LiveActor("b", holder),
    ])
    first = runner.run(scen, ExplicitSchedule(["b", "b", "a", "b", "b"]))
    same = sum(
        runner.run(scen, ExplicitSchedule(first.chosen_schedule, strict=True)).fingerprint() == first.fingerprint()
        for _ in range(50)
    )
    record = next(r for r in first.records if r.actor == "a")
    return {"outcome": first.outcome, "blocked_by": record.blocked_by, "params_recorded": record.params,
            "effect": record.effect, "final": first.final_state["r"], "replays_matching_of_50": same}


# ---------------------------------------------------------------- deadlock and wakeup reproduction


class Burners:
    """CPU contention, to expose timing sensitivity that an idle machine hides."""

    def __enter__(self):
        count = os.cpu_count() or 4
        self.procs = [subprocess.Popen([sys.executable, "-c", "while True: pass"]) for _ in range(count)]
        return self

    def __exit__(self, *_):
        for proc in self.procs:
            proc.kill()
            proc.wait()


COUNTERS = "CREATE TABLE c (id integer PRIMARY KEY, v integer NOT NULL); INSERT INTO c VALUES (1, 0), (2, 0);"


def set_both(conn, first, second, value):
    with conn.transaction():
        conn.execute("UPDATE c SET v = %s WHERE id = %s", (value, first))
        conn.execute("UPDATE c SET v = %s WHERE id = %s", (value, second))
    return "committed"


def deadlock_reproduction(runner, runs: int) -> dict:
    scen = LiveScenario("deadlock", COUNTERS, [], [
        LiveActor("a", set_both, {"first": 1, "second": 2, "value": 10}),
        LiveActor("b", set_both, {"first": 2, "second": 1, "value": 20}),
    ])
    victims: Counter = Counter()
    outcomes: Counter = Counter()
    states: Counter = Counter()
    fingerprints: Counter = Counter()
    joint: Counter = Counter()
    for _ in range(runs):
        trace = runner.run(scen, ExplicitSchedule(["a", "b", "a", "b", "a", "b"]))
        victim = next((r.actor for r in trace.records if r.sqlstate == "40P01"), None)
        state = json.dumps([r["v"] for r in trace.final_state["c"]])
        victims[victim] += 1
        outcomes[trace.outcome] += 1
        states[state] += 1
        fingerprints[trace.fingerprint()] += 1
        joint[f"{victim}->{state}"] += 1
    return {"runs": runs, "outcomes": dict(outcomes), "victims": dict(victims), "final_states": dict(states),
            "distinct_exact_traces": len(fingerprints), "victim_to_state": dict(joint),
            "most_common_exact_trace_share": fingerprints.most_common(1)[0][1] / runs}


QUEUE = "CREATE TABLE q (id integer PRIMARY KEY, seen text NOT NULL); INSERT INTO q VALUES (1, '');"


def append_name(conn, name):
    with conn.transaction():
        conn.execute("UPDATE q SET seen = seen || %s WHERE id = 1", (name,))


def wakeup_reproduction(runner, runs: int) -> dict:
    scen = LiveScenario("wakeup", QUEUE, [], [LiveActor(n, append_name, {"name": n}) for n in "abcd"])
    order = ["a", "a", "b", "b", "c", "c", "d", "d", "a", "b", "c", "d"]
    orders: Counter = Counter()
    fingerprints: Counter = Counter()
    for _ in range(runs):
        trace = runner.run(scen, ExplicitSchedule(order))
        orders[trace.final_state["q"][0]["seen"]] += 1
        fingerprints[trace.fingerprint()] += 1
    return {"runs": runs, "wakeup_orders": dict(orders), "distinct_exact_traces": len(fingerprints)}


# ---------------------------------------------------------------- bypass detection


def own_connection_read(dsn):
    def actor(conn):
        with psycopg.connect(dsn, autocommit=True) as other:
            other.execute("SELECT 424242")
        conn.execute("SELECT 1")
        conn.rollback()

    return actor


def own_connection_write(dsn):
    def actor(conn):
        with psycopg.connect(dsn, autocommit=True) as other:
            other.execute("UPDATE ds_run.c SET v = 777 WHERE id = 1")
        conn.execute("SELECT 1")
        conn.rollback()

    return actor


def async_connection(dsn):
    def actor(conn):
        async def go():
            other = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
            await other.execute("SELECT 424243")
            await other.close()

        asyncio.run(go())
        conn.execute("SELECT 1")
        conn.rollback()

    return actor


def thread_connection(dsn):
    def actor(conn):
        def go():
            with psycopg.connect(dsn, autocommit=True) as other:
                other.execute("SELECT 424244")

        thread = threading.Thread(target=go)
        thread.start()
        thread.join()
        conn.execute("SELECT 1")
        conn.rollback()

    return actor


def subprocess_connection(dsn):
    code = "import psycopg,sys\nwith psycopg.connect(sys.argv[1],autocommit=True) as c: c.execute('SELECT 424245')"

    def actor(conn):
        subprocess.run([sys.executable, "-c", code, dsn], check=True, timeout=20)
        conn.execute("SELECT 1")
        conn.rollback()

    return actor


def raw_pgconn(_dsn):
    def actor(conn):
        conn.pgconn.exec_(b"SELECT 424246")
        conn.execute("SELECT 1")
        conn.rollback()

    return actor


def parent_class_wait(_dsn):
    def actor(conn):
        psycopg.Connection.wait(conn, conn._exec_command(b"SELECT 424247"))
        conn.execute("SELECT 1")
        conn.rollback()

    return actor


def clean(_dsn):
    def actor(conn):
        conn.execute("SELECT 1")
        conn.rollback()

    return actor


def plain_cursor_class(_dsn):
    def actor(conn):
        psycopg.Cursor(conn).execute("SELECT 424248")

    return actor


BYPASSES = [clean, own_connection_read, own_connection_write, async_connection, thread_connection,
            subprocess_connection, raw_pgconn, parent_class_wait, plain_cursor_class]


def sessions(box) -> int:
    return box.monitor.execute(
        "SELECT sessions FROM pg_stat_database WHERE datname = current_database()"
    ).fetchone()[0]


def docker_log_lines(container: str) -> list[str]:
    done = subprocess.run(["docker", "logs", container], capture_output=True, text=True)
    return (done.stdout + done.stderr).splitlines()


_MARK = re.compile(r"ds_audit_mark_(\d+)")
_STATEMENT = re.compile(r"^(?P<app>[^|]*)\|(?P<pid>\d+)\|LOG:\s+(?:statement|execute [^:]*): (?P<sql>.*)$")
_AUTHORIZED = re.compile(r"^[^|]*\|(?P<pid>\d+)\|LOG:\s+connection authorized: .*database=deployspecter_test")
_SETUP_PREFIXES = ("SET ", "SELECT set_config")


def reconcile(lines: list[str], mark: int, trace, harness_pids: set[int]) -> dict:
    """Every session and statement the server logged during the run must be one the trace can account for."""
    start = end = None
    for index, line in enumerate(lines):
        match = _MARK.search(line)
        if match and int(match.group(1)) == mark:
            start = index if start is None else start
        elif match and int(match.group(1)) == mark + 1:
            end = index
            break
    window = lines[start:end]
    registered = set(trace.connections.values())
    expected: dict[int, Counter] = {pid: Counter() for pid in registered}
    for record in trace.records:
        expected[record.backend_pid][_normal(record.sql)] += 1
    unknown_sessions = sorted(
        {int(m.group("pid")) for m in map(_AUTHORIZED.match, window) if m} - registered - harness_pids
    )
    unexpected: list[str] = []
    for line in window:
        match = _STATEMENT.match(line)
        if not match:
            continue
        pid = int(match.group("pid"))
        sql = _normal(match.group("sql"))
        if pid in harness_pids:
            continue
        if pid not in registered:
            unexpected.append(sql[:60])
            continue
        if expected[pid][sql] > 0:
            expected[pid][sql] -= 1
        elif not (sql.startswith(_SETUP_PREFIXES) or sql == "BEGIN"):  # session setup, or psycopg's implicit BEGIN
            unexpected.append(sql[:60])
    missing = sum(max(v, 0) for counter in expected.values() for v in counter.values())
    return {"unknown_sessions": len(unknown_sessions), "unexpected_statements": unexpected,
            "missing_statements": missing}


def _normal(sql: str) -> str:
    return re.sub(r"\$\d+", "%s", " ".join(sql.split())).rstrip(";")


def bypass_table(box, runner, container: str) -> list[dict]:
    dsn = box.settings.test_dsn
    table = []
    mark = int(time.time()) * 10
    harness = {box.admin.info.backend_pid, box.monitor.info.backend_pid}
    for maker in BYPASSES:
        scen = LiveScenario(maker.__name__, COUNTERS, [], [LiveActor("a", maker(dsn))])
        box.reset(COUNTERS, [])
        before = sessions(box)
        box.monitor.execute(f"SELECT 'ds_audit_mark_{mark}'")
        trace = runner.run(scen, ExplicitSchedule([]))
        box.monitor.execute(f"SELECT 'ds_audit_mark_{mark + 1}'")
        time.sleep(0.2)
        delta = sessions(box) - before
        log = reconcile(docker_log_lines(container), mark, trace, harness)
        table.append({
            "scenario": maker.__name__,
            "current_outcome": trace.outcome,
            "final_c1": trace.final_state["c"][0]["v"],
            "sessions_delta": delta,
            "expected_sessions": 1,
            "counter_flags": delta != 1,
            "log_unknown_sessions": log["unknown_sessions"],
            "log_unexpected_statements": log["unexpected_statements"],
            "log_missing_statements": log["missing_statements"],
            "log_flags": bool(log["unknown_sessions"] or log["unexpected_statements"] or log["missing_statements"]),
        })
        mark += 2
    return table


def reservations_reconciliation(box, runner, container: str) -> dict:
    root = Path(__file__).resolve().parents[1] / "tests"
    sys.path.insert(0, str(root))
    from live_app import reservations as app

    seed = [SeedRow(table="events", values=[
        ColumnValue(column="id", value=1), ColumnValue(column="capacity", value=3),
        ColumnValue(column="reserved", value=0),
    ])]
    scen = LiveScenario("clean", app.SETUP_SQL, seed, [
        LiveActor("a", app.reserve_seats, dict(event_id=1, holder="alice", qty=2, lock_row=True)),
        LiveActor("b", app.reserve_seats, dict(event_id=1, holder="bob", qty=2, lock_row=True)),
    ])
    mark = int(time.time()) * 10 + 5000
    box.monitor.execute(f"SELECT 'ds_audit_mark_{mark}'")
    trace = runner.run(scen, ExplicitSchedule(["a", "b", "a", "b", "a", "a", "a", "b", "b", "b"]))
    box.monitor.execute(f"SELECT 'ds_audit_mark_{mark + 1}'")
    time.sleep(0.2)
    harness = {box.admin.info.backend_pid, box.monitor.info.backend_pid}
    return reconcile(docker_log_lines(container), mark, trace, harness)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--container", required=True)
    args = parser.parse_args()
    settings = load_settings()
    if ":55432/" in settings.admin_dsn:
        print("refusing: point DATABASE_URL at a disposable database")
        return 2
    report: dict = {"model_requests": 0, "psycopg": psycopg.__version__, "libpq": psycopg.pq.version()}
    with Sandbox(settings) as box:
        limits = LiveLimits.from_settings(settings)
        report["executemany_matrix"] = semantics_matrix(box, limits)
        report["visibility_while_blocked"] = {
            k: visibility(box, limits, k) for k in ("executemany", "repeated_execute")
        }
        report["torn_batch_plain_psycopg"] = torn_batch_in_plain_psycopg(box, limits)
        report["put_shape"] = put_comparison(box, limits)
        runner = LiveRunner(box)
        report["option_exploration"] = {
            "A_autocommit": explore_option(runner, OpaqueBatchCursor, False),
            "B_autocommit": explore_option(runner, SteppedBatchCursor, False),
            "A_explicit_transaction": explore_option(runner, OpaqueBatchCursor, True),
            "B_explicit_transaction": explore_option(runner, SteppedBatchCursor, True),
        }
        report["torn_reachability"] = torn_reachability(runner)
        report["opaque_blocked_batch"] = opaque_blocked_batch(runner)
        report["bypass"] = bypass_table(box, runner, args.container)
        report["clean_reconciliation"] = reservations_reconciliation(box, runner, args.container)
        report["deadlock_idle"] = deadlock_reproduction(runner, 200)
        report["deadlock_strict_replay"] = strict_deadlock_replay(runner, 200)
        report["deadlock_plain_control"] = plain_deadlock_control(box, limits, 200)
        report["wakeup_plain_control"] = plain_wakeup_control(box, limits, 200)
        report["wakeup_idle"] = wakeup_reproduction(runner, 200)
        with Burners():
            report["deadlock_cpu_load"] = deadlock_reproduction(runner, 200)
            report["wakeup_cpu_load"] = wakeup_reproduction(runner, 200)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=1, sort_keys=True, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
