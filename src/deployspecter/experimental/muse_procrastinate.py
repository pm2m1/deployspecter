"""EXPERIMENTAL, NONBLIND: a Muse target for the procrastinate 3.3.0 → 3.4.0 rolling-upgrade case's overlap phase.

Muse proposes structured overlap workloads (JSON against a strict schema). A deterministic validator checks them, and
the harness alone turns a valid proposal into actor arguments. The model never writes SQL or Python, and nothing it
returns is executed as code. The context holds only the application's registered task kinds, the phase structure and
the constraints: no source, docstrings, fixes, oracles or results.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..meta_client import PRICING_NOTE, MetaClient, strict_json_schema
from . import muse_apps as ma

PROMPT_VERSION = "mvp-muse-procrastinate-1"
TARGET = "procrastinate_overlap"
KINDS = ("ok", "flaky", "fail")
RELEASES = ("3.3.0", "3.4.0")
EARLIER_FAILED = (1, 2)  # jobs that ended `failed` in phase 0 (the always-failing one and a flaky one)
INSTRUCTIONS = ("You design test workloads for a blue-green upgrade of a PostgreSQL-backed task queue library, during "
                "the window in which its old and new releases run at the same time on one schema. Your workloads are "
                "executed for real by the actual library and the database's final state is checked independently. "
                "Propose workloads likely to expose problems in how jobs deferred, executed, retried or administered "
                "by one release are handled by the other. You cannot write SQL or code. Every proposal must satisfy "
                "the stated constraints exactly, or it is rejected. Return JSON only.")


class Job(BaseModel):
    kind: str
    fail_in: str


class Proposal(BaseModel):
    rationale: str
    old_jobs: list[Job]
    new_jobs: list[Job]
    admin_retry: list[int]


class ProposalSet(BaseModel):
    proposals: list[Proposal]


def context() -> dict[str, Any]:
    return {"system": "a task queue library on PostgreSQL, upgraded from release 3.3.0 (old) to 3.4.0 (new) by its "
                      "documented blue-green procedure: a pre-migration, then both releases run together, then the "
                      "old release stops and a post-migration is applied",
            "overlap_window": "an old-release producer defers old_jobs and a new-release producer defers new_jobs "
                              "while one old-release worker and one new-release worker both process the same queue; "
                              "the new release's administrator may retry earlier failed jobs",
            "task_kinds": {"ok": "returns normally", "fail": "always raises; retried once, then failed",
                           "flaky": "raises when executed by the release named in fail_in, returns normally "
                                    "otherwise; retried once"},
            "earlier_failed_jobs": {"1": "a job that always fails (failed before the overlap)",
                                    "2": "a flaky job that failed under the old release before the overlap"},
            "constraints": {"old_jobs": "0..4 items", "new_jobs": "0..4 items", "total jobs": "1..6",
                            "kind": list(KINDS), "fail_in": "one of 3.3.0 or 3.4.0 for kind flaky, else 'none'",
                            "admin_retry": "a subset of [1, 2]; each id at most once"}}


def validate(p: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """A proposal → the overlap workload (``old_jobs``, ``new_jobs``, ``admin``), or the reasons it's rejected."""
    try:
        prop = Proposal.model_validate(p)
    except Exception as exc:  # noqa: BLE001 - a malformed proposal is a recorded rejection
        return None, [f"shape: {type(exc).__name__}"]
    problems = []
    for side, jobs in (("old_jobs", prop.old_jobs), ("new_jobs", prop.new_jobs)):
        if len(jobs) > 4:
            problems.append(f"{side}: more than 4")
        for j in jobs:
            if j.kind not in KINDS:
                problems.append(f"{side}: unknown kind {j.kind!r}")
            elif j.kind == "flaky" and j.fail_in not in RELEASES:
                problems.append(f"{side}: flaky needs fail_in in {RELEASES}")
            elif j.kind != "flaky" and j.fail_in != "none":
                problems.append(f"{side}: fail_in must be 'none' for {j.kind}")
    total = len(prop.old_jobs) + len(prop.new_jobs)
    if not 1 <= total <= 6:
        problems.append(f"total jobs {total} not in 1..6")
    if len(set(prop.admin_retry)) != len(prop.admin_retry) or not set(prop.admin_retry) <= set(EARLIER_FAILED):
        problems.append(f"admin_retry {prop.admin_retry} isn't a subset of {list(EARLIER_FAILED)}")
    if problems:
        return None, problems
    def jobs(xs: list[Job]) -> list[dict[str, Any]]:
        return [{"kind": j.kind, **({"fail_in": j.fail_in} if j.kind == "flaky" else {})} for j in xs]
    return {"old_jobs": jobs(prop.old_jobs), "new_jobs": jobs(prop.new_jobs), "admin": sorted(prop.admin_retry)}, []


