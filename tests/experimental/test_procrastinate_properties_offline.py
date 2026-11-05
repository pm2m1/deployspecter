"""EXPERIMENTAL, offline: vectors for the procrastinate rolling-upgrade case checker."""

from __future__ import annotations

from deployspecter.experimental import procrastinate_properties as pp


def setup(status="succeeded", attempts=0, runs=("3.3.0",), oks=("3.3.0",), events=("deferred", "started", "succeeded"),
          max_attempts=0, admin=()):
    jobs = [{"id": 10, "n": 0, "status": status, "attempts": attempts, "task_name": "ds_ok", "queue_name": "q"}]
    evs = [{"job_id": 10, "type": t} for t in events]
    pushed = [{"n": 0, "id": 10, "version": "3.4.0", "max_attempts": max_attempts}]
    res = {"push": {"version": "3.4.0", "pushed": pushed},
           "w_old": {"version": "3.3.0", "handled": [0 for v in runs if v == "3.3.0"],
                     "succeeded": [0 for v in oks if v == "3.3.0"]},
           "w_new": {"version": "3.4.0", "handled": [0 for v in runs if v == "3.4.0"],
                     "succeeded": [0 for v in oks if v == "3.4.0"]},
           "admin": {"version": "3.4.0", "retried": list(admin)}}
    return jobs, evs, res


def test_a_cross_version_success_is_clean():
    out = pp.check(*setup(), post_applied_before=set(), quiescent=True)
    assert out["violations"] == {}


def test_a_retry_across_versions_is_clean_and_counted():
    jobs, evs, res = setup(attempts=1, runs=("3.3.0", "3.4.0"), oks=("3.4.0",), max_attempts=1,
                           events=("deferred", "started", "deferred_for_retry", "started", "succeeded"))
    assert pp.check(jobs, evs, res, post_applied_before=set(), quiescent=True)["violations"] == {}


def test_too_many_executions_violate_r2():
    jobs, evs, res = setup(attempts=2, runs=("3.3.0", "3.4.0", "3.4.0"), oks=("3.4.0",), max_attempts=1)
    assert "R2" in pp.check(jobs, evs, res, post_applied_before=set(), quiescent=True)["violations"]


def test_a_stuck_job_violates_r1_only_when_quiescent():
    jobs, evs, res = setup(status="doing", runs=(), oks=())
    assert "R1" in pp.check(jobs, evs, res, post_applied_before=set(), quiescent=True)["violations"]
    assert "R1" not in pp.check(jobs, evs, res, post_applied_before=set(), quiescent=False)["violations"]


def test_a_retried_event_before_post_violates_r6():
    jobs, evs, res = setup(events=("deferred", "started", "failed", "retried", "started", "succeeded"))
    out = pp.check(jobs, evs, res, post_applied_before=set(), quiescent=True)
    assert "R6" in out["violations"]


def test_an_admin_retry_must_go_failed_to_todo_with_one_more_attempt():
    before = {"status": "failed", "attempts": 1}
    good = {"n": 0, "ok": True, "before": before, "after": {"status": "todo", "attempts": 2}}
    bad = {"n": 0, "ok": True, "before": before, "after": {"status": "failed", "attempts": 1}}
    for a, expect in ((good, False), (bad, True)):
        jobs, evs, res = setup(attempts=2, runs=("3.3.0", "3.3.0", "3.4.0"), oks=("3.4.0",), max_attempts=1,
                               admin=[a])
        assert ("R5" in pp.check(jobs, evs, res, post_applied_before={0}, quiescent=True)["violations"]) == expect
