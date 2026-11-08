"""EXPERIMENTAL, NONBLIND (ds-nbc/1): a pinned, declared variant of ``chancy_adapter`` for further historical trees.

Runs only inside the isolated runner, and only when the container's ``DS_CHANCY_ADAPTER`` names it
(``smoke_inside.py``); the default adapter is unchanged and never imports this module.

What it adds, and nothing else:
* **Pins.** The source hashes of two more commit-pinned trees (case ``chancy-b8a77df``: the fix commit and its
  parent, ``research_tools/nbc/pin_chancy.py``), registered into ``chancy_adapter.PINNED_BY_VERSION`` when this module
  is imported in the container. ``installed_version`` still refuses any tree whose four pinned files match no entry.
  The connection seam is ``chancy_adapter``'s own (``stepped_chancy``: one stepped connection, ``notifications=False``,
  no default plugins, ``AsyncExecutor``).
* **Phase functions** (JSON arguments only, no SQL or code from a workload):
  - ``nbc_setup``: migrate with the tree's own migrations and declare queue ``q_nbc`` (concurrency 2);
  - ``nbc_work``: ``chancy_adapter.co_work`` (the worker, until the coordinator's stop), plus the outcomes of its own
    statements that failed (SQLSTATE, from the run's records, which the server log corroborates);
  - ``nbc_push_many``: one ``Chancy.push_many`` call (one transaction) of ``ds_job`` jobs with ``unique_key`` ``u<n>``,
    in the given order. An exception from ``push_many`` is the result (class and SQLSTATE), not a crash.
"""

from __future__ import annotations

from typing import Any

from ..stepping import Refused
from . import chancy_adapter as ca

NBC_PINS = {
    # chancy-b8a77df R1: 6716e0db1924d3393e51cd18076415e340b7c508 (package tree 2e8c904d…)
    "git-6716e0d": {"chancy/app.py": "d8ee1573ce425ab92e604448a09b1bd5646bff2204d7bea1d9c672f35e44b26c",
                    "chancy/worker.py": "15b56fb315cda49db0a4e2345c82b133458d8227f611c9d8159f84ecdf40becc",
                    "chancy/migrate.py": "2f19e93779a146ef6b162edf76e2fe174a0405a028d9f38339793ee0213b240e",
                    "chancy/executors/asyncex.py": "6289639519d46a8127fd5f104a960d1411984099c7a71bef7b3c7a451590107d"},
    # chancy-b8a77df R2: b8a77df3a514ce19ebcaf31453cff8da8af9f212 (package tree 306b899b…)
    "git-b8a77df": {"chancy/app.py": "ffdfaf45a54605ac00286524756b56227f4f165cd6fac2a952280281c24f378b",
                    "chancy/worker.py": "4b01a7cacc2fd359bc444724d17cde2ed66743b2cd7a84d06fde54b2088ae711",
                    "chancy/migrate.py": "2f19e93779a146ef6b162edf76e2fe174a0405a028d9f38339793ee0213b240e",
                    "chancy/executors/asyncex.py": "6289639519d46a8127fd5f104a960d1411984099c7a71bef7b3c7a451590107d"},
}
for _v, _pins in NBC_PINS.items():
    if ca.PINNED_BY_VERSION.setdefault(_v, _pins) != _pins:
        raise Refused(f"chancy_adapter already pins {_v} differently")

QUEUE = "q_nbc"
MAX_JOBS = 6


def _own_errors() -> list[dict[str, Any]]:
    from ..stepping_async import _ACTING_FOR
    me = _ACTING_FOR.get()
    if me is None:
        return []
    return [{"seq": op.seq, "kind": op.kind, "sqlstate": op.sqlstate, "error_class": op.error_class,
             "sql_head": " ".join(op.sql.split())[:80]}
            for op in me.run.records if op.actor == me.name and op.outcome in ("error", "aborted")]


async def nbc_setup(conn: Any) -> dict[str, Any]:
    from chancy import Chancy, Queue
    async with ca.stepped_chancy(conn) as chancy:
        await chancy.migrate()
        await chancy.declare(Queue(QUEUE, concurrency=2, executor=Chancy.Executor.Async, polling_interval=1))
    return {"version": ca.installed_version(), "queues": [QUEUE]}


async def nbc_work(conn: Any, *, timeout_s: float = 60) -> dict[str, Any]:
    r = await ca.co_work(conn, timeout_s=timeout_s)
    return {**r, "op_errors": _own_errors()}


async def nbc_push_many(conn: Any, *, ns: list[int]) -> dict[str, Any]:
    if not (isinstance(ns, list) and 1 <= len(ns) <= MAX_JOBS and len(set(ns)) == len(ns)
            and all(isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 9 for n in ns)):
        raise Refused(f"nbc_push_many: ns must be 1..{MAX_JOBS} distinct ints 0..9")
    version = ca.installed_version()
    jobs = [ca.ds_job.job.with_kwargs(n=n).with_queue(QUEUE).with_unique_key(f"u{n}") for n in ns]
    refs: list[str] = []
    error = None
    try:
        async with ca.stepped_chancy(conn) as chancy:
            async for batch in chancy.push_many(jobs):
                refs += [str(r.identifier) for r in batch]
    except Exception as exc:  # the application's own failure is this phase's result
        error = {"class": type(exc).__name__, "sqlstate": getattr(exc, "sqlstate", None), "message": str(exc)[:200]}
    return {"version": version, "requested": [{"n": n, "unique_key": f"u{n}"} for n in ns],
            "refs": [{"n": n, "id": i} for n, i in zip(ns, refs, strict=False)] if error is None else [],
            "error": error, "op_errors": _own_errors()}


PLAN_FUNCTIONS = {"nbc_setup": nbc_setup}
COORDINATED_FUNCTIONS = {"nbc_work": nbc_work, "nbc_push_many": nbc_push_many}
