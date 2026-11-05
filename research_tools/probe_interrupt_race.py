"""EXPERIMENTAL reliability probe, suspected race 1: ``test_an_interrupted_run_still_tears_down``.

    python research_tools/probe_interrupt_race.py --sdist SDISTS/chancy-0.26.0.tar.gz --delays 0,0.25,... \
        --reps 2 --out DIR

Each attempt starts the real runner exactly as the test does (``run_isolated.py chancy-runtime``), watches for its
``ds-mvp-iso-run`` container every 20 ms (the test polls every ~0.5 s plus two ``docker`` calls), waits a controlled
delay after the container appears, and sends SIGINT to the runner (as the test's ``proc.send_signal``). ``never``
sends nothing and measures the natural timeline. Recorded per attempt: when the container appeared and disappeared,
when the signal was sent, the runner's exit status and when it exited, its stdout/stderr tails, and what the test's
``leftovers()`` would see afterwards. The question is whether either test assertion (``returncode != 0`` and no
leftovers) can fail, and at which signal timing. Nothing is retried. Run it under ``pg_lock.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def leftovers() -> tuple[str, str]:  # the test's own helper, verbatim in effect
    ps = subprocess.run(["docker", "ps", "-a", "--filter", "name=ds-mvp-iso-run", "-q"], capture_output=True,
                        text=True).stdout.strip()
    nets = subprocess.run(["docker", "network", "ls", "--filter", "name=ds-mvp-iso-", "-q"], capture_output=True,
                          text=True).stdout.strip()
    return ps, nets


def attempt(sdist: Path, delay: float | None, out: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("MODEL_API_KEY", "DATABASE_URL")}
    before = leftovers()
    t0 = time.monotonic()
    proc = subprocess.Popen([sys.executable, str(REPO / "research_tools/isolated/run_isolated.py"), "chancy-runtime",
                             "--chancy-sdist", str(sdist), "--out", str(out)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env)
    appeared = gone = sent = None
    while proc.poll() is None and time.monotonic() - t0 < 300:
        c = subprocess.run(["docker", "ps", "-a", "--filter", "name=ds-mvp-iso-run", "-q"], capture_output=True,
                           text=True).stdout.strip()
        now = time.monotonic() - t0
        if c and appeared is None:
            appeared = now
        if appeared is not None and not c and gone is None:
            gone = now
        if appeared is not None and delay is not None and sent is None and now >= appeared + delay:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
                sent = time.monotonic() - t0
            else:
                sent = "runner had already exited"
        time.sleep(0.02)
    so, se = proc.communicate(timeout=120)
    exited = time.monotonic() - t0
    after = leftovers()
    return {"delay": delay, "before_leftovers": before, "container_appeared_s": appeared, "container_gone_s": gone,
            "signal_sent_s": sent, "runner_exit": proc.returncode, "runner_exited_s": round(exited, 3),
            "after_leftovers": after,
            "test_would_fail": {"returncode_zero": proc.returncode == 0, "leftovers": after != ("", "")},
            "stdout_tail": so[-600:], "stderr_tail": se[-1500:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sdist", type=Path, required=True)
    ap.add_argument("--delays", default="never,0,0.1,0.25,0.5,0.75,1.0,1.5,2.0,3.0")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    rows = []
    with (a.out / "attempts.jsonl").open("w") as f:
        k = 0
        for rep in range(a.reps):
            for ds in a.delays.split(","):
                delay = None if ds == "never" else float(ds)
                r = {"k": k, "rep": rep, **attempt(a.sdist, delay, a.out / f"runner_{k:03d}")}
                rows.append(r)
                f.write(json.dumps(r) + "\n")
                f.flush()
                k += 1
                if r["after_leftovers"] != ("", ""):  # record, then stop: a leftover would poison later attempts
                    (a.out / "STOPPED_ON_LEFTOVER").write_text(json.dumps(r["after_leftovers"]))
                    break
            else:
                continue
            break
    summary = {"attempts": len(rows),
               "returncode_zero_after_signal": [r["k"] for r in rows if r["delay"] is not None and
                                                r["runner_exit"] == 0],
               "leftovers_after": [r["k"] for r in rows if r["after_leftovers"] != ("", "")],
               "natural": [{k: r[k] for k in ("container_appeared_s", "container_gone_s", "runner_exited_s",
                                              "runner_exit")} for r in rows if r["delay"] is None],
               "by_delay": {str(r["delay"]): [] for r in rows}}
    for r in rows:
        summary["by_delay"][str(r["delay"])].append({"exit": r["runner_exit"], "sent": r["signal_sent_s"],
                                                     "appeared": r["container_appeared_s"],
                                                     "gone": r["container_gone_s"], "exited": r["runner_exited_s"],
                                                     "left": r["after_leftovers"] != ("", "")})
    (a.out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
