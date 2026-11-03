"""Application functions that try to escape the harness, or behave in ways it must handle. All synthetic.

They're importable because a supervised run imports them in a fresh child process.
"""

from __future__ import annotations

import asyncio
import ctypes
import gc
import json
import os
import stat
import subprocess
import sys
import threading
import time

import psycopg
from psycopg.rows import dict_row

SETUP = """
CREATE TABLE c (id integer PRIMARY KEY, v integer NOT NULL);
INSERT INTO c VALUES (1, 0), (2, 0);
"""
COMMIT_DELAY_SETUP = SETUP + """
CREATE FUNCTION slow_commit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN PERFORM pg_sleep(1.5); RETURN NULL; END $$;
CREATE CONSTRAINT TRIGGER slow_commit AFTER UPDATE ON c DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION slow_commit();
"""


# ------------------------------------------------------------------ well-behaved


def clean(conn):
    conn.execute("SELECT 1")
    conn.rollback()
    return "ok"


def bump(conn, row=1, value=1):
    with conn.transaction():
        conn.execute("UPDATE c SET v = v + %s WHERE id = %s", (value, row))
    return "ok"


def set_pair(conn, first, second, value):
    with conn.transaction():
        conn.execute("UPDATE c SET v = %s WHERE id = %s", (value, first))
        conn.execute("UPDATE c SET v = %s WHERE id = %s", (value, second))
    return "committed"


def retry_pair(conn, first, second, value):
    for attempt in range(4):
        try:
            set_pair(conn, first, second, value)
            return attempt + 1
        except psycopg.errors.DeadlockDetected:
            continue
    return None


def savepoints(conn):
    with conn.transaction():
        conn.execute("UPDATE c SET v = 1 WHERE id = 1")
        try:
            with conn.transaction():
                conn.execute("UPDATE c SET v = 2 WHERE id = 2")
                conn.execute("UPDATE c SET missing = 1")
        except psycopg.errors.UndefinedColumn:
            pass
        with conn.transaction(savepoint_name="kept"):
            conn.execute("UPDATE c SET v = 3 WHERE id = 1")
    return "ok"


def text_transaction(conn):
    conn.execute("UPDATE c SET v = 1 WHERE id = 1")
    conn.execute("BEGIN")
    conn.execute("UPDATE c SET v = 2 WHERE id = 2")
    conn.execute("SAVEPOINT s1")
    conn.execute("UPDATE c SET v = 99 WHERE id = 2")
    conn.execute("ROLLBACK TO SAVEPOINT s1")
    conn.execute("COMMIT")
    return "ok"


def multi_line_and_quotes(conn):
    conn.execute(
        """SELECT 'it''s',
                  %s::text, -- a comment
                  $$dollar;quoted$$""",
        ("value with 'quote' and\nnewline",),
    )
    conn.rollback()
    return "ok"


def named_params(conn):
    row = conn.execute("SELECT %(a)s::int + %(b)s::int", {"a": 2, "b": 3}).fetchone()
    conn.rollback()
    return row[0]


def many_types(conn):
    row = conn.execute(
        "SELECT %s::numeric, %s::float8, %s::bool, %s::bytea, %s::text, now()",
        (1.5, 2.5, True, b"bin", None),
    ).fetchone()
    conn.rollback()
    return [str(row[0]), row[1], row[2], len(row[3]), row[4]]


def reads_with_factories(conn):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id, v FROM c ORDER BY id")
        first = cur.fetchone()
        rest = cur.fetchall()
    conn.rollback()
    return {"first": first, "rest": rest}


def forgets_commit(conn):
    conn.execute("UPDATE c SET v = 5 WHERE id = 1")
    return "forgot"


def app_error(conn):
    with conn.transaction():
        conn.execute("UPDATE c SET v = 5 WHERE id = 1")
        raise ValueError("application error")


def sql_error(conn):
    conn.execute("SELECT * FROM no_such_table")


def prints_to_stdout(conn):
    print('{"type": "result", "trace": {}}')
    sys.stderr.write("noise\n")
    conn.execute("SELECT 1")
    conn.rollback()
    return "ok"


