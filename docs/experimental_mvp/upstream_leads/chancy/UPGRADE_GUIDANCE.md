# Chancy 0.25.1 → 0.26.0: is a rolling upgrade with mixed workers supported? (Phase 2C reading; NONBLIND)

Read 2026-11-15, from the public mirror of https://github.com/TkTech/chancy at `v0.26.0`
(`73fd582c0e5d2f8655316e09f189ba0c57d76ca7`) and `v0.25.1`, and from GitHub issues and discussions.

## What the project says

- **CHANGELOG, 0.26.0** ([CHANGELOG.md#L7-L15](https://github.com/TkTech/chancy/blob/73fd582c0e5d2f8655316e09f189ba0c57d76ca7/CHANGELOG.md#L7-L15)):
  > "🚨 This release requires that you run migrations, as it adds new columns for atomic job claims, […] Stop your
  > workers and use `chancy.migrate()` or use the CLI"
- The same entry also says
  ([#L46-L47](https://github.com/TkTech/chancy/blob/73fd582c0e5d2f8655316e09f189ba0c57d76ca7/CHANGELOG.md#L46-L47)):
  > "Apply workflow migrations before restarting workers; the migration temporarily blocks workflow writes."
- And, under fixes:
  > "Fixed cancellation races that could overwrite cancelled results, miss overlapping executions, or interrupt
  > another job."
- **`cancel_job` docstring, 0.25.1** (`chancy/app.py:1132-1133`):
  > "This will attempt to cancel a job that is currently running, if it is possible to do so. Notifications must be
  > enabled for this to work."
- **Documentation at v0.26.0** (`docs/`: howto, faq, design, API reference): no page on deployment, upgrading,
  rolling restarts or running mixed versions. A deployment guide was announced for
  [#41](https://github.com/TkTech/chancy/pull/41), which was closed without merging.
- **Maintainer comment**, [issue #38 "Best practices for migrations + CI/CD helpers for draining"](https://github.com/TkTech/chancy/issues/38)
  (open), 2025-05-31:
  > "we simply set the `desired_count = 0` and each service will scale down to 0, sending the SIGTERM for clean
  > shutdown and once everything is scaled down we run `chancy misc migrate`."

  And on 2026-01-19, about CLI drain helpers for running migrations safely in CI/CD.

## Reading

The documented procedure is **stop every worker, migrate, start the new version**. Nothing says that 0.25.1 and
0.26.0 workers may run at the same time, and nothing says they may not. Our simultaneous runs are therefore
**outside the documented procedure**. That suits a question, not a bug report.

## Related items found (searches: upgrade, migrate, rolling, cancel, mixed)

| Item | State | Relevance |
|---|---|---|
| [#38](https://github.com/TkTech/chancy/issues/38) migrations and draining | open | the closest: the maintainer's deployment practice |
| [#93](https://github.com/TkTech/chancy/issues/93) `shutdown_timeout` not honoured on SIGTERM | closed | draining at shutdown |
| [#105](https://github.com/TkTech/chancy/issues/105) `TaskManager.cancel_all` `KeyError` (0.26.0) | open | cancellation, but a different code path |
| [#84](https://github.com/TkTech/chancy/issues/84) cancelling jobs with a reference | closed | cancellation in `ProcessExecutor`; unrelated to versions |
| [#51](https://github.com/TkTech/chancy/issues/51) `unique_key` race | closed | a claim race; not versions |

No issue or discussion about running mixed versions was found. Discussions are enabled, with a Q&A category.