def systematic(limit: int) -> list[dict[str, Any]]:
    """The deterministic baseline: a fixed-order grid. Each side defers one of the four job shapes, ordered so that
    the first workloads cover every shape on both sides, then each admin variant."""
    shapes = [{"kind": "ok"}, {"kind": "fail"}, {"kind": "flaky", "fail_in": "3.3.0"},
              {"kind": "flaky", "fail_in": "3.4.0"}]
    out = []
    for admin in ([], [1], [2], [1, 2]):
        for i, j in itertools.product(range(4), range(4)):
            out.append({"old_jobs": [shapes[i], shapes[(i + 1) % 4]], "new_jobs": [shapes[j], shapes[(j + 2) % 4]],
                        "admin": admin})
    # diagonal first (covers all shapes on both sides early), then the rest, in fixed order
    order = sorted(range(len(out)), key=lambda k: (k % 16 != (k % 16 // 4) * 5, k))
    return [out[k] for k in order][:limit]


def prompt(n: int) -> str:
    return (f"Propose exactly {n} different workloads.\n\nContext (JSON):\n"
            + json.dumps(context(), indent=1, sort_keys=True))


def prompt_hash(n: int) -> str:
    return hashlib.sha256((PROMPT_VERSION + INSTRUCTIONS + prompt(n)).encode()).hexdigest()


def propose(client: MetaClient, *, n: int, ledger: Path, call_label: str) -> list[tuple[str, Any]]:
    """One request, as ``muse_apps.propose``: usage and estimated cost logged at once, the raw reply saved on
    arrival, nothing executed."""
    entry: dict[str, Any] = {"call": call_label, "target": TARGET,
                             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "model": client.model,
                             "prompt_version": PROMPT_VERSION, "prompt_sha256": prompt_hash(n),
                             "reasoning_effort": client.reasoning_effort, "store": client.store,
                             "requested_proposals": n, "pricing_note": PRICING_NOTE}
    try:
        reply = client.generate(INSTRUCTIONS, prompt(n), f"ds_mvp_{TARGET}", strict_json_schema(ProposalSet),
                                max_output_tokens=16000)
    except Exception as exc:
        entry.update(error=type(exc).__name__, detail=str(exc)[:300])
        ma._append(ledger, entry)
        raise
    (ledger.parent / f"{call_label}.reply.json").write_text(reply.text)  # kept at once
    entry.update(status=reply.status, latency_s=reply.latency_seconds, input_tokens=reply.input_tokens,
                 output_tokens=reply.output_tokens, reasoning_tokens=reply.reasoning_tokens,
                 estimated_usd=reply.approx_cost_usd, cumulative_estimated_usd=round(client.estimated_usd, 6))
    try:
        raw = json.loads(reply.text).get("proposals", [])
    except ValueError:
        raw, entry["parse_error"] = [], True
    entry["proposals_returned"] = len(raw)
    entry["raw_reply_sha256"] = hashlib.sha256(reply.text.encode()).hexdigest()
    ma._append(ledger, entry)
    return [(f"{call_label}#{i}", p) for i, p in enumerate(raw)]
