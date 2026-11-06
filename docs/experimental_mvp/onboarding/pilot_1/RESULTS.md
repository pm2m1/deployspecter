# Onboarding pilot 1 (onb-pilot/1): NONBLIND EXPLORATORY, developer-only

**What this is.** One operator: an AI agent that built this harness and knows the DeploySpecter codebase.
- **Not an independent participant.** No human participant took part.
- **One app per condition,** assigned before any onboarding work by a hash-drawn rule in the frozen manifest
  `PILOT_MANIFEST_v1.json` (sha256 `5b0d35e4890f0029433a02ece2b3497ae8324cfaa8a941fa0f7c77385c394dcf`, committed
  before either session).
- **No comparison of conditions is supported.**
  - **App difficulty:** the apps differ in difficulty, and the difference is confounded with the condition.
  - **Order:** the M session came second, after the operator had gained familiarity with the harness.
  - **Size:** n = 1 per condition.

## Measures (recorder clock, `session.measure`; analysis `ANALYSIS.json`)

| | D: saq 0.26.5 | M: taskiq-postgresql 0.4.0 |
|---|---|---|
| Seconds to S1 (first valid spec and workload) | 83 | 257 |
| Seconds to S2 (first corroborated run) | 296 | 283 (1) |
| Seconds to S3 (independent correctness) | 296 | 815 |
| Total seconds | 304 | 823 |
| Interventions (manual operator actions) | 11 | 17 |
| Proposals (rejected) | 1 (0), operator | 4 (1): operator 3, Muse 1 (rejected) |
| Attempts (failed) | 3 (2) | 4 (3) |
| Adapter and spec lines, final (revised) | 198 (40) | 306 (26) |
| Model calls, tokens in/out, est. USD | 0 | 1; 3,543 / 3,515; $0.019367 |
| S3 pass rate on S2 runs | 1.0 | 0.33 |
| Final S3 run: statements matched, params compared / mismatched / unchecked | 71; 89 / 0 / 0 | 12; 15 / 0 / 0 |

(1) Under the frozen definition, S2 needs only a sound corroborated run. taskiq's attempt 1 met it, even though its
install step failed on an import error.

**Budgets** (5400 s, 10 attempts, 3 Muse calls / $2.00): neither session hit one. Muse spend for the whole pilot:
$0.019367 (1 call).

## What happened

**saq (condition D).**
- `attempt_01`: the worker processed the jobs, but the task never ran. saq's `enqueue` takes `key` as the job's own
  key, not as a task argument.
- `attempt_02`: one parameter was mismatched in saq's worker-stats write, an **instrument limitation**:
  - saq binds `json.dumps(...)` text to a jsonb column;
  - PostgreSQL logs that parameter jsonb-normalized, with its keys reordered;
  - the corroborator compares the trace's text digest with that rendering.

  It's recorded as `unsupported` in the session, and needs an engine or corroborate change (out of scope here).
- `attempt_03`: reached S3 with a **declared adapter deviation**. `write_worker_info` (worker statistics, not job
  processing) passes the same values as `Jsonb`.

**taskiq-postgresql (condition M).**
- **Muse proposal 1:** honest about its limits. It named `kiq` and `startup` and listed 9 unknowns: no documented way
  to inject a connection, no worker API, no task name. It proposed an empty workload and an invalid package name, so
  the validator rejected it, as recorded. No further Muse calls were made.
- `attempt_01`: `ImportError`. **taskiq-postgresql 0.4.0 needs `taskiq.compat.IS_PYDANTIC2`, which taskiq 0.13.0
  (the newest, allowed by its unbounded `taskiq>=0.11.7`) removed.** That's a real third-party compatibility
  defect, which the library masks as "psycopg is not installed". The fix was the newest compatible taskiq, 0.12.6.
- `attempt_02`: an **operator error**. The pin edit didn't apply, so it re-ran 0.13.0. It's recorded and preserved.
- `attempt_03`: the library's own migrations, under `run_migrations=True`, ask for a second pool connection inside an
  open transaction, which the one-connection seam can't serve. It timed out, and PostgreSQL ended the idle
  transaction. Recorded as `unsupported`.
- `attempt_04`: reached S3 with **two declared adapter deviations**:
  1. a polling listen driver over the same connection replaces the library's separate LISTEN connection (the library
     has no polling option);
  2. install calls the driver's own `create_table()` and `create_index()` one after the other, outside the
     enclosing transaction.

## Answer to the study question

**Not answerable from this pilot.** With one operator and one app per condition, the condition can't be separated
from the app or the order.
- **Descriptively:** in this single instance, Muse's one proposal was rejected and its advice was limited to naming
  two documented API calls.
- **Where the work went:** most of the M session went on source reading and two library-specific obstacles that
  Muse's documentation-only view couldn't see.

These are observations about two specific sessions, not evidence about Muse-assisted onboarding in general.

## Evidence
- Sessions, as hash-chained `events.jsonl` with snapshots: `saq/session`, `taskiq_postgresql/session`. The
  Muse call log and reply are in `taskiq_postgresql/session/muse_calls.jsonl` and `muse_1.reply.json`.
- **Per attempt:** `attempt_NN/run.json` (redacted) and `trace.json`, plus `attempt_NN.{stdout,stderr,exit_status}`.
- **Harness calibration** on already integrated Chancy, before the freeze: `calibration/`.
