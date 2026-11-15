# Upstream leads: summary (2026-11-15; NONBLIND, EXPLORATORY, POST-HOC; nothing posted; no maintainer has confirmed anything)

1. **saq verdict: likely defect, as a mismatch with the documented contract.** It is a docs gap instead if the maintainers intend a soft cap. Not confirmed by a maintainer.
2. **The documented contract:** the `Worker` docstring is the only documentation: "max_burst_jobs: the maximum number of jobs to process in burst mode" (`saq/worker.py:74`, unchanged from 59f32fc to v0.26.5). Our clause B3 assumes that reading (`saq/SEMANTICS.md`).
3. **The code:** it counts a job only after it finishes, and caps only jobs in flight, as #311 says. So up to `max_burst_jobs + concurrency - 1` jobs run. The only test uses concurrency 1.
4. **On the latest release, saq 0.26.5** (sdist sha256 `75e8664c…`, the same `saq/` as the pinned tree): both ds-nbc/2 workloads violate B3 in 10 of 10 SOUND, server-log-corroborated runs. The within-limit controls hold in 6 of 6 (`saq/RERUN.md`).
5. **Smallest failing workload:** 3 jobs, concurrency 2, cap 2, giving 3 jobs processed (80 grants). It doesn't depend on the schedule: 5 seeds per workload gave the same result.
6. **Standalone reproduction** (saq public API plus docker-compose, no DeploySpecter): over the cap in 20 of 20 and 20 of 20 iterations, and 0 of 20 in both controls (`saq/standalone/`).
7. **Oracle:** not narrowed. No oracle-error file was written, because the documentation doesn't permit the overrun. Earlier results are unchanged.
8. **Final draft issue** (owner posts): `saq/UPSTREAM_ISSUE_saq.md`, body in `saq/UPSTREAM_ISSUE_saq.body.md`. It cites the constructor clamp, gives the mechanism and an untested one-line suggestion, and says "this may be intended; if so, could the docstring say…". A search of saq issues and PRs found no duplicate (#311, #253, #179, #98 are related only).
9. **Chancy:** the documented upgrade is "Stop your workers", then migrate, and the maintainer describes scaling to 0 (#38). Mixed 0.25/0.26 workers are neither documented as supported nor ruled out (`chancy/UPGRADE_GUIDANCE.md`).
10. **Chancy draft question** (`chancy/DISCUSSION_DRAFT_chancy.md`): a cancel was lost in every cell except a 0.26.0 cancel with a 0.26.0 worker. It's labelled NONBLIND and outside the documented procedure, and suggests a docs note.
