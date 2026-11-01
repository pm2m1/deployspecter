"""Application operations on project access."""


def grant_access(conn, user_id, project_id, access_level):
    with conn.transaction():
        conn.execute(
            "INSERT INTO project_grants (user_id, project_id, access_level) "
            "VALUES (%(user_id)s, %(project_id)s, %(access_level)s)",
            {"user_id": user_id, "project_id": project_id, "access_level": access_level},
        )


def revoke_access(conn, user_id, project_id, access_level):
    with conn.transaction():
        conn.execute(
            "DELETE FROM project_grants "
            "WHERE user_id = %(user_id)s AND project_id = %(project_id)s AND access_level = %(access_level)s",
            {"user_id": user_id, "project_id": project_id, "access_level": access_level},
        )


def change_access(conn, user_id, project_id, old_level, new_level):
    with conn.transaction():
        conn.execute(
            "UPDATE project_grants SET access_level = %(new_level)s "
            "WHERE user_id = %(user_id)s AND project_id = %(project_id)s AND access_level = %(old_level)s",
            {"user_id": user_id, "project_id": project_id, "old_level": old_level, "new_level": new_level},
        )
