"""Release 2: stores a canonical address, for example with plus-tags removed."""


def set_normalized_email(conn, customer_id, normalized):
    with conn.transaction():
        conn.execute(
            "UPDATE customers SET email_normalized = %(normalized)s WHERE id = %(id)s",
            {"normalized": normalized, "id": customer_id},
        )
