"""EXPERIMENTAL corrected reference for Gate 2A's synthetic reservations app (``tests/live_app/reservations.py``).

The frozen fixture is not modified. Its two builds stay as they are: ``buggy`` (no locks) and ``fixed``
(``lock_row=True``, which locks the event row only). This module is a third, separately named build, ``corrected``.
It is a reference written after the root cause was established live (see ``docs/experimental_mvp/RELEASE_PATH.md``).

Root cause in ``release_seats`` (both frozen builds):

* it reads the holder's reservation without a lock, so two concurrent releases by one holder can both choose the same
  reservation;
* it then decrements ``events.reserved`` and deletes the reservation without checking the DELETE removed a row, so the
  second release decrements for a reservation that is already gone.

A serial duplicate release is correct (the second finds nothing); only a concurrent one fails. ``lock_row`` serializes
the two decrements but doesn't stop the second.

The correction, and nothing else:

1. ``SELECT ... FOR UPDATE`` on the holder's reservations. A release waiting on that lock re-reads after the other
   commits, so it sees the deletion and takes the holder's next reservation, or none.
2. The event row is locked as in ``lock_row=True``.
3. The reservation is deleted first, and the counter changes only if exactly one row was deleted. Otherwise the
   transaction rolls back and the conflict is raised, never hidden.

``reserve_seats`` is the frozen fixture's own function with ``lock_row=True``, which is already correct for reserving.
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

RELEASE_SELECT_RESERVATION = "SELECT id, event_id, qty FROM reservations WHERE holder = %s ORDER BY id FOR UPDATE"
RELEASE_SELECT_EVENT = "SELECT reserved FROM events WHERE id = %s FOR UPDATE"
RELEASE_DELETE = "DELETE FROM reservations WHERE id = %s"
RELEASE_UPDATE = "UPDATE events SET reserved = %s WHERE id = %s"


class ReleaseConflict(RuntimeError):
    """The reservation this release locked was not there to delete. Raised after rolling back."""


def release_seats(conn: Any, holder: str) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(RELEASE_SELECT_RESERVATION, (holder,))
        held = cur.fetchone()
        if held is None:
            conn.rollback()
            return {"status": "nothing_held"}
        cur.execute(RELEASE_SELECT_EVENT, (held["event_id"],))
        reserved = cur.fetchone()["reserved"]
        cur.execute(RELEASE_DELETE, (held["id"],))
        if cur.rowcount != 1:
            conn.rollback()
            raise ReleaseConflict(f"reservation {held['id']} was already gone")
        cur.execute(RELEASE_UPDATE, (reserved - held["qty"], held["event_id"]))
    conn.commit()
    return {"status": "released", "released": held["qty"]}


ALLOWED_RELEASE = [RELEASE_SELECT_RESERVATION, RELEASE_SELECT_EVENT, RELEASE_DELETE, RELEASE_UPDATE]
