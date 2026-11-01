"""Fills balance_cents from balance_dollars."""


def resync_balances(conn, limit):
    with conn.transaction():
        rows = conn.execute(
            "SELECT id FROM wallets ORDER BY id LIMIT %(limit)s", {"limit": limit}
        ).fetchall()
    for row in rows:
        with conn.transaction():
            conn.execute(
                "UPDATE wallets SET balance_cents = (balance_dollars * 100)::bigint WHERE id = %(id)s",
                {"id": row["id"]},
            )
