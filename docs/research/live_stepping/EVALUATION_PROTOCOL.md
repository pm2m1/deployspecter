# Evaluation protocol for live application stepping

Protocol version 1, written against `main` at `e0baac256aadcf7da0d19c56d37f23b2b180a924`, before any development against an external application. Nothing described here has been run.

**Status.** Frozen by the Git commit that adds this version. This file is never edited afterwards. Any change is a numbered amendment under section 14.

**Limit of the freeze.** A local commit's date isn't independently verifiable, because Git dates can be set freely. The freeze becomes externally checkable only when this commit, or its SHA-256, is published somewhere timestamped. Reports must state when that happened relative to the first evaluation run.

## 1. Questions

1. Does running real application functions under controlled interleavings detect migration and concurrency defects that statement replay can't?
2. Do Muse Spark workload proposals add detection beyond a strong deterministic baseline, at equal execution budget?
3. Separately, do Muse Spark's semantic state goals add anything beyond deterministic state construction?
4. If Muse Spark adds no detection, does it reduce the manual work of onboarding an application, without losing detection?

## 2. Hypotheses

Stated before any arm runs. "Detected" is defined in section 5. All comparisons use the same set of eligible defects, paired by defect.

**Primary**

- **P1.** Arm D detects more historical held-out defects than arm E at equal execution budget.
- **P2.** Arm E detects more historical held-out defects than arm R at equal execution budget.

**Secondary (each reported, none claimed as confirmatory)**

