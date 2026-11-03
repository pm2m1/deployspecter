"""A tiny application under test. It knows nothing about DeploySpecter and takes any psycopg connection.

``reserve_seats`` and ``release_seats`` read a count, compute a new one in Python, and write it back. With
``lock_row=True`` they read with FOR UPDATE; without it they have a lost update. They deliberately use two different
driver styles: ``with conn.transaction()`` and ``conn.execute``, and a cursor with ``dict_row`` and ``conn.commit()``.
"""

from __future__ import annotations

from psycopg.rows import dict_row

SETUP_SQL = """
CREATE TABLE events (
    id integer PRIMARY KEY,
    capacity integer NOT NULL,
    reserved integer NOT NULL
);
CREATE TABLE reservations (
    id serial PRIMARY KEY,
    event_id integer NOT NULL REFERENCES events (id),
    holder text NOT NULL,
    qty integer NOT NULL
);
CREATE TABLE rejections (
    id serial PRIMARY KEY,
    event_id integer NOT NULL,
    holder text NOT NULL,
    seats_available integer NOT NULL
);
"""


def reserve_seats(conn, event_id: int, holder: str, qty: int, lock_row: bool = False) -> dict:
    lock = " FOR UPDATE" if lock_row else ""
    with conn.transaction():
        capacity, reserved = conn.execute(
            f"SELECT capacity, reserved FROM events WHERE id = %s{lock}", (event_id,)
        ).fetchone()
        available = capacity - reserved
        if qty > available:
            conn.execute(
                "INSERT INTO rejections (event_id, holder, seats_available) VALUES (%s, %s, %s)",
                (event_id, holder, available),
            )
            return {"status": "rejected", "available": available}
        conn.execute("UPDATE events SET reserved = %s WHERE id = %s", (reserved + qty, event_id))
        conn.execute("INSERT INTO reservations (event_id, holder, qty) VALUES (%s, %s, %s)", (event_id, holder, qty))
    return {"status": "reserved", "available": available - qty}


def release_seats(conn, holder: str, lock_row: bool = False) -> dict:
    lock = " FOR UPDATE" if lock_row else ""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id, event_id, qty FROM reservations WHERE holder = %s ORDER BY id", (holder,))
        held = cur.fetchone()
        if held is None:
            conn.rollback()
            return {"status": "nothing_held"}
        cur.execute(f"SELECT reserved FROM events WHERE id = %s{lock}", (held["event_id"],))
        reserved = cur.fetchone()["reserved"]
        cur.execute("UPDATE events SET reserved = %s WHERE id = %s", (reserved - held["qty"], held["event_id"]))
        cur.execute("DELETE FROM reservations WHERE id = %s", (held["id"],))
    conn.commit()
    return {"status": "released", "released": held["qty"]}
