# DeploySpecter research since the hackathon

Snapshot: tag [`research-2026-11-15`](https://github.com/pm2m1/deployspecter/tree/research-2026-11-15) (commit
`493b9ae`). Every path below is relative to that tag, and every number comes from the file cited. **All results are
NONBLIND or EXPLORATORY:** the developers chose the cases and wrote the oracles after reading the upstream fixes.
No confirmatory study has been run.

## What the real-application engine does

The hackathon engine steps declarative scenarios. The research engine steps **real application code**:

- **Driver-level stepping.** Every database operation of real handlers, on psycopg 3 directly, through the
  SQLAlchemy 2.x ORM, or through Django, passes through a deterministic scheduler
  (`docs/experimental_mvp/README.md`; Django runs in `docs/experimental_mvp/results/demo_full_2/integrated/`).
- **Server-log corroboration.** Each run's driver trace is checked against PostgreSQL's own server log: every
  session, statement, typed parameter and error, and the global grant order. A run that doesn't match is refused
  and never retried.
- **Isolated runner.** Third-party libraries run in restricted containers with no network. One coordinator, which
  holds no database credentials, grants every statement. This isolation is **not hostile-grade**: the containers
  share the Docker Desktop VM kernel (`docs/experimental_mvp/simultaneous/RESULTS.md` §8).

## Four reproduced upstream defects (ds-nbc/1)

From `docs/experimental_mvp/nonblind_corpus/CORPUS_RESULTS.md`. Each case runs the fix commit's parent (R1) and the
fix commit (R2) from pinned trees.

