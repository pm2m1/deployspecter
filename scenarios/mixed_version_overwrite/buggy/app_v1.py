"""Release 1: account settings page."""


def change_email(conn, account_id, email):
    with conn.transaction():
        row = conn.execute(
            "SELECT id, email, plan FROM accounts WHERE id = %(id)s", {"id": account_id}
        ).fetchone()
        conn.execute(
            "UPDATE accounts SET email = %(email)s, plan = %(plan)s WHERE id = %(id)s",
            {**row, "email": email},
        )
