"""EXPERIMENTAL ds-p9x/1: write a Phase 9B-proven state to the target schema, then verify it independently.

``physical_plan`` turns a PROVEN report into the rows to insert and their order, with the same rules as the
reservations pipeline:
* ``ORIGINAL_VALID``: the seed order;
* ``CONSTRUCTED_EQUIVALENT``: the plan's insert steps;
* ``RECONCILED_EQUIVALENT``: the effective subsequence.
The plan and its sha256 are recorded.

``write_and_verify`` does everything in **one transaction** on the target, which is rolled back on any problem, so
a refused state leaves nothing behind:
1. lock every table of the schema (SHARE ROW EXCLUSIVE: no DDL, no concurrent writes);
2. re-take the catalog fingerprint and refuse unless it is the planning one;
3. snapshot every table (``to_jsonb``, UTC);
4. insert the planned rows in order with exact typed parameters, never disabling a trigger;
5. snapshot again, and refuse unless the change is exactly the constructor's proven final state;
6. commit.

The target's own triggers run normally, so their effects are part of what step 5 compares. Phase 9B's label is never
taken on trust. ``readback`` then re-reads, on a **separate session**:
* every requested row exactly (``typed.verify``);
* every jsonb cell under contract jsonb/1 (``IS NULL``, ``::text``, ``jsonb_typeof``);
* the whole schema's change again.
"""

from __future__ import annotations

import collections
import hashlib
import json
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg import sql

from deployspecter.experimental.state_construction import typed
from deployspecter.experimental.state_construction.typed import TV

from . import catalog, jsonb_v1, loader, provenance


class ExecutionRefused(RuntimeError):
    """The target can't receive this state, or didn't end up in it. Everything was rolled back."""


def canonical_row(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True)


def plain(tv: TV) -> Any:
    """The scalar Phase 9B's ``SeedRow`` carries for a typed value (jsonb: its canonical jsonb/1 text)."""
    return None if tv.tag == "null" else tv.value


def physical_plan(report: dict[str, Any], seed_plain: list[dict[str, Any]]) -> list[int]:
    cls = report["classification"]
    if cls not in loader.PROVEN:
        raise ExecutionRefused(f"{cls} isn't proven; nothing is written")
    if cls == "ORIGINAL_VALID":
        return list(range(len(seed_plain)))
    if cls == "CONSTRUCTED_EQUIVALENT":
        return [s["row_index"] for s in report["plan"] if s["action"] == "insert"]
    out, i = [], 0
    for eff in report["effective_seed"]:
        eff_plain = {v["column"]: v["value"] for v in eff["values"]}
        while i < len(seed_plain) and canonical_row(seed_plain[i]) != canonical_row(eff_plain):
            i += 1
        if i == len(seed_plain):
            raise ExecutionRefused("the reconciled seed isn't a subsequence of the requested rows")
        out.append(i)
        i += 1
    return out


