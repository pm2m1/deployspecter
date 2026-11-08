# ds-nbc/1 case `chancy-2895c4a`: a late completion overwrites a cancellation (NONBLIND development)

**Frozen before this case's first ds-nbc/1 live run.** Changes go in `ERRATA.md`, never here.

**Label: NONBLIND development.** This is the corpus's existing case (prior exposure): it was chosen after reading the
fix, and was already reproduced in `docs/experimental_mvp/historical/chancy_2895c4a/` with a different runner
(`run_historical.py`, forced and seeded schedules). Here it is registered in ds-nbc/1 and re-run through the
RA-NONBLIND orchestrator (`research_tools/nbc/run_nbc.py`), arm RA-DET only. It is not blind, confirmatory or
independent evidence, and is never a new finding.

## Upstream

| | |
|---|---|
| Repository | `https://github.com/tktech/chancy` (MIT), public mirror (bare) |
| Fix commit (R2) | `2895c4a7fc173cec3d645221f63c4e51e2cf7e84`: "Jobs now have individual execution claims for safe fencing and cancellation. Job recovery now sends out cancellation requests for any jobs it recovers." |
| Buggy parent (R1) | `80f570f0b8332212e46c3c27af5940946f6486a9` (the fix's first parent) |
| Issue / PR | none recorded in the commit; the 0.26.0 CHANGELOG entry "Fixed cancellation races that could overwrite cancelled results" |
| Maintainers' test | `tests/plugins/test_recovery.py::test_cancellation_fences_execution_completion` (added in the fix) |

## Pinned trees (`research_tools/nbc/pin_chancy.py`, `../../PINS.json`)

`git archive --format=tar <commit> chancy`, normalized by `sealed_evaluation.normalize_archive`. The raw archives
are byte-identical to the existing `SDISTS/chancy-git-80f570f.tar` (`8747cdc8…`) and `chancy-git-2895c4a.tar`
(`cbc461d2…`), and normalizing those gives the same files.

| Slot | Commit | Root tree | Package tree | Normalized sha256 |
|---|---|---|---|---|
| R1 (buggy) | `80f570f` | `a5a389c45c8db1f53275a6c121bb3bf520796adb` | `76c7912522674ed078cec0ed10cabf91979a9503` | `b31ce4b50d65d964a428600dcc8695c54996a887456f52aa828ec648f123ef5a` |
| R2 (fixed) | `2895c4a` | `bdf46b6b445bdc8519a3c96133a411610fc4a57a` | `b2600abaeafd1c9ccaff68ad60d6b3ab11c4e3b1` | `f486e36e4d13f72bf26f832d28dd021074d5739e0b89d7862687ecc587904e25` |

The container build is `chancy-sv/1`'s: the archive is registered at run time in `run_isolated.CHANCY_GIT` under a
hash-derived name; the adapter accepts the tree only if its source hashes are the pinned `git-80f570f` or
`git-2895c4a` ones.

## Contract

Single-version. The fix commit's changelog said "Stop old workers, run migrations, then start updated workers", and
0.26.0's says "Stop your workers". Each run migrates a fresh schema with the revision under test alone (R1 to
schema 7, R2 to 8).

## Integration, workloads and oracle

- **Integration:** `research_tools/nbc/integrations/chancy_sv1.py`, a wrapper that re-exports
  `research_tools/confirmatory/integrations/chancy_sv.py` (`chancy-sv/1`, unchanged, sha256 `338580d2…` checked at
  import) and adds `WORKLOAD_JSON_SCHEMA` and `MUSE_CONTEXT`.
- **RA-DET workloads (chancy-sv/1's frozen grid, decided before any ds-nbc/1 run):**
  1. `{"jobs": [{"n": 0, "cancel": true}], "max_attempts": 1}`
  2. `{"jobs": [{"n": 0, "cancel": true}], "max_attempts": 2}`
  3. `{"jobs": [{"n": 0, "cancel": false}, {"n": 1, "cancel": true}], "max_attempts": 1}`

  The executor gives replicate r workload r, 4 seeded schedules each (plan §5: B = 12, 3 replicates; T = 900 s per
  case and arm). Gates: the producer waits for the worker's first claim query (the window), and the cancel waits for
  the worker's first claim. The explorer decides everything else; there is no forced flush gate here (unlike the
  historical case's forced schedule).
- **Oracle:** `oracle.py` in this directory. It delegates to the frozen checker
  `historical_oracles.chancy_2895c4a` (H0 sanity, H1 cancel survives, H2 claim invalidated where the column exists),
  checking that file's sha256 `db9bc5fe…` on every call. `has_claim_column` is read from the final state.

## Exposure (who read what, before which step)

| When | Who | What |
|---|---|---|
| Before the historical case (2026-11-05) | the harness developer | the fix's stat, CHANGELOG hunk, `git log -S` of three strings, the regression test (lines 128-170), the parent's `cancel_job_ex` and completion `WHERE` (see `historical/chancy_2895c4a/CASE.md`) |
| Before this registration (2026-11-08) | this Track A agent | the historical CASE.md and RESULTS.md, `historical_oracles.py`, `chancy_sv.py`, `run_historical.py`; the R1 tree's `cancel_job`, `push` docstrings, `Job` fields and migrations v1-v7 (for `MUSE_CONTEXT`) |

## Predictions (before the run)

- R1: VIOLATED (H1) on most seeded schedules of workloads 1 and 2: the queued completion flush rewrites `failed` to
  `succeeded`. Workload 3's cancel may land before job 1 is claimed: then job 1 never runs and the run can end
  INCOMPLETE (the coordinator's stop waits for every pushed job's handler) or give NO_VERDICT (H0).
- R2: HOLDS on every SOUND run.
- A NO_VERDICT schedule is not a violation; an INCOMPLETE or REFUSED schedule ends that replicate (executor policy).
