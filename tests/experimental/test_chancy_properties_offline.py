"""EXPERIMENTAL, offline: vectors for the frozen simultaneous-pilot property checker."""

from __future__ import annotations

from deployspecter.experimental import chancy_properties as cp


def job(n, state="succeeded", attempts=1, max_attempts=1, completed=True, errors=0, jid=None):
    return {"id": jid or f"id{n}", "n": n, "queue": "q", "state": state, "attempts": attempts,
            "max_attempts": max_attempts, "has_claim_id": True, "completed": completed, "errors": errors,
            "taken": True}


def results(pushed, old=(), new=(), old_ok=None, new_ok=None, cancelled=()):
    return {"push": {"version": "0.26.0", "pushed": [{"n": n, "id": f"id{n}", "version": "0.26.0"} for n in pushed],
                     "cancelled": [{"n": n, "rowcount": 1} for n in cancelled]},
            "old_worker": {"version": "0.25.1", "handled": list(old), "succeeded": list(old if old_ok is None
                                                                                       else old_ok)},
            "new_worker": {"version": "0.26.0", "handled": list(new), "succeeded": list(new if new_ok is None
                                                                                       else new_ok)}}


def test_a_clean_run_has_no_violations():
    out = cp.check([job(0), job(1)], results([0, 1], old=[0], new=[1]), {}, quiescent=True)
    assert out["violations"] == {} and out["executions"] == {"0": ["0.25.1"], "1": ["0.26.0"]}


def test_a_double_execution_violates_p2():
    out = cp.check([job(0)], results([0], old=[0], new=[0]), {}, quiescent=True)
    assert set(out["violations"]) == {"P2"}


def test_a_cancel_overwritten_by_a_late_completion_violates_p7_only():
    out = cp.check([job(0)], results([0], old=[0], cancelled=[0]), {}, quiescent=True)
    assert set(out["violations"]) == {"P7"}


def test_a_respected_cancel_is_clean_even_though_its_handler_ran():
    out = cp.check([job(0, state="failed", attempts=0)], results([0], new=[0], cancelled=[0]), {}, quiescent=True)
    assert out["violations"] == {}


def test_a_retry_handoff_is_clean_and_a_miscounted_one_is_not():
    good = cp.check([job(0, attempts=2, max_attempts=3, errors=1)],
                    results([0], old=[0], new=[0], old_ok=[]), {"retry": [0]}, quiescent=True)
    assert good["violations"] == {}
    bad = cp.check([job(0, attempts=1, max_attempts=3, errors=0)],
                   results([0], old=[0], new=[0], old_ok=[]), {"retry": [0]}, quiescent=True)
    assert {"P2", "P8"} <= set(bad["violations"])


def test_a_stuck_job_violates_p6_only_after_a_quiescent_completion():
    stuck = [job(0, state="pending", attempts=0, completed=False)]
    assert "P6" in cp.check(stuck, results([0]), {}, quiescent=True)["violations"]
    out = cp.check(stuck, results([0]), {}, quiescent=False)
    assert "P6" not in out["violations"] and out["not_evaluated"] == ["P6"]


def test_a_lost_success_violates_p3():
    out = cp.check([job(0, state="running", attempts=0, completed=False)], results([0], new=[0]), {},
                   quiescent=True)
    assert "P3" in out["violations"]
