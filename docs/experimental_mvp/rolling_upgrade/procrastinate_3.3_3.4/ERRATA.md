# Errata for CASE.md (written after the first bring-up; CASE.md and its checker stay as frozen at `dc64cc2`)

**E1. The R2 equality was wrong. It is an oracle error, not an application finding.**
- **What it said.** The frozen R2 equality, "`attempts` equals the number of retries (executions − 1)", was a harness assumption (H). It came from reading `retry_job_v1`/`v2` alone.
- **What the source shows.** procrastinate's own `procrastinate_finish_job_v1` *also* increments `attempts` when it finishes a `doing` job (`attempts = CASE status WHEN 'doing' THEN attempts + 1`). So `attempts` = executions + successful admin retries.
- **The first bring-up.** It reported R2 entries on all 15 jobs. The source-defined relation held for 15/15, and so did the documented bound (at most `max_attempts + 1` executions per retry cycle).
- **Reporting from here on:** the frozen checker's R2 entries are reported as **ORACLE ERROR**. Every run also reports, separately, the source-defined relation and the documented bound (`erratum_E1` in each report).

R1 and R3–R7 are unaffected.

**E2. The R5 "after" check wasn't atomic. It is an oracle error, not an application finding.** Found in `runs/final_seeded_2`.
- **What it said.** The frozen R5 required the admin's read *after* `retry_job_by_id_async` to see `status = 'todo'`.
- **What happened.** That read is a separate statement. Under the seeded schedule, the coordinator granted the 3.4.0 worker's claim in between:
  - grant 20: `procrastinate_retry_job_v2` on job 2, committed at 22;
  - grant 25: the 3.4.0 worker's `procrastinate_fetch_job_v2` returns job 2 with `attempts = 3` (before: 2);
  - grant 33: the admin's read sees `doing`.
- **The application** did what v2 documents: `failed → todo` with `attempts + 1`, then the job was claimed.
- **Reporting from here on:** that frozen-checker entry is reported as **ORACLE ERROR**. R5 is reported, separately, by what the retry can be shown to have done: before `failed`, `attempts` one higher, and the job executed again.
