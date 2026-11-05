"""EXPERIMENTAL mutants of the corrected ``release_seats`` (``reservations_corrected.py``), for measuring which
invariants distinguish which faults. Each mutant is a separately named function; no source is mutated in place.

* ``no_reservation_lock``: the reservation is read without ``FOR UPDATE``; the delete guard stays.
* ``no_guard``: the counter changes even when the DELETE removed nothing; the locks stay. Under these locks the guard
  never fires, so this mutant is expected to be equivalent (indistinguishable by any final-state invariant).
* ``no_event_lock``: the event row is read without ``FOR UPDATE``; a concurrent change to the counter can be lost.
* ``off_by_one``: releases one seat fewer than the reservation held (when it held more than one).
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from deployspecter.experimental.apps.reservations_corrected import (
    RELEASE_DELETE,
    RELEASE_SELECT_EVENT,
    RELEASE_SELECT_RESERVATION,
    RELEASE_UPDATE,
    ReleaseConflict,
)

SELECT_RESERVATION_UNLOCKED = "SELECT id, event_id, qty FROM reservations WHERE holder = %s ORDER BY id"
SELECT_EVENT_UNLOCKED = "SELECT reserved FROM events WHERE id = %s"


def _release(conn: Any, holder: str, *, res_sql: str, event_sql: str, guard: bool, delta: int) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(res_sql, (holder,))
        held = cur.fetchone()
        if held is None:
            conn.rollback()
            return {"status": "nothing_held"}
        cur.execute(event_sql, (held["event_id"],))
        reserved = cur.fetchone()["reserved"]
        cur.execute(RELEASE_DELETE, (held["id"],))
        if guard and cur.rowcount != 1:
            conn.rollback()
            raise ReleaseConflict(f"reservation {held['id']} was already gone")
        released = held["qty"] - delta if held["qty"] > 1 else held["qty"]
        cur.execute(RELEASE_UPDATE, (reserved - released, held["event_id"]))
    conn.commit()
    return {"status": "released", "released": held["qty"]}


def release_no_reservation_lock(conn: Any, holder: str) -> dict[str, Any]:
    return _release(conn, holder, res_sql=SELECT_RESERVATION_UNLOCKED, event_sql=RELEASE_SELECT_EVENT, guard=True,
                    delta=0)


def release_no_guard(conn: Any, holder: str) -> dict[str, Any]:
    return _release(conn, holder, res_sql=RELEASE_SELECT_RESERVATION, event_sql=RELEASE_SELECT_EVENT, guard=False,
                    delta=0)


def release_no_event_lock(conn: Any, holder: str) -> dict[str, Any]:
    return _release(conn, holder, res_sql=RELEASE_SELECT_RESERVATION, event_sql=SELECT_EVENT_UNLOCKED, guard=True,
                    delta=0)


def release_off_by_one(conn: Any, holder: str) -> dict[str, Any]:
    return _release(conn, holder, res_sql=RELEASE_SELECT_RESERVATION, event_sql=RELEASE_SELECT_EVENT, guard=True,
                    delta=1)


MUTANTS = {"no_reservation_lock": release_no_reservation_lock, "no_guard": release_no_guard,
           "no_event_lock": release_no_event_lock, "off_by_one": release_off_by_one}
ALLOWED = [RELEASE_SELECT_RESERVATION, SELECT_RESERVATION_UNLOCKED, RELEASE_SELECT_EVENT, SELECT_EVENT_UNLOCKED,
           RELEASE_DELETE, RELEASE_UPDATE]
