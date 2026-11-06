"""EXPERIMENTAL, live on the disposable MVP PostgreSQL: a str bound as jsonb, logged by the server normalized, now
corroborates exactly; a text parameter still compares verbatim."""

from __future__ import annotations

from pathlib import Path

import pytest

from deployspecter.experimental import harness as hz
from deployspecter.experimental import stepping as st
from deployspecter.experimental.apps import reservations_mixed as app

SECRET = Path.home() / ".ds_mvp_pg_password"
pytestmark = pytest.mark.skipif(not SECRET.exists(), reason="the disposable MVP PostgreSQL isn't running")
QJ, QT = "SELECT %s::jsonb IS NOT NULL", "SELECT %s::text IS NOT NULL"


def test_str_bound_as_jsonb_corroborates_exactly():
    target = hz.Target("127.0.0.1", 55441, "ds_mvp", SECRET.read_text().strip())

    def handler(conn):
        with conn.transaction():
            for v in ('{"b": 1, "a": 2}', '{"a": 1, "a": 2}', '{"n": 1.50e1, "m": 1e-3, "z": -0}'):
                conn.execute(QJ, [v])
            conn.execute(QT, ['{"b": 1, "a": 2}'])
    admin = target.admin()
    try:
        app.reset(admin, {"events": [{"id": 1, "capacity": 5, "reserved": 0}]})
        lim = st.Limits()
        actor = st.Actor("a", st.Handler("test", "j", handler, frozenset(st.normal_sql(s) for s in (QJ, QT)), {}), {})
        run = st.Run([actor], st.actor_connection_factory(target.conninfo(), lim, search_path=app.SCHEMA), admin,
                     schedule=[], limits=lim, run_id="jsonblive")
        _t, c = hz.corroborated(admin, run, user=target.user)
    finally:
        admin.close()
    assert c["ok"], c["problems"]
    assert c["params_compared"] == 4 and c["params_mismatched"] == 0 and c["params_unchecked"] == 0
