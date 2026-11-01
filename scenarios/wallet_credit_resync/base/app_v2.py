"""Release 2: credits keep both balance columns in step."""


def credit(conn, wallet_id, amount_cents):
    with conn.transaction():
        conn.execute(
            "UPDATE wallets SET balance_dollars = balance_dollars + %(amount_cents)s::numeric / 100, "
            "balance_cents = balance_cents + %(amount_cents)s::bigint WHERE id = %(id)s",
            {"amount_cents": amount_cents, "id": wallet_id},
        )
