"""EXPERIMENTAL, NONBLIND: workloads for the historical case chancy-2895c4a (B1-B3).

A workload is ``{"ns": [0..jobs-1], "cancel": [...], "max_attempts": m, "workers": w}``: one producer pushes the
jobs through Chancy's public ``push`` and cancels the listed ones through ``cancel_job``; one or two workers of the
same revision process the queue (the documented single-version deployment). The engine, its gates (no producer
statement before every worker has polled; a cancel only after the first claim) and the seeded schedule policies are
the same for every arm. Muse sees this description and the constraints: no source, fix, changelog, test or oracle.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

from pydantic import BaseModel

from . import greedy

MAX_JOBS, MAX_ATTEMPTS, MAX_WORKERS = 3, 3, 2


class ChancyProposal(BaseModel):
    rationale: str
    jobs: int
    cancel: list[int]
    max_attempts: int
    workers: int


class ChancyProposalSet(BaseModel):
    proposals: list[ChancyProposal]


INSTRUCTIONS = ("You design test workloads for a PostgreSQL-backed job queue library. Each workload is executed for "
                "real by the library's own code, under a deterministic scheduler that grants every database "
                "statement of every actor in one global order and tries different orders; the jobs' rows are then "
                "read back from the database and checked independently. Propose workloads likely to expose "
                "data-integrity problems in how the library handles jobs that are worked on and cancelled "
                "concurrently. You cannot write SQL or code. Every proposal must satisfy the stated constraints "
                "exactly, or it is rejected. Return JSON only.")


def context() -> dict[str, Any]:
    return {"system": "a job queue library on PostgreSQL, one release deployed by its documented single-version "
                      "procedure (every worker runs the same release)",
            "actors": {"producer": "pushes `jobs` jobs, numbered 0..jobs-1, to one queue with the given "
                                   "max_attempts through the library's public push, then requests cancellation of "
                                   "the jobs listed in `cancel` through the library's public cancel_job",
                       "workers": "`workers` workers (1 or 2) of the same release process that queue, each on its "
                                  "own database session"},
            "handler": "every job's handler returns normally",
            "scheduler": "no producer statement runs before every worker has polled the queue once; a "
                         "cancellation statement runs only after some worker has claimed a job; seeded policies "
                         "decide the remaining order",
            "constraints": {"jobs": f"1..{MAX_JOBS}", "cancel": "distinct job numbers, each < jobs (may be empty)",
                            "max_attempts": f"1..{MAX_ATTEMPTS}", "workers": f"1..{MAX_WORKERS}"}}


def prompt(n: int) -> str:
    return (f"Propose exactly {n} different workloads.\n\nContext (JSON):\n"
            + json.dumps(context(), indent=1, sort_keys=True))


def validate(p: Any) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        prop = ChancyProposal.model_validate(p)
    except Exception as exc:  # noqa: BLE001 - a malformed proposal is a recorded rejection
        return None, [f"shape: {type(exc).__name__}"]
    problems = []
    if not 1 <= prop.jobs <= MAX_JOBS:
        problems.append(f"jobs {prop.jobs} not in 1..{MAX_JOBS}")
    if len(set(prop.cancel)) != len(prop.cancel) or not all(0 <= c < prop.jobs for c in prop.cancel):
        problems.append(f"cancel {prop.cancel}: not distinct job numbers below {prop.jobs}")
    if not 1 <= prop.max_attempts <= MAX_ATTEMPTS:
        problems.append(f"max_attempts {prop.max_attempts} not in 1..{MAX_ATTEMPTS}")
    if not 1 <= prop.workers <= MAX_WORKERS:
        problems.append(f"workers {prop.workers} not in 1..{MAX_WORKERS}")
    if problems:
        return None, problems
    return workload(prop.jobs, prop.cancel, prop.max_attempts, prop.workers), []


def workload(jobs: int, cancel: list[int], max_attempts: int, workers: int) -> dict[str, Any]:
    return {"ns": list(range(jobs)), "cancel": sorted(cancel), "max_attempts": max_attempts, "workers": workers}


def key(w: dict[str, Any]) -> str:
    return json.dumps(w, sort_keys=True)


# B1: the maintainers' regression-test shape (one job, cancelled while worked on), then two developer-written ones.
# Written with knowledge of the defect: nonblind by construction.
CURATED = [workload(1, [0], 1, 1), workload(2, [0], 2, 1), workload(2, [0, 1], 1, 2)]


def grid() -> list[dict[str, Any]]:
    out = []
    for jobs, m, w in itertools.product(range(1, MAX_JOBS + 1), range(1, MAX_ATTEMPTS + 1), range(1, MAX_WORKERS + 1)):
        for r in range(jobs + 1):
            for cancel in itertools.combinations(range(jobs), r):
                out.append(workload(jobs, list(cancel), m, w))
    return out


def features(w: dict[str, Any]) -> tuple[Any, ...]:
    return (len(w["ns"]), len(w["cancel"]), 0 in w["cancel"], w["max_attempts"], w["workers"])


def systematic(limit: int) -> list[dict[str, Any]]:
    """B2: the full grid in greedy pairwise-coverage order."""
    return greedy.order(grid(), features, limit)
