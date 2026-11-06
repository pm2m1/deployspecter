"""EXPERIMENTAL ds-p9x/1, live on the disposable MVP PostgreSQL only (skipped unless it is running).

Each test builds a trusted fixture schema ``ds_p9x_<name>`` in ``ds_mvp``, constructs a typed state through the
Phase 9B pipeline (extension instance, traced), and checks the outcome. It checks the target independently:
nothing is taken from the constructor's own labels.
"""

from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest

from deployspecter.experimental import harness as hz
from deployspecter.experimental.phase9b_ext import execute, pipeline_v1
from deployspecter.experimental.state_construction.typed import TV

SECRET = Path.home() / ".ds_mvp_pg_password"
PORT = 55441
pytestmark = pytest.mark.skipif(not SECRET.exists(), reason="the disposable MVP PostgreSQL isn't running")


@pytest.fixture(scope="module")
def target():
    return hz.Target("127.0.0.1", PORT, "ds_mvp", SECRET.read_text().strip())


@pytest.fixture(scope="module")
def sandbox(target):
    sb = pipeline_v1.open_sandbox(PORT, target.user, target.password)
    yield sb
    sb.close()
    assert pipeline_v1.teardown(PORT, target.user, target.password) == {"database_dropped": True,
                                                                        "role_dropped": True}


def make(target, name: str, ddl: str) -> str:
    schema = f"ds_p9x_{name}"
    a = target.admin()
    try:
        a.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        a.execute(f"CREATE SCHEMA {schema}")
        a.execute(f"SET search_path = {schema}")
        a.execute(ddl)
    finally:
        a.close()
    return schema


def drop(target, schema: str) -> None:
    a = target.admin()
    try:
        a.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    finally:
        a.close()


def rows_of(target, schema: str, table: str) -> list[dict]:
    with psycopg.connect(**target.conninfo(), autocommit=True) as c:
        return [r[0] for r in c.execute(f"SELECT to_jsonb(t) FROM {schema}.{table} t").fetchall()]


@pytest.fixture
def cleanup(target):
    made = []
    yield made
    for s in made:
        drop(target, s)


JSONB_DDL = "CREATE TABLE docs (id int PRIMARY KEY, d jsonb, note text)"


def test_jsonb_values_keep_their_semantics_through_phase9b(target, sandbox, cleanup):
    schema = make(target, "jsonb", JSONB_DDL)
    cleanup.append(schema)
    values = {1: TV("jsonb", "null"), 2: TV("null", None),
              3: TV("jsonb", '{"a": [true, false, null], "bb": "é\\n"}'),
              4: TV("jsonb", str(2**53 - 1)), 5: TV("jsonb", "[]"), 6: TV("jsonb", '"text"'), 7: TV("jsonb", "{}")}
    rows = [("docs", {"id": TV("int", k), "d": v, "note": TV("str", f"n{k}")}) for k, v in values.items()]
    o = pipeline_v1.construct_on_target(target, sandbox, schema, rows)
    assert o.status == "constructed", o.detail
    assert o.classification == "ORIGINAL_VALID" and o.readback["jsonb_cells_checked"] == 7
    assert o.trace["required"]["deployspecter.experimental.phase9b_ext.loader.same_v1"] > 0
    with psycopg.connect(**target.conninfo(), autocommit=True) as c:
        got = dict(c.execute(f"SELECT id, CASE WHEN d IS NULL THEN 'SQL NULL' ELSE jsonb_typeof(d) END "
                             f"FROM {schema}.docs").fetchall())
    assert got == {1: "null", 2: "SQL NULL", 3: "object", 4: "number", 5: "array", 6: "string", 7: "object"}


@pytest.mark.parametrize("text,why", [
    ('{"a": 1,', "malformed"), ('{"a": 1, "a": 2}', "duplicate"), ("NaN", "not JSON"), ("[Infinity]", "not JSON"),
    ('{"x": 1.5}', "non-integer"), ('{"x": 1e2}', "non-integer"), (str(2**53), "exceeds"),
    ('"a\\u0000b"', "u0000")])
def test_jsonb_outside_the_contract_is_refused_before_construction(target, sandbox, cleanup, text, why):
    schema = make(target, "jsonb_bad", JSONB_DDL)
    cleanup.append(schema)
    rows = [("docs", {"id": TV("int", 1), "d": TV("jsonb", text)})]
    o = pipeline_v1.construct_on_target(target, sandbox, schema, rows)
    assert o.status == "refused_types" and why in json.dumps(o.detail), o.detail
    assert o.report is None and rows_of(target, schema, "docs") == []


