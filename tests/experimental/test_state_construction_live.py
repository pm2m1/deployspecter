"""EXPERIMENTAL state construction, live, on the disposable MVP PostgreSQL only (skipped unless it is running).

Run under ``research_tools/pg_lock.py`` when other agents share the database. Not Gate 2A's supervised runner and
not Gate A2 evidence.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

import psycopg
import pytest

from deployspecter.experimental import harness as hz
from deployspecter.experimental.state_construction import chancy, generic, phase9b, pipeline, typed
from deployspecter.experimental.state_construction.typed import TV
from deployspecter.research.track_a import scenario as sc

SECRET = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
PORT = 55441
pytestmark = pytest.mark.skipif(not SECRET.exists(), reason="the disposable MVP PostgreSQL isn't running")
BOTH_READ_FIRST = ["a", "b"] * 6
LAST_WRITER = """CREATE TABLE quotes (sku int, vendor int, cents int NOT NULL, PRIMARY KEY (sku, vendor));
CREATE TABLE best_quote (sku int PRIMARY KEY, cents int NOT NULL);
CREATE FUNCTION keep() RETURNS trigger AS $$ BEGIN
    INSERT INTO best_quote VALUES (NEW.sku, NEW.cents) ON CONFLICT (sku) DO UPDATE SET cents = EXCLUDED.cents;
    RETURN NEW; END $$ LANGUAGE plpgsql;
