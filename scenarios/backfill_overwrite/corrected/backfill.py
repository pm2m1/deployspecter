"""Populates display_name for existing profiles, one row per transaction."""

SELECT_BATCH = "SELECT id, legacy_name FROM profiles WHERE display_name IS NULL ORDER BY id LIMIT %(limit)s"


def backfill_display_names(conn, limit):
    with conn.transaction():
        rows = conn.execute(SELECT_BATCH, {"limit": limit}).fetchall()
    for row in rows:
        with conn.transaction():
            conn.execute(
                "UPDATE profiles SET display_name = %(display_name)s WHERE id = %(id)s AND display_name IS NULL",
                {"display_name": row["legacy_name"], "id": row["id"]},
            )
