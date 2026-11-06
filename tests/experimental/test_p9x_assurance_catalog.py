"""EXPERIMENTAL ds-p9x/1.1 assurance A2 (catalog-expression equivalence on parsed node trees) and the A1 live jsonb
round trips, on the disposable MVP PostgreSQL only (skipped unless it is running)."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from deployspecter.experimental import harness as hz
from deployspecter.experimental.phase9b_ext import catalog, expr_equiv, pipeline_v1
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


@pytest.fixture
def schemas(target):
    made = []

    def make(name: str, ddl: str) -> str:
        s = f"ds_p9x_a2_{name}"
        with psycopg.connect(**target.conninfo(), autocommit=True) as c:
            c.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
            c.execute(f"CREATE SCHEMA {s}")
            c.execute(f"SET search_path = {s}")
            c.execute(ddl)
        made.append(s)
        return s
    yield make
    with psycopg.connect(**target.conninfo(), autocommit=True) as c:
        for s in made:
            c.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")


CHANCY_LIKE = ("CREATE TABLE j (id int PRIMARY KEY, state varchar(25) NOT NULL, k text, q text); "
               "CREATE INDEX fetch_idx ON j (q, state) WHERE state IN ('pending', 'retrying'); "
               "CREATE UNIQUE INDEX uk ON j (k) WHERE k IS NOT NULL AND state NOT IN ('succeeded', 'failed');")


def test_chancys_two_partial_index_forms_are_established_on_parsed_trees(target, sandbox, schemas):
    s = schemas("chancy", CHANCY_LIKE)
    o = pipeline_v1.construct_on_target(target, sandbox, s, [("j", {"id": TV("int", 1), "state": TV("str", "pending"),
                                                                    "k": TV("str", "a"), "q": TV("str", "ds_q")})])
    assert o.status == "constructed", (o.status, o.detail)
    eq = o.export["expression_equivalence"]
    assert sorted(e["object"][1] for e in eq) == ["fetch_idx", "uk"]
    assert all(e["established"] and "R1" in e["rules"] for e in eq)


def establish_between(target, a: str, b: str, kind: str, table: str, name: str):
    with psycopg.connect(**target.conninfo(), autocommit=True) as c:
        return expr_equiv.establish(c, a, c, b, [{"kind": kind, "object": [table, name], "name": name}])[0]


@pytest.mark.parametrize("left,right,kind,name,want,why", [
    ("CREATE TABLE j (s varchar(9)); CREATE INDEX i ON j (s) WHERE s IN ('a', 'b')",
     "CREATE TABLE j (s varchar(9)); CREATE INDEX i ON j (s) WHERE s IN ('a', 'c')", "indexes", "i", False, "differ"),
    ("CREATE TABLE j (s varchar(9)); CREATE INDEX i ON j (s) WHERE s IN ('a', 'b')",
     "CREATE TABLE j (s varchar(9)); CREATE INDEX i ON j (s) WHERE ((s)::text = ANY "
     "(ARRAY[('a'::character varying)::text, ('b'::character varying)::text]))", "indexes", "i", True, None),
    ("CREATE TABLE j (s varchar(9)); CREATE INDEX i ON j (s) WHERE s IN ('a', 'b')",
     "CREATE TABLE j (s varchar(9)); CREATE UNIQUE INDEX i ON j (s) WHERE s IN ('a', 'b')", "indexes", "i", False,
     "non-expression"),
    ("CREATE TABLE j (n int, CONSTRAINT c CHECK (n > 0))", "CREATE TABLE j (n int, CONSTRAINT c CHECK (n > 1))",
     "constraints", "c", False, "differ"),
    ("CREATE TABLE j (n int, CONSTRAINT c CHECK (n > 0))", "CREATE TABLE j (n int, CONSTRAINT c CHECK (n >= 0))",
     "constraints", "c", False, "differ"),
    ("CREATE TABLE j (s varchar(9), CONSTRAINT c CHECK (s IN ('a', 'b')))",
     "CREATE TABLE j (s varchar(9), CONSTRAINT c CHECK (((s)::text = ANY (ARRAY[('a'::character varying)::text, "
     "('b'::character varying)::text]))))", "constraints", "c", True, None),
    ("CREATE TABLE j (s text DEFAULT 'x')", "CREATE TABLE j (s text DEFAULT 'y')", "columns", "s", False, "differ"),
    ("CREATE TABLE j (n int DEFAULT 1)", "CREATE TABLE j (n int DEFAULT 2 - 1)", "columns", "n", False, "differ"),
    ("CREATE TABLE j (s text DEFAULT 'x')", "CREATE TABLE j (s text DEFAULT 'x')", "columns", "s", True, None),
])
def test_equivalence_is_established_only_when_parsed_trees_agree(target, schemas, left, right, kind, name, want,
                                                                 why):
    a, b = schemas("left", left), schemas("right", right)
    table = "j"
    r = establish_between(target, a, b, kind, table, name)
    assert r["established"] is want, r
    if why:
        assert why in r["reason"]


def test_an_expression_over_a_schemas_own_type_is_refused(target, schemas):
    ddl = ("CREATE TYPE st AS ENUM ('a', 'b'); CREATE TABLE j (s st); "
           "CREATE INDEX i ON j (s) WHERE s = 'a'::st")
    a, b = schemas("enum_a", ddl), schemas("enum_b", ddl)
    r = establish_between(target, a, b, "indexes", "j", "i")
    assert r["established"] is False  # the two schemas' st types have different OIDs: not established, refused


def test_functions_and_triggers_stay_strict():
    fn = ["f", "", "plpgsql", "v", "abc", "f"]
    assert catalog.expression_only_differences({"functions": [fn]}, {"functions": [fn[:4] + ["abd", "f"]]}) is None
    trig = ["t", "tg", "CREATE TRIGGER tg AFTER INSERT ON <S>.t", 5, False, False, "f", "O"]
    other = trig[:2] + ["CREATE TRIGGER tg BEFORE INSERT ON <S>.t"] + trig[3:]
    assert catalog.expression_only_differences({"triggers": [trig]}, {"triggers": [other]}) is None


def test_unparsable_node_trees_are_never_equivalent():
    with pytest.raises(expr_equiv.NodeTreeError):
        expr_equiv.parse_node_tree("{VAR :varno 1")
    with pytest.raises(expr_equiv.NodeTreeError):
        expr_equiv.parse_node_tree("{VAR :varno 1} extra")


# ------------------------------------------------------------------------------------------------ A1, live

def test_adversarial_jsonb_values_round_trip_through_phase9b(target, sandbox, schemas):
    s = schemas("jsonb", "CREATE TABLE d (id int PRIMARY KEY, v jsonb)")
    vals = ['{"a": 1, "bb": [true, false, null], "ccc": {"d": {"e": []}}}', str(-(2**53 - 1)), str(2**53 - 1),
            '"1"', "1", "true", '"true"', '"{\\"a\\": 1}"', '{"a": 1}', "null", None, '"é\\u0001\\n"', "[[[]]]"]
    rows = [("d", {"id": TV("int", i), "v": TV("null", None) if v is None else TV("jsonb", v)})
            for i, v in enumerate(vals)]
    o = pipeline_v1.construct_on_target(target, sandbox, s, rows)
    assert o.status == "constructed", (o.status, o.detail)
    assert o.readback["jsonb_cells_checked"] == len(vals)
    with psycopg.connect(**target.conninfo(), autocommit=True) as c:
        got = dict(c.execute(f"SELECT id, CASE WHEN v IS NULL THEN 'SQL NULL' ELSE jsonb_typeof(v) || ':' || v::text "
                             f"END FROM {s}.d").fetchall())
    assert got[3] == 'string:"1"' and got[4] == "number:1" and got[5] == "boolean:true" and got[6] == 'string:"true"'
    assert got[7] == 'string:"{\\"a\\": 1}"' and got[8] == 'object:{"a": 1}' and got[9] == "null:null"
    assert got[10] == "SQL NULL"
