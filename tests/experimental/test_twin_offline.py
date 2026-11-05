"""EXPERIMENTAL, offline: the procrastinate digital twin's model (no database, no containers)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from deployspecter.experimental.twin import procrastinate_twin as tw

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research_tools"))
import run_twin_procrastinate as rt  # noqa: E402

BASE = {"functions": [["procrastinate_retry_job_v1", "job_id bigint", "void", "f", "aaaa", "bbbb"],
                      [tw.TRIGGER_FN, "", "trigger", "f", "cccc", "dddd"]],
        "enums": [["procrastinate_job_event_type", "deferred,started,scheduled,cancelled"]],
        "columns": [["procrastinate_jobs", "id", "bigint", "true", ""]]}


def cat(**over):
    c = {k: [list(r) for r in v] for k, v in BASE.items()}
    c.update(over)
    return c


def test_pre_migration_shape_is_additive_and_a_removal_is_not():
    after = cat(functions=BASE["functions"] + [["procrastinate_retry_job_v2", "job_id bigint, ...", "void", "f",
                                                "eeee", "ffff"]],
                enums=[["procrastinate_job_event_type", "deferred,started,scheduled,retried,cancelled"]])
    d = tw.catalog_diff(BASE, after)
    assert set(d) == {"functions", "enums"} and tw.is_additive(d)
    removed = cat(columns=[])
    assert not tw.is_additive(tw.catalog_diff(BASE, removed))
    relabelled = cat(enums=[["procrastinate_job_event_type", "deferred,scheduled,started,cancelled"]])
    assert not tw.is_additive(tw.catalog_diff(BASE, relabelled))  # reordering isn't additive


def test_function_change_and_whitespace_only_change_are_told_apart():
    body = cat(functions=[BASE["functions"][0], [tw.TRIGGER_FN, "", "trigger", "f", "9999", "8888"]])
    d = tw.catalog_diff(BASE, body)["functions"]
    assert d["changed"] == [f"{tw.TRIGGER_FN}()"] and d["whitespace_only"] == []
    ws = cat(functions=[BASE["functions"][0], [tw.TRIGGER_FN, "", "trigger", "f", "9999", "dddd"]])
    assert tw.catalog_diff(BASE, ws)["functions"]["whitespace_only"] == [f"{tw.TRIGGER_FN}()"]
    assert tw.catalog_digest(BASE) == tw.catalog_digest(cat()) != tw.catalog_digest(body)


PRE_LIKE = """-- comment with UPDATE procrastinate_jobs inside
ALTER TYPE procrastinate_job_event_type ADD VALUE 'retried' AFTER 'scheduled';
CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $$
BEGIN UPDATE procrastinate_jobs SET status = 'todo'; INSERT INTO procrastinate_events VALUES (1); END; $$;
"""


def test_backfill_detection_ignores_function_bodies_and_comments_but_not_real_dml():
    assert tw.data_only_statements(PRE_LIKE) == []
    backfill = PRE_LIKE + "UPDATE procrastinate_jobs SET queue_name = 'default' WHERE queue_name IS NULL;\n"
    found = tw.data_only_statements(backfill)
    assert len(found) == 1 and found[0].startswith("UPDATE")
    assert tw.data_only_statements("SELECT id FROM t FOR UPDATE;\nCOPY t FROM stdin;") == ["COPY"]
    trig = ("CREATE TRIGGER t AFTER UPDATE OF status ON procrastinate_jobs FOR EACH ROW EXECUTE PROCEDURE f();\n"
            "CREATE TRIGGER u BEFORE INSERT OR UPDATE ON x FOR EACH ROW EXECUTE PROCEDURE f();\n"
            "ALTER TABLE y ADD FOREIGN KEY (a) REFERENCES z ON DELETE CASCADE ON UPDATE CASCADE;")
    assert tw.data_only_statements(trig) == []
    tagged = "CREATE FUNCTION g() RETURNS trigger AS $function$ BEGIN DELETE FROM x; END; $function$;"
    assert tw.data_only_statements(tagged) == []


def rec(actor, version, sql="SELECT 1", grant=0, pid=1, txn="t1", outcome="ok"):
    return {"actor": actor, "seq": grant, "grant": grant, "sql": sql, "outcome": outcome,
            "provenance": {"app_version": f"procrastinate-{version}", "txn": txn}}


def traces(*recs_by_process):
    return {p: {"records": rs, "actors": {r["actor"]: {"backend_pid": 100 + i} for i, r in enumerate(rs)}}
            for p, rs in recs_by_process}


def test_permitted_operations():
    recs = [rec("old_worker", "3.3.0", "SELECT procrastinate_fetch_job_v2(...)"),
            rec("new_admin", "3.4.0", "SELECT procrastinate_retry_job_v2(1, now(), NULL, NULL, NULL)", 1)]
    assert tw.permitted_violations("3_overlap", recs, ddl_allowed=False, versions=["3.3.0", "3.4.0"]) == []
    bad = recs + [rec("old_worker", "3.3.0", "-- x\nDROP TABLE procrastinate_jobs", 2),
                  rec("old_admin", "3.3.0", "SELECT procrastinate_retry_job_v2(1)", 3)]
    v = tw.permitted_violations("3_overlap", bad, ddl_allowed=False, versions=["3.3.0", "3.4.0"])
    assert any("DDL" in x for x in v) and any("called retry_job_v2" in x for x in v)
    after_post = tw.permitted_violations("5_new_only", recs, ddl_allowed=False, versions=["3.4.0"])
    assert any("old_worker ran procrastinate-3.3.0" in x for x in after_post)


def test_provenance_cross_checks_the_server_log_and_interleaving_counts_switches():
    t = {"A": {"records": [rec("old_worker", "3.3.0", grant=0), rec("old_worker", "3.3.0", grant=2, txn="t2")],
               "actors": {"old_worker": {"backend_pid": 431}}},
         "B": {"records": [rec("new_worker", "3.4.0", grant=1)], "actors": {"new_worker": {"backend_pid": 430}}}}
    p = tw.provenance(t, {"431": "old_worker", "430": "new_worker"})
    assert p["consistent_with_server_log"] and p["actors"]["old_worker"]["transactions"] == 2
    assert not tw.provenance(t, {"999": "old_worker", "430": "new_worker"})["consistent_with_server_log"]
    assert tw.interleaving(t) == {"by_version": {"procrastinate-3.3.0": 2, "procrastinate-3.4.0": 1}, "switches": 2}


def test_quiescence_refuses_each_kind_of_leftover():
    clean = {"client_sessions": [], "workers": 0, "jobs": [{"n": 1, "status": "failed"}, {"n": 2, "status": "todo"}],
             "foreign_locks": [], "prepared": []}
    q = tw.quiescence(clean, [])
    assert q["quiesced"] and q["todo_jobs_left_for_new_version"] == [2]
    for broken, why in [({"client_sessions": [[1, "x", "idle"]]}, "no_client_session"),
                        ({"workers": 1}, "no_registered_worker"),
                        ({"jobs": [{"n": 3, "status": "doing"}]}, "no_doing_job"),
                        ({"foreign_locks": [["advisory", "ExclusiveLock", True]]}, "no_foreign_lock"),
                        ({"prepared": [["gid"]]}, "no_prepared_transaction")]:
        assert tw.quiescence({**clean, **broken}, [])["failed"] == [why]
    assert tw.quiescence(clean, ["abc123"])["failed"] == ["no_old_container"]


def test_background_targets_are_deterministic_and_skip_reserved():
    st = {"jobs": [{"n": 12, "status": "failed"}, {"n": 4, "status": "failed"}, {"n": 1, "status": "failed"},
                   {"n": 8, "status": "failed"}, {"n": 5, "status": "succeeded"}, {"n": 9, "status": "failed"}]}
    assert tw.background_targets(st, {4}) == [1, 8, 9]
    assert tw.background_targets({"jobs": []}, {4}) == []


def test_classification_follows_the_errata():
    checked = {"violations": {"R2": ["n=1: 2 executions but attempts 3", "n=2: 9 executions, more than the bound 4"],
                              "R5": ["admin retry of n=1: before {'status': 'failed', 'attempts': 2}, after "
                                     "{'status': 'doing', 'attempts': 3}",
                                     "admin retry of n=7: before {'status': 'todo', 'attempts': 1}, after "
                                     "{'status': 'todo', 'attempts': 1}"]},
               "admin_retries": [{"n": 1, "ok": True, "before": {"status": "failed", "attempts": 2},
                                  "after": {"status": "doing", "attempts": 3}},
                                 {"n": 7, "ok": True, "before": {"status": "todo", "attempts": 1},
                                  "after": {"status": "todo", "attempts": 1}}],
               "executions": {"1": ["3.3.0", "3.4.0"], "7": ["3.4.0"]}}
    c = tw.classify(checked)
    assert c["oracle_error_E1"] == ["n=1: 2 executions but attempts 3"]
    assert len(c["oracle_error_E2"]) == 1 and "n=1" in c["oracle_error_E2"][0]
    assert sorted(c["genuine_violations"]) == sorted(["R2: n=2: 9 executions, more than the bound 4",
                                                      "R5: " + checked["violations"]["R5"][1]])
    once = {**checked, "executions": {"1": ["3.3.0"], "7": ["3.4.0"]}}  # not executed again: not E2
    assert len(tw.classify(once)["oracle_error_E2"]) == 0


def ctx_ok():
    return {"permitted_violations": [], "provenance": {"consistent_with_server_log": True},
            "interleaving": {"by_version": {"a": 1, "b": 1}, "switches": 1}}


def side(catalog, jobs=(), workers=0):
    return {"catalog": catalog, "state": {"jobs": list(jobs), "workers": workers}}


def test_exit_checks_post_may_change_only_the_trigger_function():
    entry = side(BASE, [{"n": 1, "status": "failed"}])
    changed = side(cat(functions=[BASE["functions"][0], [tw.TRIGGER_FN, "", "trigger", "f", "9", "9"]]),
                   [{"n": 1, "status": "failed"}])
    assert rt.exit_checks("4_post_migration", entry, changed, ctx_ok()) == []
    more = side(cat(functions=[[tw.TRIGGER_FN, "", "trigger", "f", "9", "9"]]), [{"n": 1, "status": "failed"}])
    assert rt.exit_checks("4_post_migration", entry, more, ctx_ok())
    rows = side(changed["catalog"], [{"n": 1, "status": "todo"}])
    assert "post modified job rows" in rt.exit_checks("4_post_migration", entry, rows, ctx_ok())


@pytest.mark.parametrize("step", ["0b_old_on_old_schema", "3_overlap", "T4_background_retry", "5_new_only"])
def test_exit_checks_refuse_catalog_change_workers_doing_and_bad_provenance(step):
    entry = side(BASE)
    assert rt.exit_checks(step, entry, side(BASE, [{"n": 1, "status": "succeeded"}]), ctx_ok()) == []
    assert rt.exit_checks(step, entry, side(cat(columns=[])), ctx_ok())
    assert "a worker is still registered" in rt.exit_checks(step, entry, side(BASE, workers=1), ctx_ok())
    assert "a job is still doing" in rt.exit_checks(step, entry, side(BASE, [{"n": 1, "status": "doing"}]), ctx_ok())
    bad = {**ctx_ok(), "provenance": {"consistent_with_server_log": False}}
    assert rt.exit_checks(step, entry, side(BASE), bad)
    if step == "3_overlap":
        solo = {**ctx_ok(), "interleaving": {"by_version": {"a": 3}, "switches": 0}}
        assert any("interleave" in x for x in rt.exit_checks(step, entry, side(BASE), solo))


def test_classification_matches_each_r5_entry_to_its_own_retry_when_a_job_is_retried_twice():
    """Regression (found in twin run seeded_2): n=1 is admin-retried in the overlap *and* in T4. The frozen checker's
    R5 entry names the overlap retry; keying retries by n alone matched it to the T4 retry and called a documented
    E2 a genuine violation."""
    overlap = {"n": 1, "ok": True, "before": {"status": "failed", "attempts": 2},
               "after": {"status": "doing", "attempts": 3}}
    t4 = {"n": 1, "ok": True, "before": {"status": "failed", "attempts": 4}, "after": {"status": "todo", "attempts": 5}}
    entry = f"admin retry of n=1: before {overlap['before']}, after {overlap['after']}"
    checked = {"violations": {"R5": [entry]}, "admin_retries": [overlap, t4],
               "executions": {"1": ["3.3.0", "3.4.0", "3.3.0"]}}
    c = tw.classify(checked)
    assert c["oracle_error_E2"] == [entry] and c["genuine_violations"] == []
