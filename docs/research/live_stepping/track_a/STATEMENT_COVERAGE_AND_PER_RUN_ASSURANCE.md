# Track A: statement coverage and per-run initial-state assurance (milestone 4)

- **Status:** nonnormative. Nothing has run.
- **Method:** derived from reading the frozen Gate 2A code (`supervisor.py`, `serverlog.py`) and the release
  `database.py`, read only, and the research drivers.
- **No frozen file was changed.**

## 1. Every database session the drivers cause

**Classes:**

- **R** means reconciled by the new parent mechanism, independently and from the server log.
- **G** means covered by Gate 2A's previously verified child reconciliation.
- **N** means covered by neither. A call to trusted code doesn't count as an audit.

| # | Session | Opened by | Statements | Class | Used by |
|---|---|---|---|---|---|
| S1 | Tagged parent sessions `ds_parent_<op>_<nonce>` | Both drivers | Only the hashed statement sets | **R** | A1 (all operations), A2 (op2a–op2d, op6i, op6f) |
| S2 | Log reader `ds_parent_log_reader` | Both drivers | `pg_current_logfile`, `pg_stat_file`, `pg_read_binary_file`, markers | **R in A1:** the whole-batch audit allows only these four forms. **N in A2:** no batch audit | A1, A2 |
| S3 | Release `Sandbox` admin connection | A2's `RealEffects` (`Sandbox.__enter__`) | `SET lock_timeout`, `pg_advisory_lock`; driver-initiated `reset` and `snapshot`; and, inside every supervised run, Gate 2A's stale-role sweep, session termination, timeouts, the **bounded reset** (DROP, CREATE, SET, setup SQL, seed INSERTs, GRANTs), the **initial-state read**, log position, markers, role configuration (CREATE ROLE deliberately unlogged), the final-state read and role drop | **N.** Gate 2A's reconciler skips harness sessions, and its reset and initial read happen **before** its start marker | A2 only |
| S4 | Release `Sandbox` monitor connection | A2 (`Sandbox.__enter__`) | Lock-graph and activity polling during runs | **N** (harness session) | A2 only |
| S5 | Child actor sessions (`ds_run_<run id>` role) | Gate 2A child | The application's statements | **G** | A2 only |
| S6 | Child harness sessions | Gate 2A child | Session checks and monitor statements (`MONITOR_ALLOWED`) | **G** | A2 only |
| S7 | Any other session | — | — | A1: the batch audit fails the batch. A2: Gate 2A flags unregistered sessions during runs only | — |

## 2. Verdict by operation

| Operation | Coverage | Status |
|---|---|---|
| **Gate A1, every step** | S1 and S2 only, every statement R. A batch audit attributes every logged session | **Complete statement coverage by design**, except two bracketing log-reader reads at the very start and end, which can't audit themselves. Offline-tested only |
| A2 OP-2 (export phase) | The driver's `Sandbox.reset` before the export is S3, N | **EXECUTION_BLOCKED.** Replace it with A1's reconciled reset (`characterization.reset_statements`) |
| A2 OP-6i | Driver `Sandbox.reset` and `snapshot`: S3, N | **EXECUTION_BLOCKED.** The same replacement |
| A2 OP-3a, OP-3, OP-4, OP-5 | S5 and S6 G, but each run's reset, initial read, final read and role setup are S3, N | **EXECUTION_BLOCKED** until the per-run extension in section 4 is reviewed |
| A2 op2a–op2d, op6f | S1, R | Covered |

## 3. Per-run initial-state assurance: NOT ESTABLISHED by the current public interfaces

| Question | Public evidence available | Established? |
|---|---|---|
| 1. Which reset belongs to which run | `run_id` is in the trace. In the server log, Gate 2A's admin reset precedes that run's `ds_mark_start_<run id>` | **Not by any current check.** The log order would show it, but nothing reads it |
| 2. Which rows were present before execution | `trace.initial_state`, read by Gate 2A's parent through `Sandbox.snapshot` after its reset. That read is in-process and unreconciled, and it masks timestamps | **No.** It is a trusted read, not independent evidence |
| 3. Could unknown writes occur between reset and execution | Gate 2A ends outside sessions before its reset, and polls for unregistered sessions during runs. Between the reset and the start marker nothing is log-reconciled, and CREATE ROLE is deliberately unlogged | **No** |
| 4. Is the final-state check linked to the right run | op6f runs in `explore_live`'s `check` callback after `run` returns and before the next reset. Its tagged window follows that run's end marker in the log | **Partly.** By order of execution, but not yet by a log check |
| 5. Does each run's schema match the reviewed catalog | The setup SQL hash is in the scenario digest. The batch-start and batch-end exports are compared by structural digest | **No.** The end-of-batch match doesn't show per-run data or schema equality |

**No assurance is drawn from the earlier driver-initiated resets.** They are different executions.

## 4. Smallest separately reviewed extension (proposed, not implemented)

A **whole-batch server-log audit for Gate A2**: a pure, new module that changes no Gate 2A code. It reads the whole batch
window. For each `ds_mark_start_<run id>`, it checks the interval since the previous run's `ds_mark_end`:

1. **The admin session's statements are exactly:**
    - Gate 2A's stale-role sweep forms;
    - the session-termination forms;
    - the timeout `SET`s;
    - `DROP SCHEMA ds_run CASCADE`, `CREATE SCHEMA ds_run`, `SET search_path = ds_run`;
    - the setup SQL, whose text must hash to the reviewed setup SQL;
    - the seed INSERTs with exactly the bridged parameters;
    - the three GRANTs;
    - the initial-state SELECTs;
    - the log-position queries.

   That establishes questions 1, 2 and 5 from the log: the rows inserted, the DDL executed, and the run they belong to.
2. **No other session wrote in that interval.** Only the admin, monitor and log-reader sessions may appear, and only with
   the allowed forms. That establishes question 3 up to the unlogged CREATE ROLE, which writes no `ds_run` data.
3. **Each op6f window lies between that run's end marker and the next reset.** That establishes question 4.

**What this needs:**

- the exact statement forms of Gate 2A's parent code, written as a reviewed allowlist derived from the frozen source;
- its own deliberate-breakage tests.

Until it exists, Gate A2's execution operations stay **EXECUTION_BLOCKED**.