def plan_record(rows: list[tuple[str, dict[str, TV]]], order: list[int], fingerprint_sha: str, setup_sha: str,
                report: dict[str, Any]) -> dict[str, Any]:
    body = {"identity": loader.identity(), "catalog_fingerprint_sha256": fingerprint_sha,
            "setup_sql_sha256": setup_sha, "classification": report["classification"],
            "constructor_final_snapshot_sha256": hashlib.sha256(json.dumps(report["final_snapshot"],
                                                                           sort_keys=True).encode()).hexdigest(),
            "inserts": [{"row": i, "table": rows[i][0], "values": {c: tv.to_dict() for c, tv in rows[i][1].items()}}
                        for i in order]}
    return body | {"sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()}


def snapshot(conn: Any, schema: str) -> dict[str, list[str]]:
    tables = [r[0] for r in conn.execute("SELECT tablename::text FROM pg_tables WHERE schemaname = %s ORDER BY 1",
                                         (schema,)).fetchall()]
    return {t: sorted(canonical_row(r[0]) for r in conn.execute(
        sql.SQL("SELECT to_jsonb(t) FROM {}.{} t").format(sql.Identifier(schema), sql.Identifier(t))).fetchall())
        for t in tables}


def delta(before: dict[str, list[str]], after: dict[str, list[str]]) -> tuple[dict[str, list[str]],
                                                                               dict[str, list[str]]]:
    added, removed = {}, {}
    for t in sorted(set(before) | set(after)):
        b, a = collections.Counter(before.get(t, [])), collections.Counter(after.get(t, []))
        if x := sorted((a - b).elements()):
            added[t] = x
        if x := sorted((b - a).elements()):
            removed[t] = x
    return added, removed


def expected(final_snapshot: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    return {t: sorted(canonical_row(r) for r in rows) for t, rows in final_snapshot.items() if rows}


STAGES = ("before_writes", "on_insert", "before_verify", "after_verify")


def write_and_verify(conninfo: dict[str, Any], schema: str, rows: list[tuple[str, dict[str, TV]]], order: list[int],
                     final_snapshot: dict[str, list[dict[str, Any]]], planned_fingerprint: str, *,
                     on_insert: Callable[[int], None] | None = None,
                     hooks: dict[str, Callable[..., None]] | None = None, audit: bool = True) -> dict[str, Any]:
    """Steps 1–6 of the module docstring, on a journaled session whose provenance is reconciled with the server log
    (``provenance``; assurance A3). Returns the baseline snapshot, what changed and ``provenance``; raises
    ExecutionRefused (after rolling back) otherwise, with ``.provenance`` attached.

    ``hooks`` are test hooks for assurance A4, keyed by ``STAGES``: ``before_writes`` (after the fingerprint check),
    ``on_insert(k)`` (after each insert), ``before_verify`` (after the inserts), ``after_verify`` (after the change
    check, before COMMIT). ``on_insert`` is kept for the earlier tests."""
    hooks = dict(hooks or {})
    if on_insert is not None:
        hooks["on_insert"] = on_insert
    holder: list[provenance.Journal] = []
    aud = provenance.Audit(conninfo) if audit else None
    prov: dict[str, Any] | None = None
    try:
        if aud is not None:
            aud.open()
        try:
            result = _write(conninfo, schema, rows, order, final_snapshot, planned_fingerprint, hooks, holder)
        finally:
            if aud is not None:
                try:
                    j = holder[0] if holder else None
                    data = aud.close(j.pid_at_start if j else None)
                    prov = provenance.reconcile(
                        data, start=aud.start, end=aud.end, journal=j.entries if j else [],
                        construct_pid=j.pid_at_start if j else -1, audit_pid=aud.pid, user=conninfo["user"],
                        database=conninfo["dbname"])
                    if j is not None:
                        prov["journal"] = j.entries
                except Exception as exc:  # noqa: BLE001 - an audit that can't run is recorded, never passed
                    prov = {"ok": False, "problems": [f"the audit could not run: {type(exc).__name__}: {exc}"]}
    except BaseException as exc:
        exc.provenance = prov  # type: ignore[attr-defined]
        raise
    finally:
        if aud is not None:
            aud.shutdown()
    result["provenance"] = prov
    return result


def _write(conninfo: dict[str, Any], schema: str, rows: list[tuple[str, dict[str, TV]]], order: list[int],
           final_snapshot: dict[str, list[dict[str, Any]]], planned_fingerprint: str,
           hooks: dict[str, Callable[..., None]], holder: list[Any]) -> dict[str, Any]:
    want = expected(final_snapshot)

    def hook(stage: str, *args: Any) -> None:
        if stage in hooks:
            hooks[stage](*args)
    with psycopg.connect(**conninfo, autocommit=True, application_name=provenance.APPLICATION,
                         prepare_threshold=None) as raw:
        j = provenance.Journal(raw)
        j.pid_at_start = raw.info.backend_pid  # type: ignore[attr-defined]
        holder.append(j)
        try:
            with j.transaction():
                j.execute("SET LOCAL TIME ZONE 'UTC'")
                # the schema's own functions (triggers, defaults) resolve unqualified names through search_path, as
                # they do for the application; Phase 9B's sandbox runs them with search_path = ds_run likewise
                j.execute("SELECT set_config('search_path', %s, true)", (schema,))
                tables = [r[0] for r in j.execute(
                    "SELECT tablename::text FROM pg_tables WHERE schemaname = %s ORDER BY 1", (schema,)).fetchall()]
                for t in tables:
                    j.execute(sql.SQL("LOCK TABLE {}.{} IN SHARE ROW EXCLUSIVE MODE").format(
                        sql.Identifier(schema), sql.Identifier(t)))
                now = catalog.fingerprint(j, schema)["sha256"]
                if now != planned_fingerprint:
                    raise ExecutionRefused(f"the catalog changed between planning and execution ({now[:12]} vs "
                                           f"{planned_fingerprint[:12]})")
                before = snapshot(j, schema)
                hook("before_writes")
                for k, i in enumerate(order):
                    table, vals = rows[i]
                    cols = list(vals)
                    j.execute(sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
                        sql.Identifier(schema), sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)),
                        sql.SQL(", ").join(typed.placeholder(vals[c]) for c in cols)),
                        [typed.param(vals[c]) for c in cols], kind="construction",
                        casts=[vals[c].tag if vals[c].tag in ("uuid", "timestamptz", "json", "jsonb") else None
                               for c in cols])
                    hook("on_insert", k)
                hook("before_verify")
                added, removed = delta(before, snapshot(j, schema))
                if removed:
                    raise ExecutionRefused(f"rows disappeared from the target: {sorted(removed)}")
                if added != want:
                    raise ExecutionRefused("the target's change isn't the constructor's proven final state: "
                                           f"tables {sorted(added)} vs {sorted(want)}; "
                                           f"first difference {_first_difference(added, want)}")
                hook("after_verify")
        except psycopg.Error as exc:
            raise ExecutionRefused(f"a write failed and was rolled back: {str(exc).splitlines()[0]}") from exc
    return {"baseline": before, "added": added, "tables_locked": tables, "fingerprint_at_write": now}


