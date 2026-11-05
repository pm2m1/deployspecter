"""EXPERIMENTAL, live: Django 5.2's ORM on psycopg 3 through the trusted stepped backend, on a disposable PostgreSQL.

Skipped unless the disposable MVP PostgreSQL is running and Django is installed. A trusted developer-written fixture,
not part of any blind corpus.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("django")

from deployspecter.experimental import harness as hz  # noqa: E402
from deployspecter.experimental import stepping as st  # noqa: E402
from deployspecter.experimental.apps import django_fixture as D  # noqa: E402

SECRET = Path(os.environ.get("DS_MVP_SECRET_FILE", os.path.expanduser("~/.ds_mvp_pg_password")))
PORT = int(os.environ.get("DS_MVP_PORT", "55441"))
pytestmark = pytest.mark.skipif(not SECRET.exists(), reason="the disposable MVP PostgreSQL isn't running")

SEED = {"accounts": [{"id": 1, "owner": "alice", "opening": 100, "balance": 100},
                     {"id": 2, "owner": "bob", "opening": 0, "balance": 0}]}
TWO_WITHDRAWALS = [{"actor": "a", "app_version": "v1", "handler": "withdraw", "args": {"account_id": 1, "amount": 70}},
                   {"actor": "b", "app_version": "v1", "handler": "withdraw", "args": {"account_id": 1, "amount": 60}}]
INTERLEAVE = ["a", "b"] * 6


@pytest.fixture(scope="module")
def target():
    t = hz.Target("127.0.0.1", PORT, "ds_mvp", SECRET.read_text().strip())
    D.configure(t)
    trace, c = D.migrate_through_stepping(t)  # the schema comes from the app's own migration, through the gate
    assert trace.actors["migrate"]["exception"] is None and c["ok"], c["problems"]
    return t


@pytest.fixture(autouse=True)
def fresh_schema(target):
    """Tests in other files may drop experimental schemas; re-apply the app's migration if the tables are gone."""
    admin = target.admin()
    try:
        present = admin.execute("SELECT to_regclass('mvp_django.ledger_account') IS NOT NULL").fetchone()[0]
    finally:
        admin.close()
    if not present:
        _trace, c = D.migrate_through_stepping(target)
        assert c["ok"], c["problems"]


def run(target, variant, binding, schedule=INTERLEAVE, invocations=TWO_WITHDRAWALS, **kw):
    return hz.run_app(target, D, invocations, SEED, variant=variant, schedule=schedule, label="django",
                      registry_kw={"binding": binding}, **kw)


