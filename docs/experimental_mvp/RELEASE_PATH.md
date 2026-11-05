# release_seats: root cause, corrected reference and evidence (EXPERIMENTAL)

**Scope.** This is Gate 2A's synthetic `tests/live_app/reservations.py`, a developer-written fixture. It isn't a blind finding.
- **Origin:** Muse's planner document `double_release_same_holder` (`results/apps_compare_2`, `muse_planner_1#4`) reached the release path first.
- **Builds:** the frozen fixture and its two builds are unchanged. `buggy` is `lock_row=False`; `fixed` is `lock_row=True`.
- **Corrected reference:** a separately named build, `corrected` (`src/deployspecter/experimental/apps/reservations_corrected.py`, sha256 `d656ee63…`).
- **Labels:** the `fixed` build is not relabelled as correct. It is correct for `reserve_seats` only.

## The code (frozen, `tests/live_app/reservations.py:51-64`)

```python
cur.execute("SELECT id, event_id, qty FROM reservations WHERE holder = %s ORDER BY id", (holder,))   # 54: no lock
held = cur.fetchone()
...
cur.execute(f"SELECT reserved FROM events WHERE id = %s{lock}", (held["event_id"],))               # 59: lock_row locks only this
reserved = cur.fetchone()["reserved"]
cur.execute("UPDATE events SET reserved = %s WHERE id = %s", (reserved - held["qty"], held["event_id"]))  # 61
cur.execute("DELETE FROM reservations WHERE id = %s", (held["id"],))                                # 62: rowcount unchecked
```

## Findings, each established on PostgreSQL 16

1. **A serial duplicate release is correct in every build.** The second call finds no reservation and returns `nothing_held` (`TestRootCause::test_a_sequential_duplicate_release_is_correct_in_every_build`).
2. **The defect needs concurrency.** Two releases by one holder that both run line 54 before either commits pick the same reservation. Both decrement `reserved`, and the second DELETE removes 0 rows. The traces record DELETE rowcounts `[0, 1]`.
3. **The root cause is a combination of two things:**
   - a **missing claim (lock) on the reservation row** being released;
   - a **state transition not conditioned on the reservation actually being removed** (line 61 runs whatever line 62 does).

   `lock_row` adds the event lock only. That serializes the two decrements but doesn't stop the second, so a row lock on the event alone doesn't fix it.
4. **The required invariants:**
   - **R1** (frozen `properties.py`): an event's `reserved` equals the seats in its reservations.
   - **R5** (experimental, written from the declared semantics "a release removes one reservation the holder has"): reservations after = before + callers told `reserved` − callers told `released`.
   - **R2**: never violated in these cases.
5. **Oracle false positives.** The oracle reads committed state in its own REPEATABLE READ snapshot, after every actor has disconnected. The one way it could report a false positive is a seed that already violates it, so `harness.run_app` now refuses such a seed (`test_an_initial_state_that_already_violates_the_oracle_is_refused`).

## The correction (`reservations_corrected.release_seats`)

1. `SELECT … WHERE holder = %s ORDER BY id FOR UPDATE`. A release waiting on this lock re-reads after the other commits, so it sees the deletion and takes the holder's next reservation, or none.
2. `SELECT reserved FROM events WHERE id = %s FOR UPDATE`, as in `lock_row`.
3. `DELETE` first. The `UPDATE` runs only if exactly one row was deleted. Otherwise the transaction rolls back and `ReleaseConflict` is raised, never hidden.

`reserve_seats` is the frozen function with `lock_row=True`.

## Evidence (`results/release_path_1`, revision `403d3c5`, clean tree)

Each case was explored exhaustively within 200 schedules. Each build also ran once on the both-read-first schedule, and the server log corroborated every one of those race runs.

| Case | buggy: violating / explored | fixed (lock_row) | corrected |
|---|---|---|---|
| Duplicate release, one held | 90 / 92 (R1, R5) | 34 / 36 (R1, R5) | **0 / 10** |
| Duplicate release, two held | 90 / 92 (R1, R5) | 34 / 36 (R1, R5) | **0 / 10** |
| Two independent releases, one event | 80 / 92 (R1, lost update) | 0 / 36 | 0 / 36 |
| Reserve racing a release by the same holder | 0 / 21 | 0 / 21 | 0 / 21 (both serial outcomes occur) |
| Unknown holder | 0 / 21 | 0 / 21 | 0 / 21 |

Race-run trace hashes for the duplicate release, one held:

| Build | Run | Trace sha256 | Result |
|---|---|---|---|
| buggy | `e34227129609` | `e3cda1b4…` | `reserved = -4`, R1 and R5 violated |
| fixed | `ca235a12cf24` | `3331a3fe…` | the same violations |
| corrected | `75cbf943f60b` | `0ac871af…` | holds; one caller `released`, the other `nothing_held` |

The full traces, server-log reconciliation, digests and source hashes are in the `*.bundle.json` and `*.run.json` files.

**Rollback and retry** (`test_a_release_that_times_out_rolls_back_and_a_retry_succeeds`):
- A test actor holds the event lock past `lock_timeout`.
- The corrected release fails with `55P03`.
- The harness's recorded cleanup ROLLBACK undoes it, and the server log corroborates both the ERROR row and the ROLLBACK.
- A retry releases the reservation exactly once.

## Found on the way (tracer bugs, fixed)

- **Zero-row SELECTs (`0f088e6`):** the gate rewound prefetched rows with `scroll(0)`, which psycopg refuses on an empty result. So every zero-row SELECT under stepping failed the handler with `IndexError`. Earlier results whose handlers read nothing, such as releasing for a holder with no reservation, recorded an application error there instead of the handler's real path. That includes `results/apps_compare_2`, which predates the fix.
- **Run identity (`403d3c5`):** planned runs with different documents could share a run id, because the key ignored the planned state.
