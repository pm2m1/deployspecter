"""Copies users into users_v2. Safe to run repeatedly."""


def copy_users(conn, limit):
    with conn.transaction():
        rows = conn.execute(
            "SELECT id, email FROM users ORDER BY id LIMIT %(limit)s", {"limit": limit}
        ).fetchall()
    for row in rows:
        with conn.transaction():
            conn.execute(
                "INSERT INTO users_v2 (id, email) VALUES (%(id)s, %(email)s) "
                "ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email, deleted_at = NULL",
                {"id": row["id"], "email": row["email"]},
            )
