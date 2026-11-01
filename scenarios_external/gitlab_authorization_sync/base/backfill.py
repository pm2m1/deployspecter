"""Copies existing access rows into the compact table, one batch per transaction."""


def backfill_batch(conn, first_user_id, last_user_id):
    with conn.transaction():
        conn.execute(
            "INSERT INTO project_grants_compact (user_id, project_id, access_level) "
            "SELECT user_id, project_id, min(access_level) FROM project_grants "
            "WHERE user_id BETWEEN %(first_user_id)s AND %(last_user_id)s "
            "GROUP BY user_id, project_id "
            "ON CONFLICT (user_id, project_id) DO NOTHING",
            {"first_user_id": first_user_id, "last_user_id": last_user_id},
        )
