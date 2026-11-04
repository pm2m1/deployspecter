"""Gate A2.2, B5 analysis: predicting how psycopg's automatic preparation renames a session's logged statements.

Research-only and offline. Nothing here changes how any connection behaves, and the audit doesn't use it: the template
matcher refuses every prepared-statement name. This module asks whether a *future* reviewed audit could instead
predict each name exactly, rather than allowlist names.

It drives psycopg's own ``PrepareManager`` and query conversion (psycopg 3.3.6, a private module, pinned by version
here) over a sequence of statements, feeding back only what the log implies about each result: how many results, and
whether it succeeded. It reproduces psycopg's per-key counting, its ``_pg3_N`` naming, its cache clearing after
DROP/ALTER/ROLLBACK (which sends ``DEALLOCATE ALL``) and its refusal to prepare multi-statement queries. Keys include
the parameter types psycopg's dumpers choose, so an integer's size changes the key.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

PSYCOPG_VERSION = "3.3.6"


@dataclass(frozen=True)
class Op:
    query: str  # as the client sends it, with %s placeholders
    params: tuple[Any, ...] | None = None
    results: int = 1  # a multi-statement query returns several
    ok: bool = True
    command: str = "SELECT 1"  # the command tag, e.g. "DROP SCHEMA"; only its first words matter to psycopg


class _Result:
    def __init__(self, ok: bool, command: str, tuples: bool) -> None:
        from psycopg import pq
        self.status = (pq.ExecStatus.TUPLES_OK if tuples else pq.ExecStatus.COMMAND_OK) if ok \
            else pq.ExecStatus.FATAL_ERROR
        self.command_status = command.encode()


class PreparedSession:
    """One connection's preparation state, statement by statement."""

    def __init__(self, threshold: int | None = 5, prepared_max: int = 100) -> None:
        import psycopg
        from psycopg._preparing import PrepareManager

        if psycopg.__version__ != PSYCOPG_VERSION:
            raise RuntimeError(f"the model is pinned to psycopg {PSYCOPG_VERSION}, not {psycopg.__version__}")
        self.manager = PrepareManager()
        self.manager.prepare_threshold, self.manager.prepared_max = threshold, prepared_max

    def execute(self, op: Op) -> list[tuple[str, str]]:
        """(verb, server text) for what this execution logs, DEALLOCATE included."""
        from psycopg._preparing import Prepare
        from psycopg._queries import PostgresQuery
        from psycopg.adapt import Transformer

        manager = self.manager
        pgq = PostgresQuery(Transformer())
        pgq.convert(op.query, op.params)
        text = pgq.query.decode()
        prep, name = manager.get(pgq)
        out = [("execute <unnamed>" if pgq.params else "statement", text) if prep is Prepare.NO
               else (f"execute {name.decode()}", text)]
        key = manager.maybe_add_to_cache(pgq, prep, name)
        results = [_Result(op.ok, op.command, op.command.startswith("SELECT")) for _ in range(op.results)]
        if key is not None:
            manager.validate(key, prep, name, results)
        while manager._to_flush:
            flushed = manager._to_flush.popleft()
            out.append(("statement", "DEALLOCATE ALL" if flushed is None else f"DEALLOCATE {flushed.decode()}"))
        return out


def predict(ops: Iterable[Op], threshold: int | None = 5, prepared_max: int = 100) -> list[tuple[str, str]]:
    """(verb, server text) for every statement the session logs, DEALLOCATE included, in order."""
    session = PreparedSession(threshold, prepared_max)
    return [entry for op in ops for entry in session.execute(op)]
