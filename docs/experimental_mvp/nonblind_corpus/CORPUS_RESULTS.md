# ds-nbc/1 NONBLIND development corpus: results (2026-11-08/09)

**Label: NONBLIND, DEVELOPMENT ONLY.** The developers selected these cases and wrote the oracles, after reading the
upstream fixes. Every REPRODUCED case reproduces a **documented upstream** defect; none is a new finding. None is
blind, confirmatory or eligible for the confirmatory corpus.

Selection followed `SELECTION_RULE.md`, which was frozen before the scan:
- the scanner and its first-parent defect are in `scan_attempt_1_first_parent/README.md`;
- the candidates are in `CANDIDATES.json`;
- each application's screening record is in `<app>/SCREENING.{json,md}`.

## Candidates and screening

| App | Candidates | Screened (cap 12, most recent first) | Not screened (capacity) | Eligible | First failing criterion of the rest |
|---|---|---|---|---|---|
| chancy | 5 | 5 | 0 | 1 (b8a77df), plus the prior-exposure case 2895c4a | E2 ×4 (process-executor signalling, logging ×2, a pre-SQL KeyError) |
| pgqueuer | 33 | 12 | 21 | 1 (8dfadf4) | E2 ×8 (in-process asyncio, overflow, ExceptionGroup, supervision); E3 ×3 (heartbeat-silence wall-clock, the scheduler's hard-coded staleness, an event-loop interleaving in one process) |
| procrastinate | 5 | 5 | 0 | 0 | E2 ×5 (in-process stop and abort logic, a test double, a defensive constraint) |
| saq | 25 | 12 | 13 | 3 (c570ba4, ad8d1c8, addee31) | E2 ×5; E3 ×3 (before v0.25 a queue needs two concurrent connections); E4 ×1 |
| taskiq-postgresql | n/a | n/a | n/a | n/a | excluded: no public repository declared |
| **total** | **68** | **34** | **34** | **5 + 1 prior** | |

## Cases (one per upstream fix; seeds and replicates are never cases)

| Case | Upstream defect | R1 → R2 | Track A classification | Schedules to first R1 violation | R2 control | Corroboration (statements / params compared / mismatched / unchecked) |
|---|---|---|---|---|---|---|
| chancy-2895c4a | late completion overwrites a cancellation (prior exposure) | 80f570f → 2895c4a | **REPRODUCED** (workloads 1–2; workload 3 INCOMPLETE on both) | 1 | 8/8 HOLD on the reproducing workloads | 1665 / 1298 / 0 / 0 |
| chancy-b8a77df | job-row locks taken in inconsistent order deadlock (#89) | 6716e0d → b8a77df | **REFUSED (instrument)**: R1 deadlocked on 2 SOUND schedules; every R2 control was REFUSED, because a lock wait reorders statement completion against the grant order | (R1 4 and 1) | none SOUND on the overlapping workloads | 859 / 1259 / 0 / 0 |
| pgqueuer-8dfadf4 | a picked row is lost when a job is cancelled, breaking `concurrency_limit` (#1032) | f258cbc → 8dfadf4 | **REPRODUCED** | 2 (3/3 replicates) | 12/12 HOLD | 1489 / 3665 / 0 / 0 |
| saq-c570ba4 | a timed-out Postgres dequeue doesn't take its buffered job (job left `active`) | 59f32fc → c570ba4 | **REPRODUCED** | 1 | 12/12 HOLD | 715 / 791 / 0 / 0 |
| saq-addee31 | the `init_db` advisory lock was session-scoped | f827b52 → addee31 | **REPRODUCED after a disclosed POST-HOC re-encoding**: attempts 1–2 REFUSED (a stale image; a parse-time server error the corroborator can't pair); integration and oracle v2 written after attempt 2 | 1 (3/3) | 12/12 HOLD | 480 / 376 / 0 / 0 |
| saq-ad8d1c8 | jobs stranded under burst limits | 525acc9 → ad8d1c8 | **CONTROL FAILED**: R2 also exceeds `max_burst_jobs` (1 of 9). The oracle was not narrowed and nothing was rerun | 1 | 1 violation | 859 / 1139 / 0 / 0 |

**Count:**
- **Distinct executable historical cases with a holding control:** 4 (chancy-2895c4a, pgqueuer-8dfadf4,
  saq-c570ba4, saq-addee31).
- **Reproduced without any post-hoc change:** 3.
- **Two more were built:** one is REFUSED by the instrument, one failed its control.

This is **fewer than 10**, for these reasons:
- **The pool:** only already-exposed applications were admissible.
- **E2:** most fix commits in job-queue libraries fix in-process asyncio or supervision logic, not database-mediated
  behaviour (E2 failed 22 of the 34 screened).
- **Capacity:** 34 candidates were left unscreened.
- **Instrument limits:**
  - lock waits inside a run (chancy-b8a77df);
  - parse-time errors;
  - the coordinator's fixed 45 s stop;
  - one connection per actor (saq before v0.25).

## Instrument findings (recorded; nothing weakened)

1. **Lock waits reorder statement completion.** The corroborator's grant-order check refuses legitimate lock waits
   inside a run. This structurally refuses the fixed control of a lock-ordering fix.
2. **Parse-time errors can't be corroborated.** When the server rejects a statement at parse time, the log has an
   ERROR row with no statement row, and the run is refused.
3. **Stale build context:** normalized archives (mtime 0) can make Docker reuse a stale file. Every case image was
   audited afterwards (`IMAGE_AUDIT.md`); all matched exactly one tree.
4. **`run_nbc.summarize`'s `verified_detection`** rejects R2 VIOLATED runs but not REFUSED or INCOMPLETE ones. That
   code is frozen in the Track B manifest. The analysis therefore reports SOUND R2 schedules beside every detection.
5. **The executor runs one workload per replicate,** so an arm uses only its first 3 valid workloads
   (pgqueuer-8dfadf4 POST-HOC note).
6. **An unlocked suite run (incident 1)** was caused by the coordinator's brief, and corrected.

## Exposure

Each case's `case_spec.json` `exposure` and `CASE.md` record what was read, and before which step.
- **Read before encoding:** the fix commit messages, diffs and maintainers' tests.
- **Muse's view (Track B):** only each integration's `MUSE_CONTEXT`, which was leak-checked by offline tests and
  again before admission.