class TestDjangoOrm:
    @pytest.mark.parametrize("binding", ["server", "client"])
    def test_buggy_loses_an_update_and_the_corrected_build_holds_both_corroborated(self, target, binding):
        buggy = run(target, "buggy", binding, corroborate=True)
        fixed = run(target, "fixed", binding, corroborate=True)
        assert buggy.initial_digest == fixed.initial_digest
        assert buggy.violations == ["L1 account 1: balance 40 but opening 100 plus entries -130"]
        assert fixed.violations == []
        for r in (buggy, fixed):
            c = r.corroboration
            assert c["ok"], c["problems"]
            assert c["params_mismatched"] == 0 and c["params_unchecked"] == 0
            if binding == "client":  # values reach the server inside the text: compared as the exact text
                assert c["client_texts_matched"] > 0 and c["params_compared"] == 0
            else:
                assert c["params_compared"] > 0
        sites = {op.provenance["call_site"].split(":")[0] for op in buggy.trace.records
                 if op.decision is not None and buggy.trace.decisions[op.decision].source != "setup"}
        assert sites == {"src/deployspecter/experimental/apps/django_ledger/handlers.py"}

    @pytest.mark.parametrize("binding", ["server", "client"])
    def test_bounded_exploration_buggy_violates_corrected_never_does(self, target, binding):
        counts = {}
        for variant in ("buggy", "fixed"):
            results, exhausted = hz.explore_app(target, D, TWO_WITHDRAWALS, SEED, variant=variant, max_runs=200,
                                                label="django", registry_kw={"binding": binding})
            assert exhausted
            counts[variant] = (len(results), sum(bool(r.violations) for r in results))
        assert counts == {"buggy": (24, 22), "fixed": (8, 0)}

    def test_the_orm_write_is_computed_from_what_this_schedule_read(self, target):
        r = run(target, "buggy", "server")
        updates = {op.actor: op.params for op in r.trace.records if op.sql.startswith('UPDATE "ledger_account"')}
        assert updates == {"a": [30, 1], "b": [40, 1]}  # both from balance 100
        serial = run(target, "buggy", "server", schedule=["a"] * 6 + ["b"] * 6)
        assert serial.violations == [] and serial.trace.raw_results["b"] == {"status": "insufficient"}

    @pytest.mark.parametrize("binding", ["server", "client"])
    def test_nested_atomic_savepoint_releases_and_rolls_back(self, target, binding):
        ok = [{"actor": "a", "app_version": "v1", "handler": "transfer", "args": {"src": 1, "dst": 2, "amount": 30}}]
        over = [{"actor": "a", "app_version": "v1", "handler": "transfer", "args": {"src": 2, "dst": 1, "amount": 5}}]
        r1 = run(target, "fixed", binding, schedule=["a"] * 12, invocations=ok, corroborate=True)
        r2 = run(target, "fixed", binding, schedule=["a"] * 12, invocations=over, corroborate=True)
        assert r1.trace.raw_results["a"] == {"status": "ok"} and r1.violations == []
        assert r2.trace.raw_results["a"] == {"status": "insufficient"} and r2.violations == []
        assert r2.final_state["accounts"] == [{"id": 1, "owner": "alice", "opening": 100, "balance": 100},
                                              {"id": 2, "owner": "bob", "opening": 0, "balance": 0}]
        kinds = [" ".join(op.sql.split(" ")[:-1]) for op in r2.trace.records if "SAVEPOINT" in op.sql]
        assert kinds == ["SAVEPOINT", "ROLLBACK TO SAVEPOINT", "RELEASE SAVEPOINT"]  # Django's atomic on an error
        for r in (r1, r2):
            assert r.corroboration["ok"], r.corroboration["problems"]

    def test_queryset_update_with_f_is_safe_against_concurrent_deposits(self, target):
        invs = [{"actor": n, "app_version": "v1", "handler": "deposit", "args": {"account_id": 2, "amount": q}}
                for n, q in (("a", 5), ("b", 7))]
        results, exhausted = hz.explore_app(target, D, invs, SEED, variant="buggy", max_runs=100, label="django",
                                            registry_kw={"binding": "server"})
        assert exhausted and not any(r.violations for r in results)
        assert {r.final_state["accounts"][1]["balance"] for r in results} == {12}


def run_custom(target, fn, allowed, *, binding="server", corroborate=True):
    """A custom Django handler (ORM code given the alias), stepped on the fixture's schema."""
    admin = target.admin()
    try:
        D.reset(admin, SEED)
        lim = st.Limits()

        def handler(conn):
            from django.db import connections

            from deployspecter.experimental.django_backend.base import handover
            handover(conn)
            try:
                return fn(binding)
            finally:
                connections[binding].close()
        handler.__name__ = fn.__name__
        allowed_sql = frozenset(st.normal_sql(s) for s in allowed)
        actor = st.Actor("a", D.DjangoHandler("test", fn.__name__, handler, allowed_sql, {}, setup_sql=D.SETUP), {})
        connect = st.actor_connection_factory(target.conninfo(), lim, search_path=D.SCHEMA, autocommit=True,
                                              **D.connection_options(binding=binding))
        run_ = st.Run([actor], connect, admin, limits=lim, run_id="django_custom")
        if corroborate:
            return hz.corroborated(admin, run_, user=target.user)
        return run_.execute(), None
    finally:
        admin.close()


def accounts(alias):
    from deployspecter.experimental.apps.django_ledger.models import Account
    return Account.objects.using(alias)


