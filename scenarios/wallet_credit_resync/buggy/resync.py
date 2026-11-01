"""Fills balance_cents from balance_dollars."""


def resync_balances(conn, limit):
    with conn.transaction():
        rows = conn.execute(
            "SELECT id, balance_dollars FROM wallets ORDER BY id LIMIT %(limit)s", {"limit": limit}
        ).fetchall()
    for row in rows:
        cents = int(row["balance_dollars"] * 100)
        with conn.transaction():
            conn.execute(
                "UPDATE wallets SET balance_cents = %(cents)s WHERE id = %(id)s",
                {"cents": cents, "id": row["id"]},
            )