def batch(conn, rows, transaction=False):
    query = "UPDATE c SET v = %s WHERE id = %s"
    cur = conn.cursor()
    if transaction:
        with conn.transaction():
            cur.executemany(query, [tuple(r) for r in rows])
    else:
        cur.executemany(query, [tuple(r) for r in rows])
    return cur.rowcount


# ------------------------------------------------------------------ what the process and session can reach


def inspect_process(conn):
    interesting = [k for k in os.environ if k.startswith("PG") or k in {"DATABASE_URL", "MODEL_API_KEY"}]
    row = conn.execute("SELECT current_user, session_user").fetchone()
    conn.rollback()
    sockets = 0
    for fd in range(256):
        try:
            if stat.S_ISSOCK(os.fstat(fd).st_mode):
                sockets += 1
        except OSError:
            continue
    users = sorted({c.info.user for c in gc.get_objects() if isinstance(c, psycopg.Connection) and not c.closed})
    return {"env": interesting, "cwd_has_env_file": os.path.exists(".env"), "user": row[0], "session_user": row[1],
            "args": sys.argv[1:], "open_sockets": sockets, "connection_users": users}


def probe_files(conn, targets):
    import socket

    results = {}
    for name, path in targets.items():
        try:
            if name.endswith("_write"):
                with open(path, "w") as handle:
                    handle.write("x")
            elif os.path.isdir(path):
                os.listdir(path)
            else:
                with open(path) as handle:
                    handle.read(1)
            results[name] = "ok"
        except OSError as exc:
            results[name] = type(exc).__name__
    try:
        socket.create_connection(("1.1.1.1", 80), timeout=2).close()
        results["outbound_network"] = "ok"
    except OSError as exc:
        results["outbound_network"] = type(exc).__name__
    clean(conn)
    return results


def report_sys_path(conn):
    clean(conn)
    return list(sys.path)


def catches_statement_timeout(conn):
    try:
        conn.execute("SELECT pg_sleep(5)")
    except psycopg.errors.QueryCanceled:
        conn.rollback()
        return "caught"
    return "not reached"


def raw_privilege_probe(conn):
    """Direct libpq calls, past the Python instrumentation. The server must refuse what matters."""
    results = {}
    for statement in (
        "RESET ROLE", "SET ROLE deployspecter", "SET SESSION AUTHORIZATION deployspecter", "DROP TABLE ds_run.c",
        "CREATE TABLE ds_run.x (i int)", "CREATE TEMP TABLE y (i int)", "CREATE ROLE z SUPERUSER",
        "SELECT pg_read_file('postgresql.conf')", "COPY (SELECT 1) TO PROGRAM 'cat'",
    ):
        result = conn.pgconn.exec_(statement.encode())
        results[statement] = (result.error_field(psycopg.pq.DiagnosticField.SQLSTATE) or b"ok").decode()
    who = conn.pgconn.exec_(b"SELECT current_user, session_user, (SELECT rolsuper FROM pg_roles "
                            b"WHERE rolname = current_user)")
    results["who"] = [who.get_value(0, i).decode() for i in range(3)]
    return results


# ------------------------------------------------------------------ bypasses


def own_connection_read(conn, dsn):
    with psycopg.connect(dsn, autocommit=True) as other:
        other.execute("SELECT 424242")
    return clean(conn)


def own_connection_write(conn, dsn):
    with psycopg.connect(dsn, autocommit=True) as other:
        other.execute("UPDATE ds_run.c SET v = 777 WHERE id = 1")
    return clean(conn)


def stolen_password_connection(conn):
    """Reads its own login from libpq and tries a further session. CONNECTION LIMIT should refuse this one."""
    info = conn.info
    try:
        with psycopg.connect(host=info.host, port=info.port, dbname=info.dbname, user=info.user,
                             password=info.password, connect_timeout=5):
            attempted = "connected"
    except psycopg.OperationalError:
        attempted = "refused"
    clean(conn)
    return attempted


def async_connection(conn, dsn):
    async def go():
        other = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
        await other.execute("SELECT 424243")
        await other.close()

    asyncio.run(go())
    return clean(conn)


