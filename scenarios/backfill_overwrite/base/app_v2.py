"""Application release 2: users manage display_name directly."""


def update_display_name(conn, user_id, display_name):
    with conn.transaction():
        conn.execute(
            "UPDATE profiles SET display_name = %(display_name)s WHERE id = %(id)s",
            {"display_name": display_name, "id": user_id},
        )
