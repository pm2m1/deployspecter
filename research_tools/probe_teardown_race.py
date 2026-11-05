"""EXPERIMENTAL reliability probe, suspected race 2: ``docker run --rm`` auto-removal vs the isolated runner's own
teardown (``docker rm -f``, ``network disconnect -f`` of the PostgreSQL container, ``network rm``).

    python research_tools/probe_teardown_race.py --image TAG --n 40 --out DIR

Each iteration reproduces ``run_isolated.run_smoke``'s container lifecycle step for step, with a throwaway container
``ds-mvp-iso-probe-<nonce>`` on an internal network ``ds-mvp-iso-probe-net-<nonce>``, then the runner's exact finally
sequence, recording every command's exit status, stderr and timing, and whether the container or the network was
left behind. Variants: ``A`` the container exits at once (``true``); ``B`` it runs for ~0.3 s (``sleep``); ``C``
the network removal is issued with no ``docker rm -f`` first. Nothing is retried; a final sweep removes anything left,
and that sweep is recorded too. Run it under ``pg_lock.py`` (it attaches the disposable PostgreSQL container).
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "isolated"))
import run_isolated as ri  # noqa: E402


def d(*args: str) -> dict:
    t = time.monotonic()
    p = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
    return {"cmd": args[0] + (" " + args[1] if len(args) > 1 and not args[1].startswith("-") else ""),
            "exit": p.returncode, "stderr": p.stderr.strip()[-300:], "ms": round((time.monotonic() - t) * 1000, 1)}


def one(image: str, variant: str) -> dict:
    nonce = secrets.token_hex(4)
    net, name = f"ds-mvp-iso-probe-net-{nonce}", f"ds-mvp-iso-probe-{nonce}"
    steps = [d("network", "create", "--internal", net), d("network", "connect", "--alias", "ds-mvp-pg", net,
                                                          ri.PG_CONTAINER)]
    cmd = ["true"] if variant == "A" else ["sleep", "0.3"]
    steps.append(d("run", "--rm", "--name", name, "--network", net, *ri.RUNTIME_FLAGS, "--entrypoint", cmd[0],
                   image, *cmd[1:]))
    if variant != "C":
        steps.append(d("rm", "-f", name))
    steps.append(d("network", "disconnect", "-f", net, ri.PG_CONTAINER))
    steps.append(d("network", "rm", net))
    left_c = subprocess.run(["docker", "ps", "-a", "--filter", f"name={name}", "-q"], capture_output=True,
                            text=True).stdout.strip()
    left_n = subprocess.run(["docker", "network", "ls", "--filter", f"name={net}", "-q"], capture_output=True,
                            text=True).stdout.strip()
    sweep = []
    if left_c or left_n:  # recorded cleanup, never a retry of the measured sequence
        time.sleep(2)
        sweep = [d("rm", "-f", name), d("network", "disconnect", "-f", net, ri.PG_CONTAINER), d("network", "rm", net)]
    return {"variant": variant, "nonce": nonce, "steps": steps, "container_left": bool(left_c),
            "network_left": bool(left_n), "runner_would_report": {"container_removed": not left_c,
                                                                  "network_removed": not left_n},
            "sweep": sweep}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    rows = []
    with (a.out / "iterations.jsonl").open("w") as f:
        for i in range(a.n):
            r = {"i": i, **one(a.image, "ABC"[i % 3])}
            rows.append(r)
            f.write(json.dumps(r) + "\n")
            f.flush()
    summary = {}
    for v in "ABC":
        rs = [r for r in rows if r["variant"] == v]
        summary[v] = {"iterations": len(rs), "container_left": sum(r["container_left"] for r in rs),
                      "network_left": sum(r["network_left"] for r in rs),
                      "rm_f_nonzero": sum(1 for r in rs for s in r["steps"] if s["cmd"] == "rm" and s["exit"]),
                      "network_rm_nonzero": sum(1 for r in rs for s in r["steps"]
                                                if s["cmd"] == "network rm" and s["exit"]),
                      "stderr_kinds": sorted({s["stderr"][:90] for r in rs for s in r["steps"] if s["exit"]})}
    final_c = subprocess.run(["docker", "ps", "-a", "--filter", "name=ds-mvp-iso-probe", "-q"], capture_output=True,
                             text=True).stdout.split()
    final_n = subprocess.run(["docker", "network", "ls", "--filter", "name=ds-mvp-iso-probe", "-q"],
                             capture_output=True, text=True).stdout.split()
    out = {"image": a.image, "runtime_flags": ri.RUNTIME_FLAGS, "summary": summary,
           "after_all": {"probe_containers": final_c, "probe_networks": final_n}}
    (a.out / "summary.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
