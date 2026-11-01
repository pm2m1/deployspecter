"""Readable failure reports and replay instructions, built only from recorded traces."""

from __future__ import annotations

from typing import Any

from .replay import FailureArtifact


def timeline(artifact_trace) -> list[dict[str, Any]]:
    return [
        {
            "seq": r.seq, "actor": r.actor, "kind": r.kind, "sql": r.sql, "params": r.params,
            "status": r.status, "rowcount": r.rowcount, "rows": r.rows, "committed": r.committed,
            "blocked_by": r.blocked_by, "sqlstate": r.sqlstate, "error": r.error,
        }
        for r in artifact_trace.records
    ]


def _literal(value: Any) -> str:
    """Display only: this text is for a human to read and is never executed by this tool."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _inline(sql: str, params: dict[str, Any]) -> str:
    for name, value in params.items():
        sql = sql.replace(f"%({name})s", _literal(value))
    return sql


def render_markdown(artifact: FailureArtifact, path: str | None = None) -> str:
    trace, verdict = artifact.trace, artifact.verdict
    lines = [f"# Failure report: {artifact.case_id}", ""]
    lines += [f"Classification: **{verdict.classification}**. Found by `{artifact.spec.generator}`.", ""]
    for v in verdict.violations:
        lines += [f"- **{v.requirement_id}**: {v.requirement}", f"  - {v.explanation}"]
    lines += ["", "## Timeline", "", "| # | Actor | Statement | Result |", "|---|---|---|---|"]
    for r in trace.records:
        sql = _inline(r.sql or r.kind.upper(), r.params)
        outcome = f"rows={r.rowcount}" if r.kind == "sql" else (r.command_tag or "ok")
        result = r.status if r.status != "ok" else outcome
        if r.blocked_by:
            result += f", waited for {', '.join(r.blocked_by)}"
        if r.rows is not None:
            result += f", read {r.rows}"
        lines.append(f"| {r.seq} | {r.actor} | `{sql}` | {result} |")
    lines += ["", "## Final state", "", "```json", str(trace.final_state), "```"]
    lines += ["", "## Oracle assumptions", ""] + [f"- {a}" for a in verdict.assumptions]
    lines += ["", "## Replay", ""]
    if path:
        lines += [f"Automated: `uv run deployspecter replay {path}`", ""]
    lines += ["Manual: open one psql session per actor on a disposable database, apply this setup, then run:", ""]
    lines += ["```sql", artifact.setup_sql.strip()]
    for row in artifact.spec.seed:
        cols = ", ".join(v.column for v in row.values)
        vals = ", ".join(_literal(v.value) for v in row.values)
        lines.append(f"INSERT INTO {row.table} ({cols}) VALUES ({vals});")
    lines += ["```", ""]
    for r in trace.records:
        if r.status.startswith("skipped"):
            continue
        lines.append(f"{r.seq}. session `{r.actor}`: `{_inline(r.sql or r.kind.upper(), r.params)};`")
    return "\n".join(lines) + "\n"