- **S1.** G detects more than E (model workloads alone).
- **S2.** F detects more than E (model state goals alone).
- **S3.** D detects more than C (the constructor's contribution, given model goals).
- **S4.** E detects more than A (the deterministic additions, beyond the application's own tests).
- **S5.** Among defects both arms detect, D needs fewer runs to first detection than E.
- **S6.** D has no more false flags than E on fix versions.
- **S7.** Mutation score ranks the arms in the same order as historical detection.
- **S8. Muse-assisted onboarding, conditional.** Tested only if P1 isn't supported, or fewer than 10 eligible defects exist. Muse-drafted registration, reviewed by a person, reduces both developer minutes and handwritten lines by at least 30% (median across applications) against manual registration, and the deterministic workloads built from it detect no fewer held-out defects. Section 11 gives the design.

A failed or untestable hypothesis is reported as such. No hypothesis is added after sealed results are seen, except as an amendment labelled exploratory.

## 3. Corpus

### 3.1 Inclusion criteria for an application

All must hold, checked only from the dependency manifest, the file tree and a boot test:

- E1. Public repository with a licence that permits local testing.
- E2. Python application or library with sustained releases, and a pinned tag or commit.
- E3. Uses psycopg 3 against PostgreSQL.
- E4. Has versioned schema migrations.
- E5. Boots and passes its own database tests on local disposable PostgreSQL, with no external paid service.

### 3.2 Exclusion criteria

- X1. GitLab or any Ruby application. The GitLab-derived fixture is never part of the blind benchmark, because its defect and its responses are already known.
- X2. Only psycopg2, or a different driver.
- X3. Archived repository.
- X4. A framework reached only through another project's ORM layer, with no application workload of its own.
- X5. Any test needing a production system, live account or real user data.

### 3.3 Direct-driver development applications

These exercise the driver layer without an ORM. Too few qualify to split, so both are development only.

| Application | Version | Commit | Verified from the source |
|---|---|---|---|
| `langchain-ai/langgraph`, `libs/checkpoint-postgres` | Tag `checkpointpostgres==3.2.0` | `d4149cc1f5384364d1a21092b4c2f582a6e7af8f` | Package tree byte-identical to monorepo tag `1.2.19` (`5bd231cbfd913e05ebc662f519eb9a655bc766df`) except `uv.lock`. Its lock file pins `psycopg` 3.3.6 and `langgraph-checkpoint` 4.3.0 |
| `procrastinate-org/procrastinate` | `3.10.0` | `8b6398329d915b7299c0d936e567facaebea8903` | Sync connector is built on `psycopg_pool` |

### 3.4 Django applications, assigned mechanically

The pool is every application checked at this point that uses Django and psycopg 3. To avoid choosing by what is remembered of their histories, assignment is fixed by a hash: `sha256("e0baac256aadcf7da0d19c56d37f23b2b180a924:<owner>/<repo>")`, ascending. The two lowest are development, and the rest are held out. The salt is the `main` commit this work was required to start from.

| Hash prefix | Application | Version | Commit | Psycopg evidence | Assignment |
|---|---|---|---|---|---|
| `0656118ad19867c6` | `inventree/InvenTree` | `1.6.3` | `221af32fdb4382f29c351d9dd9b39992bfaf8d12` | `psycopg[binary, pool]` in the container requirements | Development |
| `0c9ba7537befa4b9` | `paperless-ngx/paperless-ngx` | `v3.4.1` | `b67cfdf3fdfcde4883c4ac136c8545835f679d21` | `psycopg[c,pool]==3.3.4` (optional) | Development |
| `3b10cb2db1fab683` | `saleor/saleor` | `3.23.45` | `e5b7e5f80f8b579aa27b18a271da78d8fd835947` | `psycopg[binary]>=3.2.9,<4` | Held out |
| `7f8588e7d824e481` | `healthchecks/healthchecks` | `v5.0.3` | `51a503531d845fc11d5ef270d576c59c300e3620` | `psycopg==3.3.6` | Held out |
| `8e74b2049adaf81e` | `netbox-community/netbox` | `v4.7.4` | `9b9844bdbc226bc0bacff6bfdf65a824a4be0eec` | `psycopg[c,pool]==3.3.6` | Held out |
| `dcc8c186ef55fa46` | `goauthentik/authentik` | `version/2026.8.5` | `4ec0d19b98da3c329781894cd550da95d3033954` | `psycopg[c,pool]==3.3.4` | Held out |

Excluded on inspection: `pretix/pretix` (X2, `psycopg2-binary`), `dbos-inc/dbos-transact-py` (X4), `Chainlit/chainlit` (X3).

Only repository metadata and these dependency lines have been read for the held-out applications. E1 and E5 haven't been checked for any of them. An application failing a criterion moves to the exclusion log, with no replacement drawn from memory. Inclusion in the pool isn't a claim that an application has a defect.

### 3.5 Rules for held-out applications

- Implementers read no source, issue, merge request or commit of a held-out application until its evaluation is frozen.
- Adapters and registration formats are developed only on development applications.
- A held-out application's registration is written once per condition in section 11, by the onboarding person, with the time logged. If the tooling can't run it, that's recorded as a tooling failure and the tool isn't changed.

### 3.6 Limits of any claim of blindness

**Default label: nonblind.** The evaluation may be called blind only if all of these hold, and the report must say which do not:

1. A person or isolated process with no role in the implementation mines the defects and writes the oracles.
2. Their output stays sealed, with only hashes visible, until the tool, prompts and budgets are frozen.
3. Everything in section 7 and section 8 is frozen and hashed first.

Limits remain even then:

- These are popular public repositories, so both the implementer and Muse Spark may recall their history from training.
- An AI agent working in this repository can't credibly seal material from itself.
- The application names and versions have already been seen.
- Even a true blind result covers only these applications, and only defects whose code paths the tool supports.

## 4. Sampling

### 4.1 Historical fixes

Applied mechanically to history before each pinned version, by the mining role only.

**Candidate rule.** A commit reachable from the pin, within 5 years before the pin's commit date, that either touches a migration path of the application or whose message matches this case-insensitive pattern: `race|deadlock|lock|concurren|backfill|data.?(loss|corrupt)|lost.update|duplicate|migrat\w*.*(fix|fail|revert)|(fix|fail|revert)\w*.*migrat`.

**A candidate is eligible if all hold:**

- H1. It changes a schema migration, data migration, backfill, trigger or the transaction logic around one.
- H2. Its own text (commit, issue or merge request) describes a data-correctness or availability failure, not a style change.
- H3. Its parent and the fix can each be installed and migrated locally.
- H4. The defect can be stated as a property of committed state or returned values, without knowing the fix.
- H5. The trigger needs no external service, multiple databases or scale beyond the budget.
- H6. Its code paths pass through driver paths the tool supports at freeze, as far as a static check can tell. A failure here is logged as "out of tool scope" and counted in the report's denominator.

**Order.** Candidates are sorted by `sha256("deployspecter-live-protocol-v1:history:<owner>/<repo>:<commit>")` and examined in that order, with at most 5 eligible defects per application.

**Target.** At least 10 held-out defects across at least 3 applications. With fewer, the result is reported as a case series with no rate or test.

Rejected candidates and their reasons stay in the sealed log and are published with the results.

### 4.2 Mutation operators and sampling

Used on development applications to tune sensitivity, and on held-out applications only after their historical results are final. Scored separately from historical defects.

| ID | Operator | Applied to |
|---|---|---|
| M1 | Remove `FOR UPDATE` or `FOR SHARE` | Application read |
| M2 | Commit between a read and its dependent write | Transaction block |
| M3 | Remove a transaction block | Application function |
| M4 | Backfill before the new write path or trigger is installed | Migration order |
| M5 | Change `ON CONFLICT DO UPDATE` to `DO NOTHING`, or remove the conflict clause | Upsert |
| M6 | Remove a guard from a batched `UPDATE` (for example a `NULL` or version check) | Backfill |
| M7 | Drop a unique index or constraint the code relies on | Migration |
| M8 | Lower the isolation level | Session |
| M9 | Remove a retry on `40001` or `40P01` | Application function |
| M10 | Remove one side of a dual write | Application function |

**Sampling.** A script enumerates every site per operator, then sorts them by `sha256("deployspecter-live-protocol-v1:mutation:<owner>/<repo>:<operator>:<path>:<line>")`. The first 5 per operator per application are taken before any arm runs.

- **Invalid mutant:** fails to import or migrate, or fails the application's own serial tests in an unrelated way. It's excluded and replaced by the next site, and the count is reported.
- **Possibly equivalent mutant:** survives every arm at full budget. Two reviewers classify it without seeing which arm did what, and a third resolves disagreement. Unresolved mutants stay in the denominator as "undetermined". The score is reported both with and without them.
- **Reach:** whether a workload executed the changed code is measured and reported, so unreached mutants aren't read as tool misses.

## 5. Ground truth, oracles and detection

- The oracle author sees the fix and its discussion. Arm authors don't.
- Each oracle states requirements over committed state and returned values, as code with no import from the tool's generators.
- **Validation before any arm runs.** An oracle needs a hand-written reproducer that fails on the pre-fix version, and the same reproducer must pass on the fix. If not, the defect is dropped as "oracle unvalidated" and logged.
- **Independence check.** Each reproducer is also run by hand on uninstrumented connections, or through `psql` where the path is plain SQL.

**Replay is reported at two levels:**

- **Semantic outcome reproduction:** the same run outcome class, the same requirement ids violated, and the same committed final state (ignoring generated identifiers and masked timestamps).
- **Exact trace reproduction:** an identical behaviour fingerprint, covering statements, results, lock waits and decisions.

Exact trace reproduction isn't always achievable. PostgreSQL chooses deadlock victims and lock wakeups, and the tool doesn't control them.

**Detection** requires all of:

1. The oracle reports a violation on the pre-fix version.
2. Semantic replay succeeds in 3 of 3 attempts. Exact replay is reported but not required.
3. The same workload doesn't violate that requirement on the fix.

Fewer than 3 of 3 is "unstable", not a detection. A violation of a different requirement is an "other finding", which isn't a detection and goes to triage. Written descriptions of a defect in a model's text are never detection.

## 6. Scheduling coverage claims

A driver call the tool can't step individually (for example a batch run as one opaque checkpoint) is recorded as opaque in the trace. A run containing one can't support a claim of complete coverage of schedules, and reports must count such runs per arm. Traffic that the tool can't attribute to a registered connection makes the run `unsupported` and inconclusive. Exhausting a decision tree means only that every choice the tool could control was tried.

## 7. Experimental arms and ablations

Every arm uses the same oracle, budget and supported paths. **The arms can't see requirements, oracles, labels or schedules.**

| Arm | Execution | Workload source | Starting state |
|---|---|---|---|
| R | Statement replay: SQL captured from a plain serial run of E's workloads, parameters fixed, no Python between statements | E's workloads | E's goals |
| A | Live stepping | The application's own test suite, adapted | Its fixtures |
| B | Live stepping | Derived from the schema and registered signatures | Schema-derived boundary values |
| C | Live stepping | Muse Spark proposals | Muse Spark seed goals, resolved literally, with no search. An infeasible goal fails and is counted |
| D | Live stepping | Muse Spark proposals | Muse Spark seed goals, resolved by Phase 9B's deterministic constructor |
| E | Live stepping | Strong deterministic baseline: union of A and B, plus pairs of registered entrypoints | Phase 9B constructor over deterministic goals |
| F | Live stepping | E's deterministic workloads | Muse Spark seed goals, with the constructor |
| G | Live stepping | Muse Spark proposals | E's deterministic goals, with the constructor |

**Ablations.** D, E, F and G form a 2×2 of workload source against state source, so the two contributions can't be confused. A vs E isolates the deterministic additions, C vs D the constructor, and E vs R live execution itself.

**Rules:**

- E is written and frozen before Muse Spark output is seen on any held-out application, with its effort logged. A weak baseline would invalidate the comparison.
- Phase 9B code is brought in only by a separately approved integration step. Until then, D through G can't run.
- Model settings, prompt, schema and provider parameters are frozen and hashed before any held-out request. Prompts are tuned only on development applications.
- Each paid run needs the owner's separate approval, with a request and cost ceiling.

## 8. Budgets and model-call accounting

**Equal execution budget.** Each arm gets the same total of B runs and T seconds of execution per defect. B and T are set on development applications and frozen, with their hash, before any held-out arm runs. Every arm uses the same shape: **3 replicates, each with B/3 runs and T/3 seconds.** A Muse replicate is one independent response. A deterministic replicate is one seeded order of its workloads, with seeds 1, 2 and 3. This way three chances are never compared with one.

**Model calls never consume the execution budget, and are never free.** For every request, log:

- Request identifier, arm, defect or application, and replicate.
- Model name, requested and reported reasoning effort, and reported storage setting.
- Input, output and reasoning tokens, latency and estimated cost, labelled as an estimate and not billing data.
- Outcome: accepted, rejected by the validator, or provider failure.

Report per arm: requests, requests per detection, tokens, estimated cost and total model latency. No retries are allowed except a logged transport failure, and a failed replicate after that counts as a miss. Onboarding requests (section 11) are counted separately.

## 9. Analysis

**Paired detection.** One row per defect, with each arm's outcome. An arm detects a defect if at least one replicate does. Primary tests are exact two-sided McNemar on discordant pairs, with Holm correction across P1 and P2, at overall α = 0.05, and only with at least 10 eligible defects. With 10 defects, at least 6 discordant pairs all in one direction are needed. This is low power, so a non-significant result isn't evidence of no difference. Report exact counts and discordant pairs, with no rate standing alone. Per-replicate detection rates are reported as secondary.

**Time to detection.** For each defect and arm, record runs and execution seconds to first detection in the first detecting replicate, with replicates in fixed order and earlier replicates' spending counted. Non-detection is censored at the total budget.

- Report the paired table, and the median paired difference over all defects, with censored values kept at the budget.
- An exact Wilcoxon signed-rank test (S5) applies only with at least 10 defects detected by both arms. Otherwise, the result is descriptive.
- End-to-end time is reported separately: execution seconds plus model latency, plus logged human time.

**False positives.** Every arm runs at the same budget on each fix version. Two reviewers triage each violation as a false flag, an oracle error or an other finding. Report false flags per arm over fix versions and over runs. An oracle error invalidates that oracle for all arms, and the defect is reported as dropped.

**Inconclusive runs.** Section 12 applies. Primary analysis counts them as misses, and sensitivity analysis removes them.

**Mutation.** Score per arm with and without undetermined mutants, per operator and overall, along with reach.

**AI workload coverage**, for each arm, against the same denominators:

- Registered entrypoints reached.
- Distinct normalized SQL statements executed.
- Line and branch coverage (`coverage.py`) of registered modules.
- Conflicting statement pairs reached on the same row or table.
- Runs with an observed lock wait, and distinct behaviours per run.
- Requirements exercised (non-vacuous).
- Proposal rejection reasons and rates.
- Seed goals feasible, by constructor class.

**Memorization.** Where a held-out defect is detected by a Muse arm, compare with the same prompt on an obfuscated copy (renamed entities, stripped comments and history) if one can be built. Otherwise report it as untested.

## 10. Manual setup measurements

For each application, and each arm where it differs, log with a timer at the time of work and never reconstruct afterwards:

- Developer minutes to boot the application, register entrypoints, arguments and entities, and write or adapt each workload source (A, B and E).
- Developer minutes reviewing and correcting generated material, counted against the arm that generated it.
- Handwritten code, as non-blank, non-comment lines counted by a script, separately for registration, adapters, workload code, seed goals and oracles.
- Adapter lines changed per application.
- Number of human corrections to generated material.
- Who did it, and what they already knew about the application.

Oracle authoring is reported separately, because in practice all arms share it.

## 11. Muse-assisted onboarding design (S8)

Measurements are always collected before any arm runs, because waiting until P1 is known would make the order of onboarding fixed. S8 is analysed only if its condition in section 2 holds. Otherwise the data is reported as descriptive.

- **Conditions.** M: a person writes the registration by hand. O: Muse Spark drafts the same registration format from the source, and a person reviews and corrects it. Neither touches oracles, requirements or defect information.
- **Assignment.** A different person per condition is preferred. With one person, the order per application is set by the parity of the first byte of `sha256("deployspecter-live-protocol-v1:onboarding:<owner>/<repo>")`, even meaning M first, and the report states that learning effects aren't removed.
- **Outcome.** Developer minutes (including review) and handwritten lines per section 10, human corrections, drafts refused by the validator, requests and estimated cost.
- **Detection check.** E's deterministic generator runs from each registration at equal budget, on held-out defects only after the primary analysis is final.
- **Claim.** S8 is supported only if the median reduction across applications is at least 30% on both minutes and lines, and the O-based workloads detect at least as many defects as M-based. With fewer than 3 applications, it's a case series with no claim. Time saved is not claimed if it comes with lost detection.

## 12. Failure and inconclusive accounting

- **Every planned unit stays in the denominator.** Nothing is removed after results are seen.
- Each run ends in a named outcome. `unsupported`, timeout, stall, divergence and connection-loss outcomes are inconclusive, and are reported per arm.
- Reported per defect and arm: detected; unstable; not detected with the budget fully used and no inconclusive run; not detected with some inconclusive runs; blocked by an unsupported path; proposal rejected; seed goal infeasible; provider failure; application failed to boot.
- Tool bugs found during the held-out evaluation are logged. A fix means the affected runs are repeated for every arm, and both results are reported.

## 13. Findings in third-party applications

If a run reproduces a plausible, previously unknown problem in a current version, stop publication of that result. Treat it as confidential, don't infer exploitability or production impact, and prepare a factual report for that project's official private channel. Sending it is the owner's decision. Evaluation reports mention it only after the project has reviewed it. No test runs against any production system, live account or real user data.

## 14. Amendments

This file isn't edited after the freeze. A change is a new file, `EVALUATION_PROTOCOL_AMENDMENT_NNN.md`, next to this one, stating: its number and date; the sections it changes; the reason; whether any held-out material or result had been seen when it was written; and whether it affects a primary hypothesis. Each amendment is committed on its own and listed in the final report. A change made after held-out results are seen is labelled exploratory and can't alter a primary analysis.