CREATE TRIGGER t AFTER INSERT ON quotes FOR EACH ROW EXECUTE FUNCTION keep();"""
EXACT = "CREATE TABLE v (id bigint PRIMARY KEY, f double precision, s text, b boolean, n integer);"


@pytest.fixture(scope="module")
def target():
    return hz.Target("127.0.0.1", PORT, "ds_mvp", SECRET.read_text().strip())


@pytest.fixture(scope="module")
def sandbox(target):
    sb = phase9b.open_sandbox(PORT, target.user, target.password)
    yield sb
    sb.close()
    assert phase9b.teardown(PORT, target.user, target.password) == {"database_dropped": True, "role_dropped": True}
    admin = target.admin()
    admin.execute("DROP SCHEMA IF EXISTS ds_run CASCADE")
    for (s,) in admin.execute("SELECT nspname::text FROM pg_namespace WHERE nspname LIKE 'ds_sc_%'").fetchall():
        admin.execute(f'DROP SCHEMA "{s}" CASCADE')
    admin.close()


def schema_exists(target, schema):
    admin = target.admin()
    try:
        return admin.execute("SELECT to_regnamespace(%s) IS NOT NULL", (schema,)).fetchone()[0]
    finally:
        admin.close()


def foreign_triggers(target):
    admin = target.admin()
    try:
        return admin.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN "
                             "pg_namespace n ON n.oid = c.relnamespace WHERE NOT t.tgisinternal AND "
                             "n.nspname <> 'ds_run'").fetchone()[0]
    finally:
        admin.close()


def test_the_live_catalog_mode_fails_closed_on_a_trigger_anywhere_in_the_database(target, sandbox):
    o = pipeline.run(target, sc.DOCUMENT, variant="fixed", schedule=BOTH_READ_FIRST, sandbox=sandbox)
    if foreign_triggers(target):  # e.g. mvp_app's v2 trigger, left by the core suite's mixed-version app
        assert o.status == "refused_planning" and o.detail["outcome"] == "indeterminate"
        assert {d["code"] for d in o.detail["detail"]} == {"trigger_effect_unknown"} and o.construction is None
    else:
        assert o.status == "ran" and o.run.violations == [] and o.run.corroboration["ok"]


def test_the_pipeline_runs_both_builds_on_the_phase9b_constructed_state(target, sandbox):
    results = {v: pipeline.run(target, sc.DOCUMENT, variant=v, schedule=BOTH_READ_FIRST, sandbox=sandbox,
                               catalog="dry_run") for v in ("buggy", "fixed")}
    for o in results.values():
        assert o.status == "ran", o.detail
        assert o.construction["classification"] == "ORIGINAL_VALID"
        assert o.verification["typed_exact"] == [] and o.verification["constructor_snapshot_equal"] is True
        assert o.run.corroboration["ok"] and o.run.corroboration["params_mismatched"] == 0
        assert o.run.initial_digest == o.verification["initial_digest"]
    assert results["buggy"].run.violations == ["R1 event 1: reserved=2 but reservations hold 4",
                                               "R2 event 1: 4 seats reserved of capacity 3"]
    assert results["fixed"].run.violations == []


def test_a_refused_plan_constructs_nothing(target, sandbox):
    d = copy.deepcopy(sc.DOCUMENT)
    d["state_goals"][0]["constraints"].append({"field": "colour", "op": "eq",
                                               "value": {"type": "str", "value": "red"}})
    admin = target.admin()
    admin.execute("DROP SCHEMA IF EXISTS ds_run CASCADE")
    admin.close()
    o = pipeline.run(target, d, variant="fixed", schedule=BOTH_READ_FIRST, sandbox=sandbox, catalog="dry_run")
    assert o.status == "refused_planning" and o.stage == "plan_state" and o.construction is None and o.run is None
    assert not schema_exists(target, "ds_run")  # nothing was built, so nothing was inserted
    o = pipeline.run(target, d, variant="fixed", schedule=BOTH_READ_FIRST, sandbox=sandbox, catalog="live")
    assert o.status == "refused_planning" and o.construction is None and o.run is None
    admin = target.admin()
    try:  # the live catalog export made the schema; no row was inserted into it
        counts = [admin.execute(f"SELECT count(*) FROM ds_run.{t}").fetchone()[0]
                  for t in ("events", "reservations", "rejections")]
    finally:
        admin.close()
    assert counts == [0, 0, 0]


def test_a_trigger_dependent_state_is_constructed_in_the_constructors_order_and_verified(target, sandbox):
    rows = [("quotes", {"sku": TV("int", 1), "vendor": TV("int", 2), "cents": TV("int", 20)}),
            ("quotes", {"sku": TV("int", 1), "vendor": TV("int", 1), "cents": TV("int", 10)}),
            ("best_quote", {"sku": TV("int", 1), "cents": TV("int", 20)})]
    r = generic.construct_typed(target, sandbox, setup_sql=LAST_WRITER, rows=rows, schema="ds_sc_t_reorder")
    assert r.status == "constructed" and r.classification == "CONSTRUCTED_EQUIVALENT" and r.order == [1, 0]
    assert r.verification == {"typed_exact": [], "constructor_snapshot_equal": True}


def test_exact_values_survive_and_drift_is_caught_by_the_independent_read(target, sandbox):
    rows = [("v", {"id": TV("int", 2**63 - 1), "f": TV("float", -0.0), "s": TV("str", "NULL"),
                   "b": TV("bool", False), "n": TV("null", None)}),
            ("v", {"id": TV("int", 1), "f": TV("float", 5e-324), "s": TV("null", None), "b": TV("bool", True),
                   "n": TV("int", 0)})]
    r = generic.construct_typed(target, sandbox, setup_sql=EXACT, rows=rows, schema="ds_sc_t_exact")
    assert r.status == "constructed", r.detail
    want = [v for _, v in rows]
    with psycopg.connect(**target.conninfo(), autocommit=True) as conn:
        assert typed.verify(conn, "ds_sc_t_exact", "v", want) == []
        for drift in ("UPDATE ds_sc_t_exact.v SET f = 0.0 WHERE f = 0",  # -0.0 → 0.0: equal under =, not exact
                      "UPDATE ds_sc_t_exact.v SET s = NULL WHERE s = 'NULL'",
                      "UPDATE ds_sc_t_exact.v SET n = NULL WHERE n = 0",
                      "UPDATE ds_sc_t_exact.v SET f = 1e-323 WHERE id = 1",  # one ulp-scale change
                      "INSERT INTO ds_sc_t_exact.v (id) VALUES (2)"):
            conn.execute(drift)
            assert typed.verify(conn, "ds_sc_t_exact", "v", want), drift
            generic.build(conn, "ds_sc_t_exact", EXACT, rows, [0, 1])  # back to the constructed state
            assert typed.verify(conn, "ds_sc_t_exact", "v", want) == []


@pytest.mark.parametrize("name, setup, rows, status", [
    ("nan", EXACT, [("v", {"id": TV("int", 1), "f": TV("float", float("nan"))})], "refused_constructor"),
    ("jsonb", "CREATE TABLE j (id int PRIMARY KEY, doc jsonb NOT NULL);",
     [("j", {"id": TV("int", 1), "doc": TV("jsonb", '{"n": 1}')})], "refused_constructor"),
    ("check", "CREATE TABLE c (id int PRIMARY KEY, qty int CHECK (qty > 0));",
     [("c", {"id": TV("int", 1), "qty": TV("int", 0)})], "refused_constructor"),
    ("type", EXACT, [("v", {"id": TV("bool", True)})], "refused_typed"),
])
def test_a_refusal_leaves_nothing_inserted(target, sandbox, name, setup, rows, status):
    r = generic.construct_typed(target, sandbox, setup_sql=setup, rows=rows, schema=f"ds_sc_t_refused_{name}")
    assert r.status == status, r.detail
    assert not schema_exists(target, f"ds_sc_t_refused_{name}")  # never built, so no row ever reached it


CHANCY_LIKE = "CREATE TABLE chancy_jobs ({});".format(", ".join(f'"{c}" {t}' for c, t in {
    **chancy.PINNED_COLUMNS, "id": "uuid PRIMARY KEY", "state": "character varying(25) NOT NULL DEFAULT 'pending'",
    "queue": "text NOT NULL", "func": "text NOT NULL", "kwargs": "jsonb NOT NULL DEFAULT '{}'",
    "created_at": "timestamptz NOT NULL DEFAULT now()", "scheduled_at": "timestamptz NOT NULL DEFAULT now()"}.items()))


@pytest.mark.parametrize("alter, why", [
    (None, None),
    ("CREATE FUNCTION f() RETURNS trigger AS $$ BEGIN RETURN NEW; END $$ LANGUAGE plpgsql; "
     "CREATE TRIGGER t BEFORE INSERT ON chancy_jobs FOR EACH ROW EXECUTE FUNCTION f();", "user triggers"),
    ("CREATE RULE r AS ON INSERT TO chancy_jobs DO ALSO NOTHING;", "rules"),
    ("ALTER TABLE chancy_jobs ALTER COLUMN kwargs TYPE json USING kwargs::json;", "pinned"),
    ("ALTER TABLE chancy_jobs ADD COLUMN extra int;", "pinned"),
])
def test_the_direct_constructors_scope_is_checked_on_the_live_catalog(target, alter, why):
    admin = target.admin()
    try:
        admin.execute("DROP SCHEMA IF EXISTS ds_sc_t_chancy CASCADE; CREATE SCHEMA ds_sc_t_chancy")
        admin.execute("SET search_path = ds_sc_t_chancy")
        admin.execute(CHANCY_LIKE)
        if alter:
            admin.execute(alter)
        problems = chancy.preconditions(admin, "ds_sc_t_chancy", chancy.rows(chancy.goals(
            {"jobs": [{"n": 1, "state": "pending"}]})))
        admin.execute("DROP SCHEMA ds_sc_t_chancy CASCADE")
    finally:
        admin.close()
    assert (problems == []) if why is None else any(why in p for p in problems), problems
