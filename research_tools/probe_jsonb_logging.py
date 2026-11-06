"""EXPERIMENTAL probe: how PostgreSQL 16 logs jsonb-typed bind parameters (disposable cluster only).

    python research_tools/probe_jsonb_logging.py OUT_DIR

It sends the same JSON values to jsonb and json columns as: a Python ``str`` (untyped, PostgreSQL infers the
type), a ``str`` with an explicit ``::jsonb`` cast, and psycopg's ``Jsonb``/``Json`` wrappers. It then copies the raw
csvlog rows of this session (``application_name`` ds_jsonb_probe) and the server settings that govern parameter
logging.
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Json, Jsonb


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    pw = (Path.home() / ".ds_mvp_pg_password").read_text().strip()
    VALUES = ['{"b": 1, "a": 2}', '{"a":1,"a":2}', '{"n": 1.0, "m": 1e2}', '[3, {"z": true, "y": null}]', '"text"']
    with psycopg.connect(host="127.0.0.1", port=55441, user="ds_mvp", password=pw, dbname="ds_mvp", autocommit=True,
                         application_name="ds_jsonb_probe") as c:
        assert c.execute("SELECT current_setting('cluster_name')").fetchone()[0] == "ds_mvp_disposable"
        settings = {k: c.execute(f"SELECT current_setting('{k}')").fetchone()[0] for k in (
            "server_version", "log_statement", "log_parameter_max_length", "log_parameter_max_length_on_error")}
        c.execute("DROP SCHEMA IF EXISTS ds_jsonb_probe CASCADE")
        c.execute("CREATE SCHEMA ds_jsonb_probe")
        c.execute("CREATE TABLE ds_jsonb_probe.t (k text, jb jsonb, js json)")
        sent = []
        for i, v in enumerate(VALUES):
            for how, sql, param in (("str_untyped", "INSERT INTO ds_jsonb_probe.t (k, jb) VALUES (%s, %s)", v),
                                    ("str_cast", "INSERT INTO ds_jsonb_probe.t (k, jb) VALUES (%s, %s::jsonb)", v),
                                    ("Jsonb", "INSERT INTO ds_jsonb_probe.t (k, jb) VALUES (%s, %s)",
                                     Jsonb(json.loads(v))),
                                    ("str_json_column", "INSERT INTO ds_jsonb_probe.t (k, js) VALUES (%s, %s)", v),
                                    ("Json_json_column", "INSERT INTO ds_jsonb_probe.t (k, js) VALUES (%s, %s)",
                                     Json(json.loads(v)))):
                key = f"v{i}:{how}"
                c.execute(sql, (key, param))
                sent.append({"key": key, "how": how, "sent_text": v if isinstance(param, str) else None,
                             "sent_obj": json.loads(v) if not isinstance(param, str) else None})
        stored = {r[0]: r[1] for r in c.execute("SELECT k, coalesce(jb::text, js::text) FROM ds_jsonb_probe.t")}
        c.execute("DROP SCHEMA ds_jsonb_probe CASCADE")
    log = subprocess.run(["docker", "exec", "deployspecter-mvp-pg", "cat", "/var/lib/postgresql/data/log/mvp.csv"],
                         capture_output=True, text=True, check=True).stdout
    rows = [r for r in csv.reader(io.StringIO(log)) if len(r) > 22 and r[22] == "ds_jsonb_probe"]
    (out / "raw_csvlog_rows.csv").write_text("".join(
        (lambda b: (csv.writer(b).writerow(r), b.getvalue())[1])(io.StringIO()) for r in rows))
    logged = {}
    for r in rows:
        msg, detail = r[13], r[14]
        if "INSERT INTO ds_jsonb_probe.t" in msg and detail.startswith("parameters:"):
            k = detail.split("$1 = '", 1)[1].split("'", 1)[0]
            logged[k] = detail.split("$2 = ", 1)[1]
    result = [s | {"logged_param": logged.get(s["key"]), "stored_text": stored.get(s["key"])} for s in sent]
    (out / "probe.json").write_text(json.dumps({"settings": settings, "results": result}, indent=1, ensure_ascii=False))
    for s in result:
        print(f"{s['key']:28s} sent={s['sent_text']!r:28} logged={s['logged_param']!s:34} stored={s['stored_text']}")
    print(settings)


if __name__ == "__main__":
    main()
