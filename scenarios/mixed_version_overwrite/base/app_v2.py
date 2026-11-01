"""Release 2: self-service plan changes, recording who made the change."""


def change_plan(conn, account_id, plan, actor):
    with conn.transaction():
        conn.execute(
            "UPDATE accounts SET plan = %(plan)s, plan_changed_by = %(actor)s WHERE id = %(id)s",
            {"plan": plan, "actor": actor, "id": account_id},
        )
