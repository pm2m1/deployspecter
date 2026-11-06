# Dry run of one assigned session (2026-11-07): NOT a participant and NOT a study record

**What it was.** The operator (an AI developer agent; `operator_kind: developer-ai`, study id `DRYRUN`) ran the
participant package's tooling once, end to end, to check that it executes. No independent participant took part,
and nothing here counts toward any result.
- **The steps:**
  - `pilot.py start`, a `note`, then `validate` (S1);
  - a live `run` in the restricted runner (S2 and S3: 71 statements, 89 parameters, 0 mismatched, 0 unchecked);
  - `end`, then `measure`;
  - `study.py allocate` (ids `DRYRUN-A` and `DRYRUN-B`), then `bundle` (a blind-scoring bundle, with its key kept
    apart), then `analyze`.
- **The app:** saq 0.26.5, using the pilot's existing adapter, so S1–S3 came within seconds. This shows nothing
  about onboarding effort.
- **The analysis behaved as predeclared:** `excluded_non_independent_sessions: 1`, and the verdict is INSUFFICIENT
  SAMPLE (0 paired independent participants; at least 12 needed).

Files: `measures_DRY_RUN.json`, `run_DRY_RUN.json`, `analysis_DRY_RUN.json`. The raw session log and the bundle
stayed outside the repository. A real study needs two apps that are new to the repository (saq and taskiq now have
adapters here).
