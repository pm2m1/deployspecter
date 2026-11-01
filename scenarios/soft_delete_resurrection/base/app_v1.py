"""Release 1: reads and updates the original table only."""


def update_email(conn, user_id, email):
    with conn.transaction():
        conn.execute(
            "UPDATE users SET email = %(email)s WHERE id = %(id)s",
            {"email": email, "id": user_id},
        )
