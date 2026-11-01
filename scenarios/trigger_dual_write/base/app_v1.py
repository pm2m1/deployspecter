"""Release 1: unaware of email_normalized."""


def update_phone(conn, customer_id, phone):
    with conn.transaction():
        conn.execute(
            "UPDATE customers SET phone = %(phone)s WHERE id = %(id)s",
            {"phone": phone, "id": customer_id},
        )


def update_email(conn, customer_id, email):
    with conn.transaction():
        conn.execute(
            "UPDATE customers SET email = %(email)s WHERE id = %(id)s",
            {"email": email, "id": customer_id},
        )
