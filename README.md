# DeploySpecter

DeploySpecter finds and reproduces data corruption caused by rolling database deployments. It runs real PostgreSQL sessions through bounded statement-level interleavings of old code, new code and background jobs, checks the outcome against independently written requirements, and saves each failure so it can be replayed.

## The problem

During a rollout, old code, new code and migrations share one schema. Each can be correct alone and still lose data together. A backfill reads a row, a user updates it, and the backfill's later write discards the update. Serial tests can't see this, because the failure needs a particular ordering of committed transactions.

## Features

- Real PostgreSQL execution, with one session per actor and explicit transaction boundaries
- Bounded enumeration of legal interleavings, fewest preemptions first
- Lock waits read from `pg_blocking_pids`; deadlocks, errors and timeouts are classified separately from corruption
- An oracle over committed history and final state, hidden from all test generators
- Verified replay and minimization of every failure
- Test generation by Muse Spark, a non-LLM harness or a person, all on the same engine and budget
- A local web interface showing code, interleaving, database state, violation, replay and comparison

## Architecture

| Component | Role | Module |
|---|---|---|
| Source extraction | Parses Python with `ast`, without running it, for SQL, transaction blocks and data flow | `source_model.py` |
| Test proposals | Declarative format with no SQL field, shared by every generator | `models.py` |
| Validator | Accepts only statements in the submitted source, on paths the code can take; checks known data flow and schema names | `validation.py` |
| Scheduler, explorer | Execute schedules within schedule and time budgets | `scheduler.py`, `explorer.py` |
| Oracle | Reference models for each stated requirement | `oracle.py` |
| Replay, minimizer | Re-execute saved failures and verify each reduction | `replay.py`, `minimizer.py` |
| Model client, analyzer | Muse Spark requests with hard spending limits | `meta_client.py`, `analyzer.py` |
| Baselines, benchmark | Static rules, non-LLM harness and scoring | `baselines.py`, `benchmark.py` |
| Interface | FastAPI service and one page | `api.py`, `static/` |

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

## Usage

```sh
uv run pytest                # offline; the one paid test needs an explicit -m live
uv run ruff check .
uv run deployspecter serve   # http://127.0.0.1:8765
uv run deployspecter usage   # recorded model usage
```

## Example

Find the failure with no schedule supplied, then replay a saved one:

```sh
uv run deployspecter explore backfill_overwrite buggy
uv run deployspecter explore backfill_overwrite corrected
uv run deployspecter replay results/benchmark/failures/dev/backfill_overwrite_buggy__hand__min.json
```

The buggy case violates its requirement in 61 of 84 schedules: a user commits `Alicia`, and the backfill's stale write leaves `Alice`. No serial order fails. The corrected case, whose update adds `AND display_name IS NULL`, violates nothing across all 84. That covers these inputs only and doesn't prove the code safe.

## Meta AI integration

`muse-spark-1.3` receives the source, the extracted statement catalog and the schema, through Meta's Responses API. It returns typed JSON describing operations, conflicts, tests and candidate invariants. It receives no scenario names, variant labels, requirements or schedules; a guard enforces this, and a test confirms each recorded response answers the prompt built from the current code.

Its output is data. It can't contain SQL or code, and any statement it names must exist in the submitted source. Its invariants are hypotheses, reported separately, and only the oracle can produce a finding.

Model requests are off by default. The interface never makes them, and uses recorded responses only. `explore` and `benchmark` send requests only when given `--max-requests` and `--max-estimated-usd`, which are enforced before each request, and automatic retries are disabled.

## Evaluation

5 scenarios, each with a buggy and a corrected variant: 4 development cases and 6 held-out cases. Budget: 300 schedules and 60 s per system per case. Muse Spark ran up to 3 times per development case and once per held-out case, with a prompt frozen before any held-out model run. See [`results/README.md`](results/README.md).

**The execution engine works on these fixtures.** Hand-written tests explored each fixture's full schedule space: every buggy case violated its requirement and no corrected case did. All 50 saved failures replay with identical behaviour.

