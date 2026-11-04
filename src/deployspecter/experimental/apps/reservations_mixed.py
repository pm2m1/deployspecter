"""EXPERIMENTAL trusted fixture: a reservations service in the middle of an expand/contract migration.

Not part of any blind corpus: it is written here, with its defect known, to exercise the stepping prototype.

* v1 (old code): one counter, ``events.reserved``.
* Migration "expand": adds ``events.reserved_v2`` (nullable) and a trigger that, once a row has been backfilled, carries
  every later change of ``reserved`` over to ``reserved_v2`` (so old code keeps v2 in sync after the backfill).
* v2 (new code): writes both counters.
* ``backfill_v2``: a background job that fills ``reserved_v2`` from ``reserved`` for rows still NULL.

The defect lives in the buggy backfill: it reads ``reserved`` in Python and writes the value it read in a later
statement. If an old-version reservation commits in between, the trigger sees ``reserved_v2`` still NULL and does
nothing, and the backfill then writes the stale value. The fixed backfill takes row locks when it reads
(``FOR UPDATE``), so the reservation waits and the trigger carries its change over afterwards.

Every handler is an ordinary function ``f(conn, **args)`` using psycopg 3 the way application code does.
"""

from __future__ import annotations

from typing import Any

from ..stepping import Handler, normal_sql, statement_id

SCHEMA = "mvp_app"

SCHEMA_V1 = """
CREATE TABLE events (id integer PRIMARY KEY, capacity integer NOT NULL CHECK (capacity >= 0),
                     reserved integer NOT NULL DEFAULT 0);
CREATE TABLE reservations (id serial PRIMARY KEY, event_id integer NOT NULL REFERENCES events(id),
                           holder text NOT NULL, qty integer NOT NULL CHECK (qty > 0));
"""

MIGRATION_EXPAND = """
ALTER TABLE events ADD COLUMN reserved_v2 integer;
CREATE FUNCTION sync_reserved_v2() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.reserved_v2 IS NOT NULL AND NEW.reserved_v2 IS NOT DISTINCT FROM OLD.reserved_v2 THEN
    NEW.reserved_v2 := OLD.reserved_v2 + (NEW.reserved - OLD.reserved);
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER events_sync_reserved_v2 BEFORE UPDATE OF reserved ON events
  FOR EACH ROW EXECUTE FUNCTION sync_reserved_v2();
"""

PHASES = ("expand",)  # the only phase this fixture models; "pre" and "contract" are refused by the validator

# ------------------------------------------------------------------ the handlers (real application code)

SELECT_V1 = "SELECT capacity, reserved FROM events WHERE id = %s FOR UPDATE"
UPDATE_V1 = "UPDATE events SET reserved = %s WHERE id = %s"
INSERT_RES = "INSERT INTO reservations (event_id, holder, qty) VALUES (%s, %s, %s) RETURNING id"
SELECT_RES = "SELECT event_id, qty FROM reservations WHERE id = %s FOR UPDATE"
DELETE_RES = "DELETE FROM reservations WHERE id = %s"


def reserve_v1(conn: Any, *, event_id: int, holder: str, qty: int) -> dict[str, Any]:
    """Old code: lock the event, check capacity from what it read, write the new counter."""
    with conn.transaction():
        row = conn.execute(SELECT_V1, (event_id,)).fetchone()
        if row is None:
            return {"status": "no_event"}
        capacity, reserved = row
        if reserved + qty > capacity:
            return {"status": "rejected", "available": capacity - reserved}
        conn.execute(UPDATE_V1, (reserved + qty, event_id))
        rid = conn.execute(INSERT_RES, (event_id, holder, qty)).fetchone()[0]
    return {"status": "reserved", "reservation": rid, "reserved_after": reserved + qty}


def release_v1(conn: Any, *, reservation_id: int) -> dict[str, Any]:
    with conn.transaction():
        row = conn.execute(SELECT_RES, (reservation_id,)).fetchone()
        if row is None:
            return {"status": "no_reservation"}
        event_id, qty = row
        _capacity, reserved = conn.execute(SELECT_V1, (event_id,)).fetchone()
        conn.execute(UPDATE_V1, (reserved - qty, event_id))
        conn.execute(DELETE_RES, (reservation_id,))
    return {"status": "released", "reserved_after": reserved - qty}


SELECT_V2 = "SELECT capacity, reserved, reserved_v2 FROM events WHERE id = %s FOR UPDATE"
UPDATE_V2 = "UPDATE events SET reserved = %s, reserved_v2 = %s WHERE id = %s"


def reserve_v2(conn: Any, *, event_id: int, holder: str, qty: int) -> dict[str, Any]:
    """New code: dual-writes both counters (v2 stays NULL until the backfill has reached the row)."""
    with conn.transaction():
        row = conn.execute(SELECT_V2, (event_id,)).fetchone()
        if row is None:
            return {"status": "no_event"}
        capacity, reserved, reserved_v2 = row
        if reserved + qty > capacity:
            return {"status": "rejected", "available": capacity - reserved}
        conn.execute(UPDATE_V2, (reserved + qty, None if reserved_v2 is None else reserved_v2 + qty, event_id))
        rid = conn.execute(INSERT_RES, (event_id, holder, qty)).fetchone()[0]
    return {"status": "reserved", "reservation": rid, "reserved_after": reserved + qty}