def test_a_non_canonical_or_ambiguous_jsonb_value_is_refused(target, sandbox, cleanup):
    schema = make(target, "jsonb_amb", JSONB_DDL)
    cleanup.append(schema)
    o = pipeline_v1.construct_on_target(target, sandbox, schema, [("docs", {"id": TV("int", 1),
                                                                           "d": TV("jsonb", '{"b":1,"a":2}')})])
    assert o.status == "refused_types" and "canonical" in json.dumps(o.detail)
    o = pipeline_v1.construct_on_target(target, sandbox, schema, [("docs", {"id": TV("int", 1), "d": TV("jsonb", "{}"),
                                                                           "note": TV("str", "{}")})])
    assert o.status == "refused_types" and "ambiguous" in json.dumps(o.detail)
    assert rows_of(target, schema, "docs") == []


AUDIT_DDL = """
CREATE TABLE items (id int PRIMARY KEY, label text NOT NULL);
CREATE TABLE audit (item int NOT NULL, label text NOT NULL);
CREATE FUNCTION audit_item() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN INSERT INTO audit (item, label) VALUES (NEW.id, NEW.label); RETURN NEW; END $$;
CREATE TRIGGER items_audit AFTER INSERT ON items FOR EACH ROW EXECUTE FUNCTION audit_item();
"""


def test_trigger_generated_rows_are_part_of_the_verified_state(target, sandbox, cleanup):
    schema = make(target, "audit", AUDIT_DDL)
    cleanup.append(schema)
    rows = [("items", {"id": TV("int", i), "label": TV("str", f"L{i}")}) for i in (1, 2)]
    o = pipeline_v1.construct_on_target(target, sandbox, schema, rows)
    assert o.status == "constructed", o.detail
    assert o.triggers["controlled"]["items"][0]["trigger"] == "items_audit"
    assert sorted(o.write["added"]) == ["audit", "items"]
    assert sorted((r["item"], r["label"]) for r in rows_of(target, schema, "audit")) == [(1, "L1"), (2, "L2")]
    assert len(o.plan["sha256"]) == 64 and o.plan["catalog_fingerprint_sha256"] == o.fingerprint_sha256


REORDER_DDL = """
CREATE TABLE settings (k text PRIMARY KEY, v text NOT NULL);
CREATE TABLE writes (k text NOT NULL, v text NOT NULL);
CREATE FUNCTION last_writer() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN INSERT INTO settings (k, v) VALUES (NEW.k, NEW.v)
  ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v; RETURN NEW; END $$;
CREATE TRIGGER writes_last AFTER INSERT ON writes FOR EACH ROW EXECUTE FUNCTION last_writer();
"""


def test_a_physical_order_change_that_preserves_the_requested_state(target, sandbox, cleanup):
    schema = make(target, "reorder", REORDER_DDL)
    cleanup.append(schema)
    rows = [("writes", {"k": TV("str", "x"), "v": TV("str", "late")}),
            ("writes", {"k": TV("str", "x"), "v": TV("str", "early")}),
            ("settings", {"k": TV("str", "x"), "v": TV("str", "late")})]
    o = pipeline_v1.construct_on_target(target, sandbox, schema, rows)
    assert o.status == "constructed", (o.status, o.detail, o.classification)
    assert o.classification == "CONSTRUCTED_EQUIVALENT" and [x["row"] for x in o.plan["inserts"]] == [1, 0]
    assert rows_of(target, schema, "settings") == [{"k": "x", "v": "late"}]


def test_a_trigger_that_would_change_the_requested_value_is_refused_not_obeyed(target, sandbox, cleanup):
    schema = make(target, "override", """
CREATE TABLE names (id int PRIMARY KEY, name text NOT NULL);
CREATE FUNCTION up() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.name := upper(NEW.name); RETURN NEW; END $$;
CREATE TRIGGER names_up BEFORE INSERT ON names FOR EACH ROW EXECUTE FUNCTION up();""")
    cleanup.append(schema)
    o = pipeline_v1.construct_on_target(target, sandbox, schema, [("names", {"id": TV("int", 1),
                                                                            "name": TV("str", "lower")})])
    assert o.status == "refused_constructor" and o.classification not in ("ORIGINAL_VALID", "CONSTRUCTED_EQUIVALENT")
    assert rows_of(target, schema, "names") == []


@pytest.mark.parametrize("body,why", [
    ("BEGIN EXECUTE 'INSERT INTO log VALUES (1)'; RETURN NEW; END", "dynamic SQL"),
    ("BEGIN INSERT INTO log VALUES (extract(epoch FROM clock_timestamp())::int); RETURN NEW; END", "clock_timestamp"),
    ("BEGIN INSERT INTO log VALUES (current_setting('ds.x')::int); RETURN NEW; END", "current_setting")])