**Muse Spark usually generates valid tests.** On first response, 27 of 32 development tests and 18 of 22 held-out tests were valid. The rejected ones came from bare function names in 2 development responses and hyphenated actor names in 1 held-out response.

**It found no more bugs than the non-LLM harness.**

| Held-out | Bugs found | Replayed | False flags | Median schedules to failure | Requests | Est. cost |
|---|---|---|---|---|---|---|
| Static rules | 3/3 (warnings, not executed) | n/a | 0/3 | n/a | 0 | 0 |
| Non-LLM harness | 3/3 (1 by a guessed value) | 3/3 | 0/3 | 1 | 0 | 0 |
| Muse Spark | 3/3 | 3/3 | 0/2 (1 not executed) | 3 | 6 | $0.39 |
| Hand-written | 3/3 | 3/3 | 0/3 | 3 | 0 | 0 |

| Development | Bugs found | Replayed | False flags | Median schedules to failure | Requests | Est. cost |
|---|---|---|---|---|---|---|
| Static rules | 2/2 (warnings, not executed) | n/a | 0/2 | n/a | 0 | 0 |
| Non-LLM harness | 2/2 | 2/2 | 0/2 | 26 | 0 | 0 |
| Muse Spark (per run) | 6/6 | 6/6 | 0/4 | 4 | 12 | $0.64 |
| Hand-written | 2/2 | 2/2 | 0/2 | 4 | 0 | 0 |

| Schedules to first failure | Split | Non-LLM harness | Muse Spark |
|---|---|---|---|
| `backfill_overwrite` | dev | 48 | 4 |
| `mixed_version_overwrite` | dev | 4 | 4 |
| `soft_delete_resurrection` | held-out | 53 | 4 |
| `trigger_dual_write` | held-out | 1 | 1 |
| `wallet_credit_resync` | held-out | 1, by a guessed value | 3 |

Its measured contributions are narrow:

- **Fewer schedules** on 2 of 5 scenarios, and the same or more on the rest.
- **Sounder data flow** on the wallet case. The non-LLM harness guessed a value and failed in a serial order, while Muse Spark's test failed only under concurrency, like the hand-written reference.

The non-LLM harness was still far faster overall, because a schedule takes milliseconds and a model response takes about a minute. Costs are estimates at [Meta's published standard rates](https://dev.meta.ai/docs/pricing-rate-limits), not billing data. Tokens are as reported by the API.

After the held-out run, the validator was changed to normalize actor names. Re-executing the rejected `wallet_credit_resync/corrected` response then gave 4 of 4 valid tests, 245 schedules and no violation, though one of those tests never exercised the requirement. This post-hoc check isn't counted in the table above.

## Limitations

- **Small benchmark, not independent.** The fixtures, oracles and baselines were designed with knowledge of each other, including the static rules and non-LLM harness. Their perfect scores are optimistic, and the results don't generalize.
- **Little model data.** Each held-out case has one Muse Spark run, so variation there is unknown, and 2 development repeats are missing. The held-out fixtures have now been seen, so a fair re-test needs new ones.
- **Authoring effort is unmeasured.** Whether Muse Spark saves people time remains an open question.
- **Requirements are written by hand per scenario.** A new codebase needs its own. Without them, only final-state serializability is available, as a diagnostic.
- **Scheduling stops at statement boundaries.** PostgreSQL internals aren't controlled, and only `READ COMMITTED` is exercised.
- **Python logic between statements is modelled, not run.** Generators supply data flow using a few allowed transforms, and flow the extractor can't see is counted as unverified.
- **Extraction is limited.** It handles constant SQL through `execute` and `with conn.transaction()`. Other patterns produce warnings, and ORMs and other languages are unsupported.
- **Submitted code must be trusted.** Migrations run with admin rights on the test database, and SQL in the source could undo the actor role. Model output is restricted, but this isn't a sandbox for hostile code.
