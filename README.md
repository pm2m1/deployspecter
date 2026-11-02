# DeploySpecter

DeploySpecter finds and reproduces data corruption caused by rolling database deployments. It runs real PostgreSQL sessions through bounded statement-level interleavings of old code, new code and background jobs, checks the outcome against independently written requirements, and saves each failure so it can be replayed.

## The problem

During a rollout, old code, new code and migrations share one schema. Each can be correct alone and still lose data together. A backfill reads a row, a user updates it, and the backfill's later write discards the update. Other defects need no concurrency at all, yet still only appear once a migration meets real data.

## What it does

- Executes tests on real PostgreSQL, with one session per actor and explicit transaction boundaries
- Enumerates legal interleavings, fewest preemptions first, within schedule and time budgets
- Reads lock waits from `pg_blocking_pids`, and classifies deadlocks, errors and timeouts separately from corruption
- Judges results with an oracle over committed history and final state, hidden from all test generators
- Replays and minimizes every failure, verifying each step by re-execution
- Generates tests with Muse Spark, non-LLM harnesses or by hand, all on the same engine
- Shows code, interleaving, database state, violation, replay and comparison in a local web interface

It supports the Python and SQL patterns described under Limitations. It does not analyze arbitrary codebases.

## Architecture

| Component | Role | Module |
|---|---|---|
| Source extraction | Parses Python with `ast`, without running it, for SQL, transaction blocks and data flow | `source_model.py` |
| Test proposals | Declarative format with no SQL field, shared by every generator | `models.py` |
| Validator | Accepts only statements in the submitted source, on paths the code can take | `validation.py` |
| Scheduler, explorer | Execute schedules within budgets | `scheduler.py`, `explorer.py` |
| Oracle | Reference models for each stated requirement | `oracle.py` |
| Replay, minimizer | Re-execute saved failures and verify each reduction | `replay.py`, `minimizer.py` |
| Model client, analyzer | Muse Spark requests with hard spending limits | `meta_client.py`, `analyzer.py` |
| Baselines, benchmark | Static rules, non-LLM harnesses and scoring | `baselines.py`, `baseline_c.py`, `benchmark.py` |
| External example | The GitLab-derived case, kept apart from the benchmark | `documented.py` |
| Site | Homepage, recorded walkthrough and workbench, with no frontend dependencies | `api.py`, `guided.py`, `site.py`, `static/` |

Actors run as a non-superuser role in a dedicated `deployspecter_test` database, with explicit timeouts, and the schema is rebuilt before each schedule.

## Setup