def thread_connection(conn, dsn):
    def go():
        with psycopg.connect(dsn, autocommit=True) as other:
            other.execute("SELECT 424244")

    thread = threading.Thread(target=go)
    thread.start()
    thread.join()
    return clean(conn)


def subprocess_connection(conn, dsn):
    code = "import psycopg,sys\nwith psycopg.connect(sys.argv[1],autocommit=True) as c: c.execute('SELECT 424245')"
    subprocess.run([sys.executable, "-c", code, dsn], check=True, timeout=30)
    return clean(conn)


def raw_pgconn(conn):
    conn.pgconn.exec_(b"SELECT 424246")
    return clean(conn)


def raw_pgconn_write(conn):
    conn.pgconn.exec_(b"UPDATE c SET v = 888 WHERE id = 1")
    return clean(conn)


def parent_class_wait(conn):
    psycopg.Connection.wait(conn, conn._exec_command(b"SELECT 424247"))
    return clean(conn)


def repeated_identical_statement(conn):
    conn.pgconn.exec_(b"SELECT 1")  # identical text to the stepped statement that follows
    conn.execute("SELECT 1")
    conn.rollback()
    return "ok"


def raw_same_text_different_values(conn):
    conn.pgconn.exec_params(b"SELECT $1::int", [b"999"])
    conn.execute("SELECT %s::int", (5,))
    conn.rollback()
    return "ok"


def raw_prepared_transaction(conn):
    conn.pgconn.exec_(b"BEGIN")
    conn.pgconn.exec_(b"UPDATE c SET v = 555 WHERE id = 1")
    conn.pgconn.exec_(b"PREPARE TRANSACTION 'escaped'")
    return clean(conn)


def uses_monitor_connection(conn):
    """Finds the harness's own monitor session in this process, and uses it."""
    for item in gc.get_objects():
        if type(item) is psycopg.Connection and not item.closed:
            item.execute("UPDATE c SET v = 321 WHERE id = 1")
            return clean(conn)
    return clean(conn)


def write_fake_result(conn):
    """Writes a result straight to the supervisor's pipe, claiming nothing happened."""
    message = {"type": "result", "trace": {"scenario": "forged", "requested_schedule": [], "outcome": "completed"}}
    os.write(int(sys.argv[1]), (json.dumps(message) + "\n").encode())
    time.sleep(5)


def holds_own_connection(conn, dsn, hold=0.3):
    with psycopg.connect(dsn, autocommit=True) as other:
        other.execute("SELECT 424249")
        time.sleep(hold)
    return clean(conn)


def takes_advisory(conn):
    conn.execute("SELECT pg_advisory_lock(424250)")
    conn.rollback()
    return "ok"


def table_reader(conn):
    conn.execute("SELECT * FROM c")
    conn.rollback()
    return "ok"


def pause_then_bump(conn, pause=0.4):
    conn.execute("SELECT pg_sleep(%s)", (pause,))
    conn.rollback()
    return bump(conn)


def uses_stream(conn):
    list(conn.cursor().stream("SELECT 1"))


def uses_pipeline(conn):
    with conn.pipeline():
        conn.execute("SELECT 1")


# ------------------------------------------------------------------ process lifecycle


def infinite_python_loop(conn):
    while True:
        pass


def gil_holding_c_call(conn):
    ctypes.PyDLL(None).sleep(120)


def python_sleep_forever(conn):
    time.sleep(600)


def uncancellable_statement(conn):
    """Swallows the cancel on the server, so a cancel really is lost. Statement timeout can't stop it either."""
    conn.execute("""DO $$ BEGIN
        LOOP
            BEGIN PERFORM pg_sleep(0.05); EXCEPTION WHEN query_canceled THEN NULL; END;
        END LOOP;
    END $$""")


def sleeping_statement(conn):
    conn.execute("SELECT pg_sleep(600)")


def swallows_connection_loss(conn):
    try:
        with conn.transaction():
            conn.execute("UPDATE c SET v = 1 WHERE id = 1")
            conn.execute("SELECT pg_sleep(600)")
    except Exception:
        return "carried on as if nothing happened"
    return "ok"