class TestDjangoBoundary:
    @pytest.mark.parametrize("binding", ["server", "client"])
    def test_queryset_iterator_needs_a_server_side_cursor_and_is_refused(self, target, binding):
        trace, c = run_custom(target, lambda a: [x.pk for x in accounts(a).iterator()], [], binding=binding)
        assert trace.actors["a"]["exception_class"] == "refused"
        assert "named" in trace.actors["a"]["exception"]
        assert c["ok"], c["problems"]  # nothing reached the server: no DECLARE ... CURSOR either

    def test_with_server_side_cursors_disabled_the_iterator_runs_on_a_gated_cursor(self, target):
        from django.db import connections
        settings_dict = connections.settings["server"]
        settings_dict["DISABLE_SERVER_SIDE_CURSORS"] = True
        try:
            trace, c = run_custom(target, lambda a: [x.pk for x in accounts(a).iterator()],
                                  [D._ACC + ' ORDER BY "ledger_account"."id" ASC', D._ACC])
        finally:
            settings_dict["DISABLE_SERVER_SIDE_CURSORS"] = False
        assert trace.raw_results["a"] == [1, 2] and c["ok"], c["problems"]

    def test_a_reopened_connection_is_refused(self, target):
        def reopen(a):
            from django.db import connections
            accounts(a).get(pk=1)
            connections[a].close()
            return accounts(a).get(pk=2)  # Django reconnects: a second connection this thread wasn't given
        trace, c = run_custom(target, reopen, [D.GET])
        assert trace.actors["a"]["exception_class"] == "refused"
        assert "wasn't given" in trace.actors["a"]["exception"] and c["ok"], c["problems"]

    def test_a_persistent_connection_serves_two_requests_on_one_session(self, target):
        from django.db import connections
        settings_dict = connections.settings["server"]
        settings_dict["CONN_MAX_AGE"] = None

        def two_requests(a):
            first = accounts(a).get(pk=1).balance
            connections[a].close_if_unusable_or_obsolete()  # what Django does at a request's end
            return [first, accounts(a).get(pk=2).balance]
        try:
            trace, c = run_custom(target, two_requests, [D.GET])
        finally:
            settings_dict["CONN_MAX_AGE"] = 0
        assert trace.raw_results["a"] == [100, 0] and c["ok"], c["problems"]

    def test_pooling_is_refused(self, target):
        from django.db import connections
        options = connections.settings["server"]["OPTIONS"]
        options["pool"] = True
        try:
            trace, c = run_custom(target, lambda a: accounts(a).get(pk=1).balance, [D.GET])
        finally:
            options.pop("pool")
        assert trace.actors["a"]["exception_class"] == "refused" and "pooling" in trace.actors["a"]["exception"]

    def test_a_settings_target_that_isnt_the_actors_connection_is_refused(self, target):
        from django.db import connections
        settings_dict = connections.settings["server"]
        settings_dict["PORT"] = "55432"  # the protected database's port: never connected to, refused before use
        try:
            trace, _c = run_custom(target, lambda a: accounts(a).get(pk=1).balance, [D.GET], corroborate=False)
        finally:
            settings_dict["PORT"] = str(PORT)
        assert trace.actors["a"]["exception_class"] == "refused"
        assert "isn't the actor's connection" in trace.actors["a"]["exception"]

    def test_raw_cursor_sql_is_gated_unlisted_sql_is_refused_and_a_pgconn_bypass_is_caught(self, target):
        def raw_ok(a):
            from django.db import connections
            with connections[a].cursor() as cur:
                cur.execute("SELECT count(*) FROM ledger_account")
                return cur.fetchone()[0]

        def raw_unlisted(a):
            from django.db import connections
            with connections[a].cursor() as cur:
                cur.execute("DELETE FROM ledger_entry")

        def raw_bypass(a):
            from django.db import connections
            accounts(a).get(pk=1)
            connections[a].connection.pgconn.exec_(b"SELECT 1")
            return "bypassed"

        trace, c = run_custom(target, raw_ok, ["SELECT count(*) FROM ledger_account"])
        assert trace.raw_results["a"] == 2 and c["ok"], c["problems"]
        trace, c = run_custom(target, raw_unlisted, [])
        assert trace.actors["a"]["exception_class"] == "refused" and c["ok"], c["problems"]
        trace, c = run_custom(target, raw_bypass, [D.GET])
        assert trace.raw_results["a"] == "bypassed" and not c["ok"]
        assert any("doesn't record" in p for p in c["problems"])