def test_unprovable_procedural_triggers_are_refused(target, sandbox, cleanup, body, why):
    schema = make(target, "proc", f"""
CREATE TABLE t (id int PRIMARY KEY); CREATE TABLE log (n int);
CREATE FUNCTION f() RETURNS trigger LANGUAGE plpgsql AS $$ {body} $$;
CREATE TRIGGER t_f AFTER INSERT ON t FOR EACH ROW EXECUTE FUNCTION f();""")
    cleanup.append(schema)
    o = pipeline_v1.construct_on_target(target, sandbox, schema, [("t", {"id": TV("int", 1)})])
    assert o.status == "refused_triggers" and why in json.dumps(o.detail), o.detail
    assert rows_of(target, schema, "t") == []


def test_a_statement_level_trigger_is_refused(target, sandbox, cleanup):
    schema = make(target, "stmt", """
CREATE TABLE t (id int PRIMARY KEY); CREATE TABLE log (n int);
CREATE FUNCTION f() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN INSERT INTO log VALUES (1); RETURN NULL; END $$;
CREATE TRIGGER t_f AFTER INSERT ON t FOR EACH STATEMENT EXECUTE FUNCTION f();""")
    cleanup.append(schema)
    o = pipeline_v1.construct_on_target(target, sandbox, schema, [("t", {"id": TV("int", 1)})])
    assert o.status == "refused_triggers" and "statement-level" in json.dumps(o.detail)


def test_an_unexpected_trigger_side_effect_on_the_target_is_caught_and_rolled_back(target, sandbox, cleanup):
    # the trigger's effect depends on target data the sandbox doesn't have (a pre-existing row), so the target's
    # change differs from the constructor's proven final state
    schema = make(target, "sidefx", """
CREATE TABLE cfg (k int PRIMARY KEY); CREATE TABLE t (id int PRIMARY KEY); CREATE TABLE log (n bigint NOT NULL);
CREATE FUNCTION f() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN INSERT INTO log SELECT count(*) FROM cfg; RETURN NEW; END $$;
CREATE TRIGGER t_f AFTER INSERT ON t FOR EACH ROW EXECUTE FUNCTION f();
INSERT INTO cfg VALUES (1);""")
    cleanup.append(schema)
    o = pipeline_v1.construct_on_target(target, sandbox, schema, [("t", {"id": TV("int", 1)})])
    assert o.status == "refused_execution" and "proven final state" in o.detail, (o.status, o.detail)
    assert rows_of(target, schema, "t") == [] and rows_of(target, schema, "log") == []


def test_a_catalog_change_between_planning_and_execution_is_refused(target, sandbox, cleanup):
    schema = make(target, "drift", "CREATE TABLE t (id int PRIMARY KEY, v text)")
    cleanup.append(schema)

    def drift():
        a = target.admin()
        try:
            a.execute(f"ALTER TABLE {schema}.t ADD COLUMN extra int DEFAULT 7")
        finally:
            a.close()
    o = pipeline_v1.construct_on_target(target, sandbox, schema, [("t", {"id": TV("int", 1), "v": TV("str", "a")})],
                                        before_write=drift)
    assert o.status == "refused_execution" and "catalog changed" in o.detail
    assert rows_of(target, schema, "t") == []


def test_a_trigger_catalog_mismatch_between_target_and_sandbox_is_refused(target, sandbox, cleanup):
    # a disabled trigger is exported as enabled (CREATE TRIGGER can't carry tgenabled), so the sandbox's catalog
    # differs from the target's
    schema = make(target, "tgmis", AUDIT_DDL + "ALTER TABLE items DISABLE TRIGGER items_audit;")
    cleanup.append(schema)
    o = pipeline_v1.construct_on_target(target, sandbox, schema, [("items", {"id": TV("int", 1),
                                                                            "label": TV("str", "a")})])
    assert o.status == "refused_catalog" and o.stage == "sandbox_fingerprint", (o.status, o.detail)
    assert rows_of(target, schema, "items") == []


def test_inconsistent_foreign_keys_are_refused(target, sandbox, cleanup):
    schema = make(target, "fk", "CREATE TABLE p (id int PRIMARY KEY); "
                                "CREATE TABLE c (id int PRIMARY KEY, p int NOT NULL REFERENCES p (id));")
    cleanup.append(schema)
    o = pipeline_v1.construct_on_target(target, sandbox, schema, [("c", {"id": TV("int", 1), "p": TV("int", 99)})])
    assert o.status == "refused_constructor" and o.classification == "IMPOSSIBLE_WITH_SUPPORTED_OPERATIONS"
    assert rows_of(target, schema, "c") == []


