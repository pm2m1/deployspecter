# The root suite with a database (2026-11-07)

**Safe?** Yes, under these conditions:
- `DATABASE_URL` points at the **disposable** cluster: 127.0.0.1:55441, database `ds_mvp`, with `cluster_name` checked
  as `ds_mvp_disposable` before any test runs (`/tmp`-only wrapper script; the password is never printed);
- the repository has no `.env`;
- the root `Sandbox` refuses any database not named `*_test`, and creates `deployspecter_test` in that cluster;
- the protected 55432 instance was never involved.

**Attempt 1 (`root_suite_db_1`)** exited 1: 1904 passed, 164 skipped, 1 failed. The failure was a defect from this
sprint: `test_every_research_module_and_tool_imports_offline` found that two new probe scripts
(`probe_jsonb_logging.py`, `capture_jsonb_out.py`) read the password file at import time. Both now run only under
`__main__`.

**Attempt 2 (`root_suite_db_2`): exit 0, 1905 passed, 164 skipped, 0 failed.** The run wrote nothing into the
repository, and no password appears in any log.

**The 164 skips are an environment limitation:**
- 163 tests need the separate live-stepping logging cluster `ds_live_disposable` (with its own required logging
  settings). That is the Gate 2B environment, which needs the owner's separate approval, so it wasn't started.
- 1 collection skip: the optional `langgraph` dependency isn't installed.

Without `DATABASE_URL` (the standard acceptance run), the same suite still reports 1746 passed and 322 that can't run.
