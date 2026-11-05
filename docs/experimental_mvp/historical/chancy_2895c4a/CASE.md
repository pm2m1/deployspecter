# Historical case `chancy-2895c4a`: a late completion overwrites a cancellation (NONBLIND development case)

**Frozen before any run of this case.** This file and `src/deployspecter/experimental/historical_oracles.py` are committed before the first execution; each run report records both hashes.

**Classification: NONBLIND_DEVELOPMENT.**
- Chancy is a prototype development target. Its adapter was written from its source, and its changelog was read in the previous increment.
- The case was chosen *after* reading the fix and its regression test.

It is never a blind or confirmatory case and never counts in RA-Q3.

## The historical defect, from the maintainers' own record

| | |
|---|---|
| Repository | `https://github.com/tktech/chancy` (MIT) |
| Fix commit | `2895c4a7fc173cec3d645221f63c4e51e2cf7e84` (Tyler Kennedy, 2026-10-07): "Jobs now have individual execution claims for safe fencing and cancellation. Job recovery now sends out cancellation requests for any jobs it recovers." Tree `bdf46b6b445bdc8519a3c96133a411610fc4a57a` |
| Buggy parent | `80f570f0b8332212e46c3c27af5940946f6486a9` (2026-10-07). Tree `a5a389c45c8db1f53275a6c121bb3bf520796adb` |
| Released buggy / fixed | 0.25.1 (sdist `264ed4f7…`) / 0.26.0 (sdist `ac98b9c5…`, tag commit `73fd582c`), the first release containing the fix |
| Schema change in the fix | `chancy/migrations/v8.py`, which adds `claim_id`. This is the migration area the frozen corpus rule selected for Chancy (`corpus_selection_1`) |
| Code change in the fix | `cancel_job_ex`: from `SET state = 'failed' WHERE id = %s` to `SET state = 'failed', claim_id = NULL, completed_at = NOW() WHERE id = %s`. Worker completion: from `WHERE id = %(id)s` to `WHERE id = %(id)s AND claim_id IS NOT DISTINCT FROM %(claim_id)s`. 10 files in `chancy/`, +210/−70 |
| Maintainers' statement of the property | the 0.26.0 `cancel_job` docstring: "Marks the job failed and invalidates its execution claim, preventing a late completion from overwriting the cancellation". 0.26.0 CHANGELOG: "Fixed cancellation races that could overwrite cancelled results" (added in `2d463ba`, a changelog rewrite, not with the code) |
| Maintainers' regression test | `tests/plugins/test_recovery.py::test_cancellation_fences_execution_completion` (blob `9345a746cdc06f101f637f5b4dc4c201565e88f9`, added in `2895c4a`). It claims a job, cancels it (FAILED), completes the old execution (success, or CancelledError with attempts left), flushes, and asserts that the job is unchanged, `claim_id` is NULL and `completed_at` is set |
| Upgrade contract | single-version only. The fix commit's changelog said "Stop old workers, run migrations, then start updated workers", and 0.26.0's says "Stop your workers". This case runs **one version at a time**; no mixed-version operation is part of it |

## Exposure ledger (what the developer running this case read, before any run)

1. The 0.25.1 and 0.26.0 sdists' source (adapter work, previous increments) and the 0.26.0 `CHANGELOG.md`.
2. `git log v0.25.1..v0.26.0` subjects (98 commits). Commits matching cancel, claim or overwrite: `2895c4a`, `89db802`, `a53daaf`, `7feee32`, `ea44e76`, `c9bfe78`, `91c03e0`, `aa52319`.
3. `git show --stat 2895c4a`, its CHANGELOG hunk, and `git log -S` for the three changed strings.
4. `git show 2895c4a:tests/plugins/test_recovery.py`: test names, and lines 128–170 (the cancellation test).
5. The parent's `cancel_job_ex` and its completion `WHERE` clause.

Nothing else from this repository's issues, pull requests or tests was read. Training-data exposure of the public repository can't be excluded, and is a standing limitation of every case.

## Oracle (derived from the maintainers' test; encoded by the harness developer, NOT independently authored)

Encoded only from the test assertions and the docstring above. The encoding is the harness developer's.

- **H1 `cancel_survives_completion`:** a job whose `cancel_job` updated 1 row ends `failed`, whatever its execution did afterwards. Source: the test's `assert await chancy.get_job(ref) == expected` (expected = FAILED) and the docstring.
- **H2 `claim_invalidated`:** on a schema with `claim_id`, a cancelled job ends with `claim_id` NULL and `completed_at` set. Source: the test's `assert expected.claim_id is None` and `assert expected.completed_at is not None`. Not applicable to schemas without `claim_id` (0.25.1, `80f570f`).
- **H0, a sanity check (not an oracle):** the job's handler ran exactly once, and was claimed once. Without that, the schedule didn't exercise the race, and the run gives no verdict for H1 or H2.

## Predictions, written before running

- **Buggy (`80f570f`, 0.25.1) under the forced schedule:** H1 violated. The completion flush rewrites `failed` → `succeeded`.
- **Fixed (`2895c4a`, 0.26.0):** H1 and H2 hold.
- **Seeded schedules with cancel-after-claim (no flush gate):** the buggy version violates H1 only when the cancel is granted before the completion flush. The fixed version never does.

## Competing explanations, recorded before running

1. **Notifications are disabled** (the harness can't instrument Chancy's LISTEN connection). The 0.25.1 docstring says cancelling a running job needs notifications.
   - Code reading of 0.25.1, not executed: `_handle_cancellation` → `executor.cancel(ref)` cancels the asyncio task only while the job is still running in `executor.jobs`, and never touches `worker.outgoing`.
   - So when the cancel lands after the handler returned but before the flush, which is the forced schedule here and the case the maintainers' test exercises, the queued `succeeded` update still overwrites the cancel.
   - This is an argument from source; a run with notifications is not possible here.
2. **The release pair is confounded.** 0.25.1 and 0.26.0 differ in 137 files. Attribution rests on the commit pair `80f570f` → `2895c4a` (10 files).
3. **The forced order is deliberate.** It is the order the maintainers' test constructs. It is reachable in production: the flush runs on a 1-second interval after the handler returns. The scheduler orders statements and writes nothing.