| Case | Upstream defect | Fix commit | Schedules to first R1 violation | R2 control |
|---|---|---|---|---|
| chancy-2895c4a | a late completion overwrites a cancellation | [TkTech/chancy@2895c4a](https://github.com/TkTech/chancy/commit/2895c4a7fc173cec3d645221f63c4e51e2cf7e84) | 1 | 8/8 HOLD |
| pgqueuer-8dfadf4 | a picked row is lost when a job is cancelled, breaking `concurrency_limit` (#1032) | [janbjorge/pgqueuer@8dfadf4](https://github.com/janbjorge/pgqueuer/commit/8dfadf4ea3a6fb6d60ac970d799faf8338f9d03f) | 2 (3/3 replicates) | 12/12 HOLD |
| saq-c570ba4 | a timed-out Postgres dequeue doesn't take its buffered job | [tobymao/saq@c570ba4](https://github.com/tobymao/saq/commit/c570ba4ee8366404db97e21074390361328f0d8b) | 1 | 12/12 HOLD |
| saq-addee31 | the `init_db` advisory lock was session-scoped | [tobymao/saq@addee31](https://github.com/tobymao/saq/commit/addee31a697c42d292c5fd05aa107c157b0ee8e0) | 1 (3/3) | 12/12 HOLD |

- **Without any post-hoc change:** 3 reproduced.
- **saq-addee31** reproduced after a disclosed POST-HOC re-encoding.
- **Two other cases were built:** chancy-b8a77df was REFUSED by the instrument, and saq-ad8d1c8 failed its control.
- **Screening:** 68 candidates, of which 34 were screened. Criterion E2 failed 22 of the 34.
- **Corroboration:** 0 parameters mismatched in every case.

## Mixed-version Chancy (0.25.1 and 0.26.0 workers at the same time)

From `docs/experimental_mvp/simultaneous/RESULTS.md`: two restricted containers and one coordinator, with every
statement corroborated.

- **Duplicate execution:** the released code never ran a job twice in 10 seeded schedules (§5: 0 of 10). A declared
  claim-lock mutant did, in 2 of 10.
- **Cancellation:** a cancel was lost in every cell except a 0.26.0 cancel with a 0.26.0 worker (§4). These runs
  are outside Chancy's documented upgrade procedure ("Stop your workers"), so this is **not claimed as a defect**.

## Muse Spark results

- **ds-nbc/1 Track B** (`docs/experimental_mvp/nonblind_corpus/track_b/RESULTS.md`): RA-DET detected 4 of 4 cases
  (95% CI 0.398–1.000) and RA-MD (Muse) 1 of 4 (0.006–0.806). McNemar p = 0.25. Verdict: **INSUFFICIENT SAMPLE**.
  Muse's cost was $0.114168.
  - **Why the gap:** the executor ran one workload per replicate, so only Muse's first 3 of 12 valid proposals ran.
    On pgqueuer-8dfadf4, 2 unused proposals had the triggering shape. This is an executor artifact (POST-HOC).
  - **A second bias:** RA-DET's workloads were written after reading the fixes.
- **ds-nbc/2**, a separate prospective experiment (`docs/experimental_mvp/nonblind_corpus/nbc2/RESULTS.md`): D1
  (fix-informed) 4/4, D2 (outcome-blind generator) 4/4, M (Muse) 4/4.
  - **M vs D2 (primary):** McNemar p = 1.0, no discordant pairs. Kaplan–Meier median schedules to detection: 5
    (M) vs 9 (D2). Verdict: **INSUFFICIENT SAMPLE**.
  - **Fixed-version violations:** D2 and M both had one, on saq-c570ba4 (clause B3).

## Preregistered single-investigator evaluation

The next study is preregistered on OSF: [osf.io/34ytz](https://osf.io/34ytz) (project
[osf.io/9xdrb](https://osf.io/9xdrb)), registered 2026-11-17. It compares Muse workloads with an outcome-blind
generator on historical defects selected by frozen code with a sealed seed, judged by the maintainers' own regression
tests, plus seeded mutants. One person runs it, so its results will be labelled "preregistered, automation-blinded,
single-investigator. Not independent". The frozen plan, code and seed hashes are on branch `research/benchmark-prep`
at commit `312d0af` (`docs/experimental_mvp/osf/FREEZE.json`).

**Status: terminated at Stage 2 (2026-11-17), with 0 eligible historical cases.**
- Selection ran as registered and admitted 4 cases from 3 applications after the 100-screen budget. All 4 were Django
  applications, and none needed concurrency to show its defect.
- The Django seam's validation on paperless-ngx v3.4.2 failed. Migrations and simple requests ran faithfully, but
  interleaved runs stalled on a file lock the application holds outside the database, and the tool refused a
  reconnection the task queue makes. So the 4 cases were refused, and re-selection admitted none.
- No arm ran and no mutants were generated. The primary comparison and every other registered comparison are not
  estimable. Muse spend was $0, and no claim is made about Muse either way.
- Report: [`RESULTS.md`](https://github.com/pm2m1/deployspecter/blob/10aa32d13913c6d61c9f4f257355508da5617377/docs/experimental_mvp/results/RESULTS.md).

## Limitations

- **NONBLIND:** the developers chose the cases and wrote the oracles after reading the fixes.
- **Developer-selected cases:** 4 cases from 3 applications, all of them already exposed to this project.
- **Hand-built integrations:** one per library, written by the developers.
- **Isolation:** not hostile-grade, and hostile-code testing is BLOCKED.
- **No confirmatory study yet.** The protocol (Amendment 003, revision candidate r3) is PROPOSED and unsigned. The
  confirmatory runner refuses to run (`docs/experimental_mvp/protocol/APPROVAL_DASHBOARD.md`). The single-investigator
  evaluation above was registered and terminated at Stage 2 with no estimable result. It was not independent and is
  not called confirmatory.

## Reproduce one case end to end (saq-c570ba4, RA-DET)

Requires Python 3.12, uv and Docker. It uses a disposable PostgreSQL on 127.0.0.1:55441.

```sh
git clone https://github.com/pm2m1/deployspecter && cd deployspecter && git checkout research-2026-11-15
uv sync
git clone --mirror https://github.com/tobymao/saq /tmp/saq.git
uv run python -I research_tools/nbc/pin_saq.py --repo /tmp/saq.git --out /tmp/archives/saq   # verifies commit, tree, sha256
uv run python - <<'EOF'   # point the case spec at your archives; the sha256 values are unchanged and checked again
import json; p = "docs/experimental_mvp/nonblind_corpus/saq/cases/saq-c570ba4/case_spec.json"; s = json.load(open(p))
for slot in ("R1", "R2"): s[slot]["archive"] = f"/tmp/archives/saq/saq-c570ba4/{slot}.tar"
json.dump(s, open("/tmp/saq-c570ba4.json", "w"), indent=1)
EOF
research_tools/mvp_pg.sh start
uv run python research_tools/pg_lock.py -- uv run python research_tools/nbc/run_nbc.py run \
  --cases /tmp/saq-c570ba4.json --arms RA-DET --out /tmp/nbc_saq
uv run python research_tools/nbc/run_nbc.py summarize --out /tmp/nbc_saq
research_tools/mvp_pg.sh stop
```

Expected, per `CORPUS_RESULTS.md`: R1 violates on its first schedule, and the R2 control holds in 12 of 12. The
summary's row for RA-DET should show `"detected": true`, `"schedules_to_detection": 1`, `"false_positive": false`,
`"fixed_schedules": 12`, `"runs": {"VIOLATED": 2, "HOLDS": 4}` and `"params_mismatched": 0`. Because the scheduler is
seeded, a re-run gives the same verdicts. The statement and parameter counts can differ by a few, because saq polls
the queue; the frozen run matched 715 statements, a re-run from a fresh clone 718. RA-DET makes no model requests,
so no API key is needed.

Notes for a first run:
- The `run` step took 74 seconds here, including the image build. The runner builds its image from the pinned archive
  on every run, and checks the image's `saq/` tree against the archive file by file.
- An existing `--out` directory resumes after its committed runs, and does nothing once complete. Use a new `--out`
  to run again.
- `mvp_pg.sh start` refuses if port 55441 is in use or a container named `deployspecter-mvp-pg` exists. Its password
  file is `~/.ds_mvp_pg_password`, deleted by `mvp_pg.sh stop`.
- To clean up afterwards: `rm -rf /tmp/saq.git /tmp/archives /tmp/saq-c570ba4.json /tmp/nbc_saq`.

These steps were re-run from a fresh clone of the tag on 2026-11-15 (macOS, Docker 29.7.2, uv 0.12.9), and gave the
expected verdicts with 0 mismatched parameters. Docker already had the base image, so its first download wasn't
tested.
