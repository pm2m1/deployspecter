"""EXPERIMENTAL probe: does server-log corroboration accept a ``str`` bound as jsonb (disposable cluster only)?

    python research_tools/probe_jsonb_corroboration.py OUT_DIR LABEL

One trusted handler binds JSON text to jsonb in five forms:
* key order the server rewrites;
* a duplicate key;
* exponent and decimal numbers;
* an already-canonical value;
* text to a text-typed parameter (must stay exact).
The run is corroborated with the standard harness. OUT_DIR/LABEL gets the corroboration result, the trace's
parameter records and the raw csvlog rows of the window.
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from deployspecter.experimental import harness as hz  # noqa: E402
from deployspecter.experimental import stepping as st  # noqa: E402
from deployspecter.experimental.apps import reservations_mixed as app  # noqa: E402

Q_JSONB = "SELECT %s::jsonb IS NOT NULL"
Q_TEXT = "SELECT %s::text IS NOT NULL"
VALUES = ['{"b": 1, "a": 2}', '{"a": 1, "a": 2}', '{"n": 1.50e1, "m": 1e-3, "z": -0}', '{"a": 2, "b": 1}']


def handler(conn):
    with conn.transaction():
        for v in VALUES:
            conn.execute(Q_JSONB, [v])
        conn.execute(Q_TEXT, ['{"b": 1, "a": 2}'])


def main() -> int:
    out = Path(sys.argv[1]) / sys.argv[2]
    out.mkdir(parents=True, exist_ok=False)
    pw = (Path.home() / ".ds_mvp_pg_password").read_text().strip()
    target = hz.Target("127.0.0.1", 55441, "ds_mvp", pw)
    admin = target.admin()
    try:
        assert admin.execute("SELECT current_setting('cluster_name')").fetchone()[0] == "ds_mvp_disposable"
        app.reset(admin, {"events": [{"id": 1, "capacity": 5, "reserved": 0}]})
        lim = st.Limits()
        actor = st.Actor("a", st.Handler("test", "jsonb_probe", handler,
                                         frozenset(st.normal_sql(s) for s in (Q_JSONB, Q_TEXT)), {}), {})
        connect = st.actor_connection_factory(target.conninfo(), lim, search_path=app.SCHEMA)
        run = st.Run([actor], connect, admin, schedule=[], limits=lim, run_id="jsonbprobe")
        trace, corr = hz.corroborated(admin, run, user=target.user)
    finally:
        admin.close()
    log = subprocess.run(["docker", "exec", "deployspecter-mvp-pg", "cat", "/var/lib/postgresql/data/log/mvp.csv"],
                         capture_output=True, text=True, check=True).stdout
    rows = [r for r in csv.reader(io.StringIO(log)) if len(r) > 14 and ("::jsonb IS NOT NULL" in r[13] or
                                                                         "::text IS NOT NULL" in r[13])][-5:]
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    (out / "raw_csvlog_rows.csv").write_text(buf.getvalue())
    params = [{"sql": o.sql, "params": o.params} for o in trace.records if getattr(o, "params", None)]
    summary = {k: corr.get(k) for k in ("ok", "matched", "params_compared", "params_mismatched", "params_unchecked",
                                        "problems")}
    (out / "corroboration.json").write_text(json.dumps({"summary": summary, "trace_params": params}, indent=1,
                                                       default=str))
    print(json.dumps(summary, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
