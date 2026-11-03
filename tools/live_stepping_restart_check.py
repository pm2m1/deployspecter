"""A server crash and restart during a run. Outside pytest, because it ends every session on the cluster.

Usage: uv run python tools/live_stepping_restart_check.py OUTPUT.json

Kills one backend with SIGKILL, so the postmaster resets every session and goes through crash recovery. That's a real
restart of the database, on the disposable cluster only. A run must not survive it as a result, and the runner must
refuse to continue until a new supervisor has re-verified the cluster.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from deployspecter.config import load_settings  # noqa: E402
from deployspecter.database import Sandbox  # noqa: E402
from deployspecter.live.runner import ExplicitSchedule, LiveActor, LiveScenario  # noqa: E402
from deployspecter.live.supervisor import SupervisedRunner, SupervisorHooks, SupervisorLimits  # noqa: E402
from live_app import adversarial as adv  # noqa: E402

ADV = "live_app.adversarial:"


def wait_for_server(settings, seconds: float = 60.0) -> Sandbox:
    deadline = time.monotonic() + seconds
    while True:
        try:
            return Sandbox(settings)
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.25)


def main() -> int:
    output = Path(sys.argv[1])
    settings = load_settings()
    box = Sandbox(settings)
    seen: dict = {}

    def tick(runner, ctx):
        if not ctx.actor_pids or seen.get("sent"):
            return
        rows = runner.box.admin.execute(
            "SELECT pid FROM pg_stat_activity WHERE pid = ANY(%s) AND query LIKE '%%pg_sleep%%' AND state = 'active'",
            (list(ctx.actor_pids.values()),),
        ).fetchall()
        if rows:
            seen["sent"] = True
            seen["proc"] = ctx.proc
            runner.box.admin.execute(f"COPY (SELECT 1) TO PROGRAM 'kill -9 {rows[0][0]}; cat > /dev/null'")

    scenario = LiveScenario("restart", adv.SETUP, [], [LiveActor("a", f"{ADV}write_then_wait")])
    runner = SupervisedRunner(box, supervisor=SupervisorLimits(db_call_s=5.0), hooks=SupervisorHooks(on_tick=tick))
    started = time.monotonic()
    trace = runner.run(scenario, ExplicitSchedule([]))
    elapsed = time.monotonic() - started
    refused = False
    try:
        runner.run(scenario, ExplicitSchedule([]))
    except RuntimeError:
        refused = True
    proc = seen.get("proc")
    child_alive = proc is not None and proc.poll() is None
    if proc is not None and child_alive:
        os.killpg(proc.pid, 9)
    fresh_box = wait_for_server(settings)
    # Not cleaned up here: sweeping what a crash left behind is the new supervisor's job, and this checks it does.
    leftover = fresh_box.admin.execute(
        "SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname LIKE 'ds_run_%'"
    ).fetchall()
    after = SupervisedRunner(fresh_box).run(scenario.__class__("clean", adv.SETUP, [], [LiveActor("a", f"{ADV}bump")]),
                                            ExplicitSchedule([]))
    remaining = fresh_box.admin.execute("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'ds_run_%'").fetchone()[0]
    report = {
        "crash_sent": bool(seen.get("sent")), "elapsed_s": round(elapsed, 2), "outcome": trace.outcome,
        "scoreable": trace.scoreable, "inconclusive": trace.inconclusive,
        "flags": [f.model_dump() for f in trace.evidence.flags], "poisoned": runner.poisoned,
        "next_run_refused": refused, "child_alive_afterwards": child_alive, "roles_left_by_the_crash": len(leftover),
        "those_roles_could_still_log_in": any(row[1] for row in leftover), "roles_after_new_supervisor": remaining,
        "new_supervisor_after_recovery": {"outcome": after.outcome, "scoreable": after.scoreable,
                                          "flags": [f.model_dump() for f in after.evidence.flags]},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    fresh_box.close()
    good = (report["crash_sent"] and not report["scoreable"] and report["inconclusive"] and report["next_run_refused"]
            and not child_alive and after.scoreable and remaining == 0)
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
