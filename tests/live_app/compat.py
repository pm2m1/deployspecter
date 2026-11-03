"""The 44 executemany compatibility cells from the Gate 2 audit, as importable application functions."""

from __future__ import annotations

import psycopg

SETUP = """
CREATE TABLE t (id integer PRIMARY KEY, v text NOT NULL CHECK (v <> 'bad'));
INSERT INTO t VALUES (2, 'seed');
"""
INSERT = "INSERT INTO t (id, v) VALUES (%s, %s)"
MODES = ["autocommit_bare", "autocommit_in_transaction", "not_autocommit_commit_anyway", "autocommit_in_pipeline_block"]
CASES = [
    "all_succeed", "conflict_in_middle", "check_fails_last", "unadaptable_parameter_in_middle",
    "wrong_arity_in_middle", "on_conflict_do_nothing", "returning_collected", "returning_not_collected",
    "update_many_rows", "empty_sequence", "generator_raises_after_first",
]


def _params(case: str):
    if case == "all_succeed":
        return INSERT, [(10, "a"), (11, "b"), (12, "c")], False
    if case == "conflict_in_middle":
        return INSERT, [(10, "a"), (2, "dup"), (12, "c")], False
    if case == "check_fails_last":
        return INSERT, [(10, "a"), (11, "b"), (12, "bad")], False
    if case == "unadaptable_parameter_in_middle":
        return INSERT, [(10, "a"), (object(), "x"), (12, "c")], False
    if case == "wrong_arity_in_middle":
        return INSERT, [(10, "a"), (11,), (12, "c")], False
    if case == "on_conflict_do_nothing":
        return f"{INSERT} ON CONFLICT DO NOTHING", [(10, "a"), (2, "dup"), (12, "c")], False
    if case == "returning_collected":
        return f"{INSERT} RETURNING id", [(10, "a"), (11, "b")], True
    if case == "returning_not_collected":
        return f"{INSERT} RETURNING id", [(10, "a"), (11, "b")], False
    if case == "update_many_rows":
        return "UPDATE t SET v = %s WHERE id >= %s", [("x", 0), ("y", 2)], False
    if case == "empty_sequence":
        return INSERT, [], False
    if case == "generator_raises_after_first":
        def generate():
            yield (10, "a")
            raise ValueError("application iterator failed")

        return INSERT, generate(), False
    raise ValueError(case)


def _batch(cur, query, params, returning):
    cur.executemany(query, params, returning=returning)
    results = []
    if returning:
        while True:
            results.append(cur.fetchall())
            if not cur.nextset():
                break
    return {"rowcount": cur.rowcount, "results": results, "statusmessage": cur.statusmessage}


def cell(conn, mode: str, case: str) -> dict:
    query, params, returning = _params(case)
    outcome: dict = {"exception": None}
    cur = conn.cursor()
    try:
        if mode == "autocommit_in_transaction":
            with conn.transaction():
                outcome.update(_batch(cur, query, params, returning))
        elif mode == "autocommit_in_pipeline_block":
            with conn.pipeline():
                outcome.update(_batch(cur, query, params, returning))
                cur.execute(INSERT, (99, "after"))
        else:
            outcome.update(_batch(cur, query, params, returning))
    except Exception as exc:
        outcome["exception"] = [type(exc).__name__, getattr(exc, "sqlstate", None)]
    outcome["txn_after_call"] = psycopg.pq.TransactionStatus(conn.pgconn.transaction_status).name
    if mode == "not_autocommit_commit_anyway":
        try:
            conn.commit()
        except Exception as exc:
            outcome["commit_exception"] = [type(exc).__name__, getattr(exc, "sqlstate", None)]
    return outcome


def batch_in_transaction(conn):
    """A statement's value depends on what has committed, so another session's commit could tear the batch."""
    with conn.transaction():
        conn.cursor().executemany(
            "UPDATE r SET v = (SELECT count(*) FROM log) WHERE id = %s", [(1,), (2,)],
        )
    return "ok"


def insert_log(conn):
    conn.execute("INSERT INTO log DEFAULT VALUES")
    return "ok"


def batch_two_rows(conn):
    cur = conn.cursor()
    cur.executemany("UPDATE r SET v = 5 WHERE id = %s", [(1,), (2,)])
    return cur.rowcount


def reader(conn):
    row = conn.execute("SELECT array_agg(v ORDER BY id) FROM r").fetchone()[0]
    return row


def row_two_holder(conn):
    with conn.transaction():
        conn.execute("UPDATE r SET v = 7 WHERE id = 2")
        conn.execute("UPDATE r SET v = 8 WHERE id = 2")
    return "ok"


def batch_commit_text(conn):
    conn.cursor().executemany("COMMIT", [()])


def batch_multi_statement(conn):
    conn.cursor().executemany("SELECT %s; SELECT %s", [(1, 2)])
