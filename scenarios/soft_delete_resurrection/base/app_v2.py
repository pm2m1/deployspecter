"""Release 2: deletion leaves a tombstone in the new table."""


def delete_user(conn, user_id, email):
    with conn.transaction():
        conn.execute(
            "INSERT INTO users_v2 (id, email, deleted_at) VALUES (%(id)s, %(email)s, now()) "
            "ON CONFLICT (id) DO UPDATE SET deleted_at = now()",
            {"id": user_id, "email": email},
        )
        conn.execute("DELETE FROM users WHERE id = %(id)s", {"id": user_id})
