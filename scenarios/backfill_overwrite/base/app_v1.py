"""Application release 1: only knows about legacy_name."""


def update_legacy_name(conn, user_id, name):
    with conn.transaction():
        conn.execute(
            "UPDATE profiles SET legacy_name = %(name)s WHERE id = %(id)s",
            {"name": name, "id": user_id},
        )
