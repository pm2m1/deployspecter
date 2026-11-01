# Upstream evidence

This fixture is an independent, minimal model of a defect published by GitLab. It is not GitLab's schema, services, locking or workload, and table names differ on purpose.

| Fact | Source |
|---|---|
| The source table can hold several rows per user and project, one per access level. The new table holds one per pair | [MR !246505](https://gitlab.com/gitlab-org/gitlab/-/merge_requests/246505) description; primary keys in GitLab's `db/structure.sql` |
| The trigger deleted the pair when any one access level row was deleted, even if another remained | MR !246505 description and removed code |
| On GitLab.com this was triggered by two permission refreshes racing, and a row went missing | MR !246505 description (GitLab reports this; it is not reproduced here) |
| The backfill uses `ON CONFLICT DO NOTHING`, so it does not repair a surviving row until it is re-run | MR !246505 description; [MR !246509](https://gitlab.com/gitlab-org/gitlab/-/merge_requests/246509) requeues it |
| The fix recomputes on delete from remaining rows in one statement, using the minimum level, and deletes only if none remain | MR !246505 diff |
| Insert and update still write the new row's level, not the minimum | MR !246505 diff and regression spec |
| The trigger is `AFTER INSERT OR DELETE OR UPDATE ... FOR EACH ROW` | GitLab's `db/structure.sql` |

`corrected/` keeps the statement shape of GitLab's fix.

Results from this fixture describe SQL in isolation. They are not evidence about GitLab's production system, which has its own locks and workers.

## Comparison with the upstream SQL

- **Same:** `AFTER` row trigger timing, source and destination primary keys, the single-statement delete branch of the fix, and the unconditional delete of the original.
- **Different, by design:** table names, the order of the trigger's branches, the `MIN` written with `HAVING` rather than `GROUP BY`, and no `is_unique` column.
