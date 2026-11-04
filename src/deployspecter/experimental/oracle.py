"""EXPERIMENTAL independent oracle for the reservations fixture.

It reads the committed state on its own plain psycopg connection (never a stepped one) and checks invariants written
from the specification, not from the handlers' code:

    I1  events.reserved equals the sum of qty over that event's reservations
    I2  events.reserved never exceeds events.capacity
    I3  where reserved_v2 has been backfilled (is not NULL), it equals reserved
    I4  once a backfill job has returned "done", no event still has reserved_v2 NULL

It also gives the canonical state digest used to verify each run's initial state.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA = "mvp_app"
STATE_SQL = {
    "events": f"SELECT id, capacity, reserved, reserved_v2 FROM {SCHEMA}.events ORDER BY id",
    "reservations": f"SELECT id, event_id, holder, qty FROM {SCHEMA}.reservations ORDER BY id",
}


def read_state(conn: Any) -> dict[str, list[dict[str, Any]]]:
    """One REPEATABLE READ snapshot of both tables, as typed Python values."""
    out: dict[str, list[dict[str, Any]]] = {}
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for table, text in STATE_SQL.items():
            cur = conn.execute(text)
            names = [d.name for d in cur.description]
            out[table] = [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    return out


def digest(state: dict[str, list[dict[str, Any]]]) -> str:
    """Typed: an int and its string form differ, and NULL differs from 0."""
    def typed(v: Any) -> Any:
        return [type(v).__name__, v]
    canonical = {t: [{k: typed(v) for k, v in sorted(r.items())} for r in rows] for t, rows in sorted(state.items())}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def expected_initial(seed: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {"events": [{"id": e["id"], "capacity": e["capacity"], "reserved": e["reserved"],
                        "reserved_v2": e.get("reserved_v2")} for e in sorted(seed.get("events", []),
                                                                             key=lambda e: e["id"])],
            "reservations": [{"id": r["id"], "event_id": r["event_id"], "holder": r["holder"], "qty": r["qty"]}
                             for r in sorted(seed.get("reservations", []), key=lambda r: r["id"])]}


def check(state: dict[str, list[dict[str, Any]]], *, backfill_done: bool) -> list[str]:
    """Violations, as 'I<n> event <id>: ...'. An empty list means every invariant holds."""
    sums: dict[int, int] = {}
    for r in state["reservations"]:
        sums[r["event_id"]] = sums.get(r["event_id"], 0) + r["qty"]
    out = []
    for e in state["events"]:
        if e["reserved"] != sums.get(e["id"], 0):
            out.append(f"I1 event {e['id']}: reserved {e['reserved']} but reservations sum to {sums.get(e['id'], 0)}")
        if e["reserved"] > e["capacity"]:
            out.append(f"I2 event {e['id']}: reserved {e['reserved']} exceeds capacity {e['capacity']}")
        if e["reserved_v2"] is not None and e["reserved_v2"] != e["reserved"]:
            out.append(f"I3 event {e['id']}: reserved_v2 {e['reserved_v2']} differs from reserved {e['reserved']}")
        if backfill_done and e["reserved_v2"] is None:
            out.append(f"I4 event {e['id']}: reserved_v2 still NULL after the backfill finished")
    return out
