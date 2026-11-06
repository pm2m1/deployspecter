"""EXPERIMENTAL: capture PostgreSQL 16's jsonb output text (``%s::jsonb::text``) for a fixed vector list, or the
rejection, into tests/experimental/fixtures/pg16_jsonb_out.json. Disposable cluster only.

The output function is the one the server uses to log jsonb parameters (corroboration/jsonb_logging_probe_1)."""

from __future__ import annotations

import json
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parents[1]
VECTORS = [
    '{"b": 1, "a": 2}', '{"a":1,"a":2}', '{"a": {"y": 1, "x": [2, {"q": 0, "p": 1}]}}', '{"aa": 1, "b": 2}',
    '{"é": 1, "e": 2, "ee": 3}', '{"a": 1, "a": {"b": 2}, "a": [3]}', "[]", "{}", "[ ]", "{ }", " [1,2 , 3] ",
    "1", "-0", "0", "-0.0", "0.00", "1.0", "1.50", "1e2", "1E2", "1.50e1", "1.5e3", "1e-3", "1.5E-3", "-1e2",
    "12345678901234567890", "0.1", "1e20", "1e-20", "2.5e+2", "123.456e-2", '"text"', '"a\\u00e9b"', '"\\u0041"',
    '"tab\\tnl\\n"', '"q\\"b\\\\"', '"\\u001f"', '"\\/"', '"😀"', '"\\ud83d\\ude00"', "true", "false", "null",
    '[true, false, null, "x", 1.0]', '{"k": "v\\u0000"}', '"\\ud800"', "NaN", "Infinity", "-Infinity", '{"a": 1,}',
    "[1,]", "", "{", "'single'", '"raw\ncontrol"', "01", "1.", ".5", "+1", '{"a": 1} x']

def main() -> None:
    pw = (Path.home() / ".ds_mvp_pg_password").read_text().strip()
    out = []
    with psycopg.connect(host="127.0.0.1", port=55441, user="ds_mvp", password=pw, dbname="ds_mvp",
                         autocommit=True) as c:
        assert c.execute("SELECT current_setting('cluster_name')").fetchone()[0] == "ds_mvp_disposable"
        version = c.execute("SHOW server_version").fetchone()[0]
        for v in VECTORS:
            try:
                out.append({"input": v, "output": c.execute("SELECT %s::jsonb::text", (v,)).fetchone()[0]})
            except psycopg.Error as exc:
                out.append({"input": v, "output": None, "error": str(exc).splitlines()[0]})
    path = REPO / "tests/experimental/fixtures/pg16_jsonb_out.json"
    path.write_text(json.dumps({"server_version": version, "vectors": out}, indent=1, ensure_ascii=False) + "\n")
    print(len(out), "vectors;", sum(o["output"] is None for o in out), "rejected; server", version)


if __name__ == "__main__":
    main()