def release_v2(conn: Any, *, reservation_id: int) -> dict[str, Any]:
    with conn.transaction():
        row = conn.execute(SELECT_RES, (reservation_id,)).fetchone()
        if row is None:
            return {"status": "no_reservation"}
        event_id, qty = row
        _capacity, reserved, reserved_v2 = conn.execute(SELECT_V2, (event_id,)).fetchone()
        conn.execute(UPDATE_V2, (reserved - qty, None if reserved_v2 is None else reserved_v2 - qty, event_id))
        conn.execute(DELETE_RES, (reservation_id,))
    return {"status": "released", "reserved_after": reserved - qty}


BACKFILL_READ_BUGGY = "SELECT id, reserved FROM events WHERE reserved_v2 IS NULL ORDER BY id LIMIT %s"
BACKFILL_READ_FIXED = "SELECT id, reserved FROM events WHERE reserved_v2 IS NULL ORDER BY id LIMIT %s FOR UPDATE"
BACKFILL_WRITE = "UPDATE events SET reserved_v2 = %s WHERE id = %s AND reserved_v2 IS NULL"


def _backfill(conn: Any, read: str, batch: int) -> dict[str, Any]:
    filled = 0
    while True:
        with conn.transaction():
            rows = conn.execute(read, (batch,)).fetchall()
            for event_id, reserved in rows:  # the value written is the value this job read
                conn.execute(BACKFILL_WRITE, (reserved, event_id))
                filled += 1
        if len(rows) < batch:
            return {"status": "done", "filled": filled}


def backfill_v2_buggy(conn: Any, *, batch: int = 10) -> dict[str, Any]:
    return _backfill(conn, BACKFILL_READ_BUGGY, batch)


def backfill_v2_fixed(conn: Any, *, batch: int = 10) -> dict[str, Any]:
    return _backfill(conn, BACKFILL_READ_FIXED, batch)


# ------------------------------------------------------------------ the trusted registry


def _allow(*texts: str) -> frozenset[str]:
    return frozenset(normal_sql(t) for t in texts)


MIGRATION_TOUCHING = frozenset(statement_id(t) for t in (SELECT_V2, UPDATE_V2, BACKFILL_READ_BUGGY,
                                                         BACKFILL_READ_FIXED, BACKFILL_WRITE))


def registry(variant: str) -> dict[tuple[str, str], Handler]:
    """The handlers a workload may invoke, by (app version, name). ``variant`` selects the backfill build."""
    if variant not in ("buggy", "fixed"):
        raise ValueError("variant must be buggy or fixed")
    reserve_args = {"event_id": int, "holder": str, "qty": int}
    backfill = backfill_v2_buggy if variant == "buggy" else backfill_v2_fixed
    read = BACKFILL_READ_BUGGY if variant == "buggy" else BACKFILL_READ_FIXED
    handlers = [
        Handler("v1", "reserve", reserve_v1, _allow(SELECT_V1, UPDATE_V1, INSERT_RES), reserve_args),
        Handler("v1", "release", release_v1, _allow(SELECT_RES, SELECT_V1, UPDATE_V1, DELETE_RES),
                {"reservation_id": int}),
        Handler("v2", "reserve", reserve_v2, _allow(SELECT_V2, UPDATE_V2, INSERT_RES), reserve_args,
                MIGRATION_TOUCHING),
        Handler("v2", "release", release_v2, _allow(SELECT_RES, SELECT_V2, UPDATE_V2, DELETE_RES),
                {"reservation_id": int}, MIGRATION_TOUCHING),
        Handler("job", "backfill_v2", backfill, _allow(read, BACKFILL_WRITE), {"batch": int}, MIGRATION_TOUCHING),
    ]
    return {(h.app_version, h.name): h for h in handlers}


# ------------------------------------------------------------------ resetting to a verified initial state


def reset(conn: Any, seed: dict[str, list[dict[str, Any]]]) -> None:
    """Rebuild the schema at the expand phase and insert the seed. Runs on a plain admin connection, not stepped."""
    with conn.transaction():
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {SCHEMA}")
        conn.execute(f"SET LOCAL search_path = {SCHEMA}")
        conn.execute(SCHEMA_V1)
        conn.execute(MIGRATION_EXPAND)
        for e in seed.get("events", []):
            conn.execute("INSERT INTO events (id, capacity, reserved, reserved_v2) VALUES (%s, %s, %s, %s)",
                         (e["id"], e["capacity"], e["reserved"], e.get("reserved_v2")))
        for r in seed.get("reservations", []):
            conn.execute("INSERT INTO reservations (id, event_id, holder, qty) VALUES (%s, %s, %s, %s)",
                         (r["id"], r["event_id"], r["holder"], r["qty"]))
        conn.execute("SELECT setval('reservations_id_seq', greatest((SELECT max(id) FROM reservations), 0) + 1, "
                     "false)")
