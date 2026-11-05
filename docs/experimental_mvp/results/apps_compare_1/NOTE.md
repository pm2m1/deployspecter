# Failed attempt, kept as a record

The first Muse comparison on the ledger and planner targets (2026-11-05). Two ledger calls completed (about $0.068 estimated, in `muse_ledger.jsonl`). The first planner call hit the client's 300-second timeout. Its tokens are unknown, and it may still have been billed.

The runner then stopped before it wrote any proposals or a report, so the ledger proposals were lost. The runner now writes each reply to disk as soon as it arrives, records a failed call without stopping, and takes `--muse-timeout`. The measured run is `apps_compare_2`.