def _first_difference(a: dict[str, list[str]], b: dict[str, list[str]]) -> Any:
    for t in sorted(set(a) | set(b)):
        x, y = a.get(t, []), b.get(t, [])
        if x != y:
            extra = sorted(set(x) - set(y))[:1]
            missing = sorted(set(y) - set(x))[:1]
            return {"table": t, "target_only": extra, "constructor_only": missing}
    return None


def readback(conninfo: dict[str, Any], schema: str, rows: list[tuple[str, dict[str, TV]]],
             baseline: dict[str, list[str]], final_snapshot: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """The independent check, on a new session. Returns ``{"problems": [...], ...}``; empty problems = verified."""
    problems: list[str] = []
    jsonb_cells = 0
    want = expected(final_snapshot)
    with psycopg.connect(**conninfo, autocommit=True, application_name="ds_p9x_readback") as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        # typed.verify reads a jsonb JSON null as psycopg's None, i.e. as SQL NULL, so jsonb columns are left to
        # jsonb/1's own readback below (IS NULL, ::text, jsonb_typeof); the whole-row change check covers them too
        by_table: dict[str, list[dict[str, TV]]] = {}
        for t, vals in rows:
            by_table.setdefault(t, []).append({c: tv for c, tv in vals.items()
                                               if tv.tag != "jsonb" and not _is_jsonb(conn, schema, t, c)})
        for t, rs in by_table.items():
            count = len(baseline.get(t, [])) + len(want.get(t, []))
            problems += typed.verify(conn, schema, t, rs, expect_count=count)
        for i, (t, vals) in enumerate(rows):
            keys = {c: tv for c, tv in vals.items() if tv.tag not in ("json", "jsonb")}
            for c, tv in vals.items():
                if tv.tag != "jsonb" and not (tv.tag == "null" and _is_jsonb(conn, schema, t, c)):
                    continue
                jsonb_cells += 1
                where = sql.SQL(" AND ").join(
                    sql.SQL("{} IS NOT DISTINCT FROM {}").format(sql.Identifier(k), typed.placeholder(v))
                    for k, v in keys.items())
                got = conn.execute(sql.SQL("SELECT {c} IS NULL, {c}::text, jsonb_typeof({c}) FROM {s}.{t} WHERE {w}")
                                   .format(c=sql.Identifier(c), s=sql.Identifier(schema), t=sql.Identifier(t),
                                           w=where), [typed.param(v) for v in keys.values()]).fetchall()
                if len(got) != 1:
                    problems.append(f"row {i} {t}: {len(got)} rows match its non-jsonb columns")
                    continue
                problems += [f"row {i} {t}.{c}: {p}" for p in
                             jsonb_v1.readback(None if tv.tag == "null" else tv.value, *got[0])]
        added, removed = delta(baseline, snapshot(conn, schema))
        if removed or added != want:
            problems.append("after commit, the schema's change isn't the constructor's proven final state")
    return {"problems": problems, "jsonb_cells_checked": jsonb_cells,
            "rows_checked": len(rows), "verified": not problems}


def _is_jsonb(conn: Any, schema: str, table: str, column: str) -> bool:
    return conn.execute("SELECT data_type = 'jsonb' FROM information_schema.columns WHERE table_schema = %s AND "
                        "table_name = %s AND column_name = %s", (schema, table, column)).fetchone()[0]