Requires Python 3.12, [uv](https://docs.astral.sh/uv/) and Docker.

```sh
docker run -d --name deployspecter-pg -p 127.0.0.1:55432:5432 \
  -e POSTGRES_USER=deployspecter -e POSTGRES_DB=deployspecter -e POSTGRES_PASSWORD=CHANGE_ME postgres:16
cp .env.example .env   # match the password in DATABASE_URL
uv sync
```

`MODEL_API_KEY` is needed only for new model requests. Everything below works without it.

## Local demonstration

```sh
uv run deployspecter serve
```

| URL | What it is |
|---|---|
| http://127.0.0.1:8765/ | Product overview, with numbers read from saved results |
| http://127.0.0.1:8765/app | Workbench: runs tests on your local PostgreSQL |
| http://127.0.0.1:8765/demo.html | Recorded walkthrough: saved runs, no database needed |

The site uses recorded model responses only and makes no model requests. It takes no SQL from visitors, and live runs choose from a fixed list of schedules.

```sh
uv run deployspecter build-demo-data          # re-record the walkthrough on PostgreSQL
uv run deployspecter export-site --out dist/site   # static copy of the homepage and recorded walkthrough
```

The static copy has no workbench, API or database. Host only that copy. The workbench is for your own machine: `serve` binds to 127.0.0.1, its API has no authentication, and it should not be exposed publicly.

## Reproduce one failure

```sh
uv run deployspecter explore backfill_overwrite buggy
uv run deployspecter explore backfill_overwrite corrected
uv run deployspecter replay results/benchmark/failures/dev/backfill_overwrite_buggy__hand__min.json
```

The first command finds a stale backfill overwrite with no schedule supplied: 61 of 84 schedules violate the requirement, and no serial order does. The corrected case has no violation in those 84. That covers these inputs only and doesn't prove the code safe.

Tests and lint:

```sh
uv run pytest        # offline; the one paid test needs an explicit -m live
uv run ruff check .
```

## Muse Spark integration

`muse-spark-1.3`, through Meta's Responses API, turns code into executable test proposals:

```
source code → operation catalog → Muse Spark proposal → validator → PostgreSQL → oracle → reproducible failure
```

- **Muse Spark** receives the source, extracted statement catalog and schema. It proposes actors, inputs, seed rows and candidate invariants. It never receives scenario names, labels, requirements or schedules.
- **The validator** rejects unsupported or unsafe proposals. Output can't contain SQL or code, and every statement it names must exist in the submitted source.
- **PostgreSQL** executes the accepted tests.
- **The oracle** alone decides whether a violation exists. Candidate invariants are hypotheses and can never produce a finding.

The interface's pipeline tab shows each stage for any recorded response, including the generated tests. Model requests are off by default, and `explore` and `benchmark` send them only with `--max-requests` and `--max-estimated-usd`, with automatic retries disabled.

## Benchmark findings

5 synthetic scenarios, each with a buggy and a corrected variant: 4 development cases and 6 held-out cases. Budget: 300 schedules and 60 s per system per case. Muse Spark ran up to 3 times per development case and once per held-out case, with a prompt frozen before any held-out model run. See [`results/README.md`](results/README.md).

| Held-out | Defects found | Replayed | False flags | Median schedules to failure | Requests | Est. cost |
|---|---|---|---|---|---|---|
| Static rules | 3/3 (warnings, not executed) | n/a | 0/3 | n/a | 0 | 0 |
| Baseline B (non-LLM) | 3/3 (1 by a guessed value) | 3/3 | 0/3 | 1 | 0 | 0 |
| Muse Spark | 3/3 | 3/3 | 0/2 (1 not executed) | 3 | 6 | $0.39 |
| Hand-written | 3/3 | 3/3 | 0/3 | 3 | 0 | 0 |

| Development | Defects found | Replayed | False flags | Median schedules to failure | Requests | Est. cost |
|---|---|---|---|---|---|---|
| Static rules | 2/2 (warnings, not executed) | n/a | 0/2 | n/a | 0 | 0 |
| Baseline B (non-LLM) | 2/2 | 2/2 | 0/2 | 26 | 0 | 0 |
| Muse Spark (per run) | 6/6 | 6/6 | 0/4 | 4 | 12 | $0.64 |
| Hand-written | 2/2 | 2/2 | 0/2 | 4 | 0 | 0 |

- **Muse Spark found no more defects than the non-LLM systems.**
- Its measured edges are narrow: fewer schedules on 2 of 5 scenarios (4 vs 48, 4 vs 53), and sounder data flow on one, where Baseline B reached a violation with a guessed value. Baseline B needed fewer on another (1 vs 3).
- The non-LLM harnesses were far faster overall, because a schedule takes milliseconds and a model response about a minute.
- First-response validity was 27 of 32 tests on development cases and 18 of 22 on held-out cases, where one whole response was rejected over actor names and never executed.
- Baseline C, added later with these fixtures in view, also found all 5 defects with no false flags in its second run. Its first run's 2 false flags came from generator input mistakes, fixed before that run. It isn't part of the tables above.

Costs are estimates at [Meta's published standard rates](https://dev.meta.ai/docs/pricing-rate-limits), not billing data.

## GitLab-derived example

This example is separate from the benchmark and changes none of its numbers. It is a minimal fixture modelled on a defect GitLab published in [MR !246505](https://gitlab.com/gitlab-org/gitlab/-/merge_requests/246505), with recovery in [MR !246509](https://gitlab.com/gitlab-org/gitlab/-/merge_requests/246509). It isn't GitLab's schema, services, locks or workload. See [`SOURCE.md`](scenarios_external/gitlab_authorization_sync/SOURCE.md).

```sh
uv run deployspecter documented-defect
```

A source table holds one row per access level, and a trigger maintains a one-row-per-pair destination table. With the original delete logic, deleting one access level removes the destination row while another source row remains. This fails in a single transaction and in every schedule tested, so it is a migration semantics defect rather than a race. With the delete logic of GitLab's fix, the same hand-written test keeps the pair at the lowest remaining level. Passing that one test does not prove general safety, including under concurrency.

Baseline B misses this defect, because it seeds one row per pair. Baseline C finds it, but it was written with this defect known, so that result is not blind. The hand-written test also finds it and is not blind either.

### Muse Spark on this example

Muse Spark did not detect this defect: 0 of 3 responses on the original trigger. This was a targeted, post-hoc experiment on a fixture whose defect was already known, not an independent or blind evaluation. The model received only the source, the extracted statement catalog and the schema, using prompt v2 unchanged. Each response had a budget of 300 schedules and 60 s.

Across six responses, three on each trigger, 11 of 24 proposed tests were valid. Another 12 were rejected because they seeded a destination row the trigger had already created, and 1 omitted a required parameter. Each response on the original trigger proposed a test aimed at the delete behaviour, but all three tests were among those rejected seeds, so none of them ran. A response's written description of the behaviour doesn't count as detection. All 6 requests succeeded, with no retries or repairs, at about $0.36 estimated cost, which is not billing data.

The requests left reasoning and storage unset. Each published raw response reports that the provider used high reasoning effort and stored the response.

This section reports detection of the published defect only. It is not a safety result for the corrected trigger, and it says nothing about GitLab's production systems.

Evidence is in [`results/external_public/gitlab_authorization_sync/20261102T130607Z-public-evidence-gitlab-ai-2aef4b9c09c435b7/`](results/external_public/gitlab_authorization_sync/20261102T130607Z-public-evidence-gitlab-ai-2aef4b9c09c435b7/README.md), which holds the three buggy-fixture responses and their execution results. It is a partial record: some original evidence is withheld, so all six trials can't be fully reproduced from public files. [`OMISSIONS.md`](results/external_public/gitlab_authorization_sync/20261102T130607Z-public-evidence-gitlab-ai-2aef4b9c09c435b7/OMISSIONS.md) lists what is left out.

### Reproduce the public evidence

With PostgreSQL running as in [Setup](#setup), and no model key:

```sh
uv run deployspecter reproduce-public-evidence
```

This makes no model requests. It verifies the package's file hashes, replays the three published buggy-fixture responses through the validator, scheduler and oracle, and compares the outcome with the saved results, including the 0 of 3. It also reproduces the documented serial defect with replay, minimization and a separate plain-SQL check, and re-runs Baseline B and Baseline C on the buggy fixture. It prints what it can't reproduce: the withheld corrected-fixture trials, the combined six-request figures, and the original runner. [GitHub Actions](.github/workflows/ci.yml) runs this, the offline tests and Ruff on PostgreSQL 16, with no model key.

### Later retrospective observation

After that experiment, a deterministic state constructor was developed on a separate branch. It searches orders of a proposal's own seed rows, without changing their values or the test's operations. Applied offline to the saved responses, it made the three defect-aimed proposals executable, and all three then reproduced the original serial defect.

This was post-hoc development on a known fixture, using a tool built after those failures were seen. It is not an independent or blind evaluation. It does not change the recorded 0 of 3, and it doesn't show that any Muse Spark prompt or configuration detects defects better. The constructor is not part of this release. [The details are in the evidence package](results/external_public/gitlab_authorization_sync/20261102T130607Z-public-evidence-gitlab-ai-2aef4b9c09c435b7/derived/retrospective_state_construction.json).

## Limitations

- **Small benchmark, not independent.** The fixtures, oracles and baselines were designed with knowledge of each other, so scores are optimistic and don't generalize.
- **Little model data.** Each held-out case has one Muse Spark run, 2 development repeats are missing, and the held-out fixtures have now been seen.
- **Authoring effort is unmeasured.** Whether Muse Spark saves people time remains an open question.
- **Requirements are written by hand per scenario.** A new codebase needs its own.
- **Scheduling stops at statement boundaries.** PostgreSQL internals aren't controlled, and the benchmark uses `READ COMMITTED`.
- **Python logic between statements is modelled, not run.** Flow the extractor can't see is counted as unverified.
- **Extraction is limited.** It handles constant SQL through `execute` and `with conn.transaction()`. ORMs and other languages are unsupported.
- **Submitted code must be trusted.** Migrations run with admin rights on the test database. Model output is restricted, but this isn't a sandbox for hostile code.
