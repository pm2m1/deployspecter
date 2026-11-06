"""EXPERIMENTAL onboarding pilot: the success criteria S2 and S3, from the harness's own records.

* **S2 (corroborated run):** the run's server-log corroboration is ok, with 0 parameters mismatched; isolation is
  ok; teardown is complete.
* **S3 (independent correctness):** on a run that meets S2:
  - the harness-owned recorder saw every enqueued key handled **exactly once**, and nothing else;
  - each phase (install, enqueue, work) finished without an exception.

Neither criterion uses the adapter's account of what happened.
"""

from __future__ import annotations

import collections
from typing import Any


def s2(run: dict[str, Any]) -> list[str]:
    c = run.get("corroboration") or {}
    p = []
    if c.get("ok") is not True:
        p.append(f"corroboration not ok: {(c.get('problems') or [])[:3]}")
    if c.get("params_mismatched", 0) != 0:
        p.append(f"{c.get('params_mismatched')} parameters mismatched")
    if not run.get("isolation_ok"):
        p.append("isolation checks failed")
    if not all((run.get("teardown") or {"missing": False}).values()):
        p.append(f"teardown incomplete: {run.get('teardown')}")
    return p


def s3(run: dict[str, Any], workload: dict[str, Any]) -> list[str]:
    p = s2(run)
    want = collections.Counter(j["key"] for j in workload["jobs"])
    got = collections.Counter(run.get("handled") or [])
    if got != want:
        missing = sorted((want - got).elements())
        extra = sorted((got - want).elements())
        p.append(f"handled exactly once: missing {missing}, extra or repeated {extra}")
    for phase in ("install", "enqueue", "work"):
        if (run.get("phases") or {}).get(phase, {}).get("exception"):
            p.append(f"phase {phase} raised: {str(run['phases'][phase]['exception'])[:200]}")
        elif phase not in (run.get("phases") or {}):
            p.append(f"phase {phase} didn't run")
    return p