def test_an_interrupted_write_leaves_nothing(target, sandbox, cleanup):
    schema = make(target, "interrupt", AUDIT_DDL)
    cleanup.append(schema)

    def boom(k):
        if k == 0:
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        pipeline_v1.construct_on_target(target, sandbox, schema,
                                        [("items", {"id": TV("int", i), "label": TV("str", "x")}) for i in (1, 2)],
                                        on_insert=boom)
    assert rows_of(target, schema, "items") == [] and rows_of(target, schema, "audit") == []


def test_a_tampered_construction_digest_is_refused_and_rolled_back(target, sandbox, cleanup):
    schema = make(target, "digest", AUDIT_DDL)
    cleanup.append(schema)
    rows = [("items", {"id": TV("int", 1), "label": TV("str", "x")})]
    o = pipeline_v1.construct_on_target(target, sandbox, schema, rows)
    assert o.status == "constructed"
    drop(target, schema)
    schema = make(target, "digest", AUDIT_DDL)
    final = json.loads(json.dumps(o.report["final_snapshot"]))
    final["audit"][0]["label"] = "tampered"
    with pytest.raises(execute.ExecutionRefused, match="proven final state"):
        execute.write_and_verify(target.conninfo(), schema, rows, [0], final, o.fingerprint_sha256)
    assert rows_of(target, schema, "items") == []


def test_a_partial_index_whose_deparse_is_not_a_fixpoint_is_accepted_narrowly(target, sandbox, cleanup):
    # Chancy 0.26.0's own form: a varchar array compared through text[]
    schema = make(target, "partial", "CREATE TABLE j (id int PRIMARY KEY, state varchar(25) NOT NULL, k text); "
                                     "CREATE UNIQUE INDEX j_k ON j (k) WHERE k IS NOT NULL AND "
                                     "state NOT IN ('done', 'failed');")
    cleanup.append(schema)
    rows = [("j", {"id": TV("int", 1), "state": TV("str", "pending"), "k": TV("str", "a")})]
    o = pipeline_v1.construct_on_target(target, sandbox, schema, rows)
    assert o.status == "constructed", (o.status, o.detail)
    assert o.export["verified_by"].startswith("expression-only") and o.export["expression_differences"][0]["kind"] \
        == "indexes"


def test_an_identity_column_is_exported_and_constructed(target, sandbox, cleanup):
    schema = make(target, "ident",
                  "CREATE TABLE w (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, n int NOT NULL)")
    cleanup.append(schema)
    o = pipeline_v1.construct_on_target(target, sandbox, schema, [("w", {"n": TV("int", 5)})])
    assert o.status == "constructed", (o.status, o.detail)
    assert rows_of(target, schema, "w") == [{"id": 1, "n": 5}]


def test_a_trigger_writing_a_table_with_a_clock_default_is_refused(target, sandbox, cleanup):
    # procrastinate's shape: a status trigger inserts an event whose timestamp column defaults to now()
    schema = make(target, "clockdef", """
CREATE TABLE jobs (id int PRIMARY KEY, status text NOT NULL);
CREATE TABLE events (job int NOT NULL, type text NOT NULL, at timestamptz DEFAULT now());
CREATE FUNCTION ev() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN INSERT INTO events (job, type) VALUES (NEW.id, NEW.status); RETURN NEW; END $$;
CREATE TRIGGER jobs_ev AFTER INSERT ON jobs FOR EACH ROW EXECUTE FUNCTION ev();""")
    cleanup.append(schema)
    o = pipeline_v1.construct_on_target(target, sandbox, schema, [("jobs", {"id": TV("int", 1),
                                                                           "status": TV("str", "todo")})])
    assert o.status == "refused_triggers" and "events" in json.dumps(o.detail) and "now()" in json.dumps(o.detail)
    assert rows_of(target, schema, "jobs") == [] and rows_of(target, schema, "events") == []


def test_an_interruption_during_sandbox_construction_leaves_the_target_untouched(target, sandbox, cleanup,
                                                                                  monkeypatch):
    from deployspecter.experimental.phase9b_ext import loader
    schema = make(target, "sbint", AUDIT_DDL)
    cleanup.append(schema)
    real = loader.construct

    def interrupted(*a, **k):
        real(*a, **k)
        raise KeyboardInterrupt  # after the sandbox work, before anything reaches the target
    monkeypatch.setattr(pipeline_v1.loader, "construct", interrupted)
    with pytest.raises(KeyboardInterrupt):
        pipeline_v1.construct_on_target(target, sandbox, schema, [("items", {"id": TV("int", 1),
                                                                             "label": TV("str", "x")})])
    assert rows_of(target, schema, "items") == [] and rows_of(target, schema, "audit") == []
    # the module's sandbox fixture still tears the constructor's database and role down (asserted at teardown)
