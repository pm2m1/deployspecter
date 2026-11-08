# ds-nbc/1 screening: procrastinate (NONBLIND development)

**Label: NONBLIND development.** Screened by the Track A agent on 2026-11-08, under the frozen rule
`SELECTION_RULE.md` (sha256 `0b567517…`) and the scan's `CANDIDATES.json` (sha256 `0b026150…`, head `bedbe0e`). The
machine-readable record, with every pin, is `SCREENING.json`.

**Result: 5 of 5 candidates screened (cap 12), none eligible. No case was built, so nothing was run.**
Every candidate fails E2: none is database-mediated behaviour reachable through procrastinate's public API.

| # | Fix commit | Date | Subject | First failing | Why |
|---|---|---|---|---|---|
| 1 | `0826080` | 2026-06-12 | Fix worker stop request lost during startup … | **E2** | An asyncio scheduling race inside one process. A cancel that lands between `create_task(_run_loop)` and the loop task's first step is erased by `_stop_event.clear()`. No database statement lies in that window. The maintainers' regression test is a unit test on the in-memory app |
| 2 | `a7bea33` | 2025-05-08 | Refactor queuing lock handling of InMemoryConnector | **E2** | Only `procrastinate/testing.py` changes: the in-process test double that simulates the queueing-lock index in Python |
| 3 | `7864e21` | 2024-12-17 | Constraint to prevent jobs in todo state with abort_requested | **E2** | A defensive CHECK constraint, not a reachable defect. In the parent, the only path that sets `todo` on an existing job (`procrastinate_retry_job_v1`) takes that branch only when `abort_requested` is false, read under `FOR UPDATE` in the same plpgsql call. Cancel moves `todo` to `cancelled`, finish resets the flag, and inserts default it to false |
| 4 | `e94a5a2` | 2024-10-14 | handle aborting sync jobs on shutdown | **E2** | In-process worker shutdown logic: Python timers (`shutdown_graceful_timeout`), sync jobs in threads, and an in-memory abort-reason map. The database only records the outcome |
| 5 | `b49a755` | 2024-07-17 | handle reraising cancellation on error | **E2** | asyncio `CancelledError` propagation in `job_processor.py` (swallowed by `except Exception: pass`). No database involvement |

Not screened (capacity): none.

## E1 for every candidate (tree level)

Both trees of each pair are public and reachable from the default branch. `git archive` of `procrastinate/` is
normalized with `sealed_evaluation.normalize_archive`. That is deterministic: built twice, same sha256. The trees are
pinned in `SCREENING.json` by git tree hash and archive sha256. Every declared runtime dependency is available as a
hash-pinned entry in `research_tools/isolated/requirements.procrastinate.txt` or `uv.lock`.

**No image was built.** A git tree has no `PKG-INFO`, and procrastinate reads its own distribution metadata at import,
so a build would need a declared `METADATA` file. This wasn't attempted because E2 fails on its own.

The archives are kept outside the repository, under
`/private/tmp/claude-501/-Users-kiyo-Desktop-deployspecter/b1c95b43-b64d-4432-a288-8cf30f7b145b/scratchpad/nbc_archives/procrastinate/procrastinate-<fix[:7]>/{R1,R2}.tar`.

To reproduce:

```bash
.venv/bin/python research_tools/nbc/pin_procrastinate.py pin --mirror <procrastinate.git> --out-root <dir> <fix commits>
.venv/bin/python research_tools/nbc/pin_procrastinate.py verify --mirror <procrastinate.git> \
    --screening docs/experimental_mvp/nonblind_corpus/procrastinate/SCREENING.json
```

## Secondary notes (not used for the decision)

The screen stops at the first failing criterion. The later notes in `SCREENING.json` are marked secondary:
- `0826080` and `e94a5a2` would also fail E3. Their windows are event-loop slots, wall-clock timeouts and threads,
  and the stepping seam orders database statements only.
- `7864e21` would fail E3 for any interleaving inside `procrastinate_retry_job_v1`, which is one stored-procedure call.
- The parents of `7864e21`, `e94a5a2` and `b49a755` are on the 3.0.0 development line and were never released.

## Exposure (what was read, before which step)

All of this was read during screening, before any case decision. Nothing was run.
- **Every candidate:** commit message, `git show --stat`, and `pyproject.toml` dependencies of the fix and its parent.
- **`0826080`:** the full diff of `worker.py` and the new unit test. Also the parent's `Worker.run` and `_run_loop`.
- **`a7bea33`:** the full diff.
- **`7864e21`:** the full diff, and the parent's `schema.sql` (job functions, plus a grep for `'todo'` and
  `abort_requested`). Also the subject of merge `9cc20e2` (PR #1256). The PR page wasn't read.
- **`e94a5a2`:**
  - the diffs of `docs/howto/advanced/shutdown.md`, `app.py`, `cli.py`, `job_context.py` and `manager.py`;
  - part of the `worker.py` diff;
  - part of the `tests/acceptance/test_async.py` diff.
- **`b49a755`:** the full diff.

No issue or pull-request pages were read (no network). Training-data exposure of the public repository can't be
excluded.

**Prior exposure:** `8fa4ab5` ("Close race condition in procrastinate_fetch_job", 2020-06-15) was read in earlier work.
It is not a ds-nbc/1 candidate: it lies outside the window (rule §2), dated before psycopg 3 existed.