def write_then_wait(conn):
    with conn.transaction():
        conn.execute("UPDATE c SET v = 41 WHERE id = 1")
        conn.execute("SELECT pg_sleep(600)")
    return "ok"


def hold_row(conn, seconds):
    with conn.transaction():
        conn.execute("UPDATE c SET v = 5 WHERE id = 1")
        conn.execute("SELECT pg_sleep(%s)", (seconds,))
    return "ok"


def slow_commit(conn):
    with conn.transaction():
        conn.execute("UPDATE c SET v = 7 WHERE id = 1")
    return "committed"


def autocommit_write(conn):
    conn.execute("UPDATE c SET v = 11 WHERE id = 1")
    return "ok"


def abandoned_thread_commits_later(conn, delay):
    """Leaves a thread with its own transaction, which would commit after the actor returns."""
    info = conn.info
    login = {"host": info.host, "port": info.port, "dbname": info.dbname, "user": info.user, "password": info.password}

    def later():
        time.sleep(delay)
        try:
            with psycopg.connect(**login) as other, other.transaction():
                other.execute("UPDATE ds_run.c SET v = 666 WHERE id = 2")
        except psycopg.Error:
            pass

    threading.Thread(target=later, daemon=False).start()
    return clean(conn)


def detached_process_commits_later(conn, delay, result_file):
    """Starts a process outside the process group. Only the dropped role stops it logging in later."""
    info = conn.info
    login = {"host": info.host, "port": info.port, "dbname": info.dbname, "user": info.user, "password": info.password}
    code = (
        "import json,sys,time,psycopg\n"
        "login,delay,out=json.loads(sys.argv[1]),float(sys.argv[2]),sys.argv[3]\n"
        "time.sleep(delay)\n"
        "try:\n"
        "    with psycopg.connect(**login,connect_timeout=5) as c, c.transaction():\n"
        "        c.execute('UPDATE ds_run.c SET v = 666 WHERE id = 2')\n"
        "    r='committed'\n"
        "except Exception as e:\n"
        "    r=type(e).__name__+': '+str(e)[:200]\n"
        "open(out,'w').write(r)\n"
    )
    subprocess.Popen([sys.executable, "-c", code, json.dumps(login), str(delay), result_file], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_scrubbed())
    return clean(conn)


def _scrubbed():
    """The environment without the supervisor's run marker: the hard case, where the process scan can't find it."""
    return {k: v for k, v in os.environ.items() if k != "DS_LIVE_RUN"}


def detached_process_left_running(conn, pid_file):
    """Keeps the inherited environment and no database connection. Only the process scan can see it."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"], start_new_session=True,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(pid_file, "w") as handle:
        handle.write(str(process.pid))
    return clean(conn)


def child_in_the_process_group_left_running(conn, pid_file):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(pid_file, "w") as handle:
        handle.write(str(process.pid))
    return clean(conn)


def detached_process_holds_session(conn, delay, result_file, pid_file):
    """Connects before the actor returns and tries to commit afterwards, on a session that already exists.

    The connection limit leaves no spare slot, so the actor's monitor-free role needs a free one: tests give it one.
    """
    info = conn.info
    login = {"host": info.host, "port": info.port, "dbname": info.dbname, "user": info.user, "password": info.password}
    code = (
        "import json,sys,time,psycopg\n"
        "login,delay,out,pidf=json.loads(sys.argv[1]),float(sys.argv[2]),sys.argv[3],sys.argv[4]\n"
        "c=psycopg.connect(**login,connect_timeout=5)\n"
        "open(pidf,'w').write(str(c.info.backend_pid))\n"
        "c.execute('UPDATE ds_run.c SET v = 667 WHERE id = 2')\n"
        "time.sleep(delay)\n"
        "try:\n"
        "    c.commit()\n"
        "    r='committed'\n"
        "except Exception as e:\n"
        "    r=type(e).__name__\n"
        "open(out,'w').write(r)\n"
    )
    subprocess.Popen([sys.executable, "-c", code, json.dumps(login), str(delay), result_file, pid_file],
                     start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, env=_scrubbed())
    for _ in range(600):
        if os.path.exists(pid_file):
            break
        time.sleep(0.01)
    return clean(conn)
