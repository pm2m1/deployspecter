# Results: the preregistered single-investigator evaluation (https://osf.io/34ytz)

**"preregistered, automation-blinded, single-investigator. Not independent"**

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

- Registration: https://osf.io/34ytz (DOI `10.17605/OSF.IO/34YTZ`), registered 2026-11-17T00:39:37Z.
- Frozen plan and code: `FREEZE.json` sha256 `6fb0a189eb68c2351203bb5764387432c824e20d11a117a389dcdacb9aeced47`,
  commit `312d0af`.
- `build_freeze.py --check` passed at the start of every stage.
- Closed on 2026-11-17 on the owner's instruction (`DEVIATIONS.md` D6).

## Outcome

**The study terminated at Stage 2 with 0 eligible historical cases.**
- **Not estimable:**
  - the primary comparison (RA-Q3: the Muse workload arm RA-MD against RA-DET-G, exact McNemar);
  - every other registered comparison: against RA-DET; time to detection; the strict-subset and detection-level
    sensitivity analyses; leave-one-application-out; RA-Q1 and RA-Q2.
- **No arm ran.** No workload was proposed, generated or executed for any case.
- **No mutants were generated.** The plan generates them per application of the selected cases, and none remain. So
  there is no mutation score and no `RESULTS.json`.
- **Muse spend: $0.00.** No Muse request was made.

This result says nothing about Muse Spark, the outcome-blind generator or the curated workloads, in either direction.

## How the cases were lost

### Screening (`solo/selection_8`, the last registered re-selection)

The frozen `select_cases.py` screened the 20 frozen candidate applications in sealed-seed order. It stopped at the
100-screen budget.

| Skip code | Screens | Meaning |
|---|---|---|
| `driver` | 37 | not psycopg 3, and not a Django application on psycopg2 eligible under rule S4 |
| `s4_failed` | 24 | a Django application whose S4 check failed |
| `no_regression_test` | 20 | the fix changed no regression test |
| `adapter_refused` | 19 | admitted, then refused (below) |
| admitted | **0** | |
| **Total** | **100** | |

Before the last refusals, `solo/selection_7` admitted 4 cases from 3 applications: pretix 1, plane 2 and wger 1.
That was already below the registered target of 10 cases from at least 5 applications, so RA-Q3 would have been
descriptive only. Every `select` pass is in `solo/selection_runs/SELECT_LOG.md` (12 passes, `selection_1` …
`selection_8`).

### Refusals (`solo/REFUSED.json`, 19 cases)

| Reason | Cases | Channel |
|---|---|---|
| a not-expressible reason fails mechanical verification (`in_memory_return_value` whose expression makes a call, or a name not bound once from a call; `assertRaisesRegex` isn't `pytest.raises`) | 10 | `maintainer_oracle.py check-spec` |
| no assertion of the regression test is expressible over the final database state | 3 | `check-spec` |
| both of the above | 2 | `check-spec` |
| `E3: r3 #12 Django seam not validated` | 4 | D3 seam validation (below) |

By application: plane 9, airflow 5, wger 2, kiwitcms 1, mlflow 1 and pretix 1.

### S4 verdicts (`solo/S4.json`, logs in `solo/s4/`)

Rule S4: a Django application on psycopg2 is admitted only if it runs unmodified under psycopg 3. That means
migrating from an empty database, then running a test set fixed before the run under both drivers, with no failure
only under psycopg 3.

| Application | Verdict | Evidence |
|---|---|---|
| TandoorRecipes/recipes | pass | the same 36 tests fail under both drivers (of 1,316) |
| getsentry/sentry | fail | migration exits 1 under psycopg 3 (`'Jsonb' object has no attribute 'adapted'`); 534 tests fail only under psycopg 3 |
| pretix/pretix | pass | the same 15 tests fail under both drivers (of 6,121) |
| zulip/zulip | fail | its dependency `django-bitfield` needs psycopg2: `module 'psycopg' has no attribute 'extensions'` |

### The 4 cases admitted before E3 (`solo/STAGE1_SUMMARY.md`)

| Case | Assertions | Expressed | Strict subset | Concurrency-dependent |
|---|---|---|---|---|
| `pretix-409e64d5f2` | 4 | 3 | no | no: `_voucher_use_diff[...] += 2` becomes `+= 1`; one `commit()` in the test |
| `plane-b2717dc9f4` | 6 | 1 | no | no: a PUT/PATCH permission check (`[Admin, Member]` becomes `Admin`); one request per test |
| `plane-4605f0078f` | 26 | 2 | no | no: mass assignment in `partial_update`; one request per test |
| `wger-bcfdde1a96` | 3 | 1 | no | no: match an existing weight unit by gram on re-import; sequential imports |

- **Strict subset: 0 cases.** Every case has at least one not-expressible reason that rests on investigator
  judgement.
- Every expressed assertion generalises the test's scenario into an invariant over the application's tables, which
  is investigator judgement too.
- **0 of 4 cases are concurrency-dependent.** The classification is descriptive. It was added after Gate 1 and
  before any arm or oracle-error check, from the fix diff and the chosen test functions only (commit `1935251`).
- All 4 are Django applications, so under D3 none could run before the seam validation.

## The Django seam validation (D3, r3 #12)

Records: `django_seam_validation/`, which holds `PLAN.md`, `LOG.md`, `VERDICT.json`, `evidence/` and
`EVIDENCE.sha256`.

- **Plan:** committed before any run through the seam (`b1881ad`). The application was **paperless-ngx v3.4.2**, run
  unmodified from its published image (`sha256:d704e84f…`): Django 5.2.18, psycopg 3.3.4.
  - The seam's code was used as committed and not changed.
  - Addendum 1 (`57a6455`) added a driver dry run on Django's stock backend only. It found two driver errors, fixed
    in the driver (`9be9540`).
  - The validation ran once (`b4ce19c`).
- **Workloads:** 5, each two concurrent REST API requests through the application's full middleware. Each ran under
  5 schedules (two serial, one alternating, two seeded random), 25 runs in all, compared with Django's stock backend.

| Criterion | Result |
|---|---|
| P1: the application's full migrations run through the seam, corroborated | **pass**: 109 migrations, 1,117 statements, corroboration ok, 0 mismatched |
| P2: every run completes, corroborated, with no DeploySpecter exception | **FAIL**: 5 of 25 runs time out (W2_S3, W2_S4, W5_S3, W5_S4, W5_S5) |
| P3: the alternating schedule interleaves the two actors | **pass** in all 5 workloads |
| P4: the serial seam run's final state equals the stock backend's | **FAIL** for W3; W1, W2, W4 and W5 match |
| **Verdict** | **FAIL**, so each admitted case is refused with E3 |

Corroboration was ok with 0 mismatched parameters in all 26 seam runs, including the five that timed out.

### Two limits of statement-level database stepping

Both come from DeploySpecter's design. It controls an application only at its database statements, and gives each
actor exactly one database connection.

1. **Synchronisation outside the database (P2).**
   - paperless-ngx's search index (`documents/search/_backend.py`) takes a file lock and reads from the database
     while it holds it.
   - Under an interleaved schedule, the actor holding the lock waits at the gate for its next statement. The other
     actor waits on the lock in Python and sends no statement.
   - The scheduler grants only database operations, so it can't make either actor progress, and the run times out.
   - A longer time limit would not help.
   - In production the same lock serializes paperless's processes. The tool can't observe or order such a lock, so
     interleavings through it can't be explored.
2. **A reconnect is refused (P4).**
   - W3's `bulk_edit` starts an application task. It ran inline, because Celery was configured eager, as the plan
     says.
   - Celery's Django integration closes the database connection when a task starts. The application's next query
     makes Django open a new connection.
   - The seam refuses that by design: "a second or reopened connection" (`django_backend/base.py:61`).
   - The application turns the error into HTTP 400, so W3 diverges from the stock backend.
   - The rule exists so that every statement of an actor stays on the one stepped, corroborated session.
   - Any code path that closes and reopens its connection inside one operation can't run through the seam.

The P2 failure doesn't depend on the eager-Celery choice: the search index is updated directly by the document
request.

## Incidental observations (NON-BLIND, EXPLORATORY, not study results)

These come from the seam validation runs. The application wasn't selected by the registered rule, and the workloads
were written to exercise the seam, not to find defects. No oracle was registered, nothing was replicated, and none of
this enters any registered analysis.

- **W1** (two `POST /api/tags/` with the same name):
  - serially, the second request gets 400 from validation, on both the stepped and the stock backend;
  - under the alternating schedule (W1_S3), both requests pass validation, and the second gets **HTTP 500**:
    `IntegrityError: duplicate key value violates unique constraint "documents_tag_unique_name_owner"`.
- **W4** (`DELETE` a tag while a `PATCH` adds it to a document):
  - serially (stock, and W4_S1) the PATCH gets 400;
  - under W4_S3, W4_S4 and W4_S5 it gets **HTTP 500**:
    `IntegrityError: … violates foreign key constraint "documents_document_tags_tag_id_…_fk_documents_tag_id"`.

In both, the database constraints held. The final state stayed consistent, and only the response code differs.
These are check-then-act races, surfacing as unhandled errors.

## Deviations (`DEVIATIONS.md`, each written before it took effect)

| | Deviation | Effect on what counts as a case, arm or budget |
|---|---|---|
| D1 | PostHog mirror cloned in steps. Addendum 1: no low-speed limit, other mirrors fetched in parallel. Addendum 2: heads and tags only, without `refs/pull/*` | none; `select` reads only the default branch |
| D2 | S4 commands run through the application's own Django entry point (`sentry django …`, `sentry upgrade`) | none |
| D3 | Owner's decision: the Django seam is validated on InvenTree or paperless-ngx before any Django case runs; on failure, E3 refusals | decided the outcome: the validation failed and all 4 cases were refused |
| D4 | Owner's decision: E2–E6 applied through the existing refusal channels (`check-spec`; E3 at integration), then `select` is re-run | the 19 refusals |
| D5 | Owner's decision: one generic final-state read (every application table via `to_jsonb`, plus `db_now` as a DeploySpecter source). Addendum: the value encoding | none; it fixed what oracles may read |
| D6 | Owner's instruction: the study is closed as terminated at Stage 2; no Stage 3–5 work, no mutants | every registered comparison not estimable |

The S4 checks for pretix and Zulip each needed further attempts because the environment setup failed (a missing
compiler, git `safe.directory`, services not started). Each failed attempt was recorded in a plan addendum, committed
before the next attempt.

## What a future study would need

1. **A population with real concurrency defects.**
   - The frozen topic and fix expressions found authorisation and data-integrity fixes, not races.
   - 0 of the 4 admitted cases needed concurrency to show.
   - A study of schedule exploration needs cases whose defect depends on interleaving, and a selection rule that
     tests for that before admission.
2. **Oracles over database state.**
   - 15 of the 19 refusals came from `check-spec`. The maintainers' regression tests assert on HTTP responses,
     in-memory return values or other things outside the final database state, in ways the oracle format couldn't
     carry or mechanically verify.
   - Of the 4 admitted cases, none had an oracle free of investigator judgement (strict subset 0).
   - Oracles need a source that speaks about stored state, or a registered, independent way to write them.
3. **Handling synchronisation outside the database.**
   - Real applications hold file locks, in-process locks and task-queue boundaries across database statements, and
     reconnect inside one operation.
   - A stepping tool must either see and order such points, or detect them and refuse the case before any arm runs,
     instead of timing out.
   - The Django seam should be validated on a large application before it is registered as an instrument.

## Records

| What | Where |
|---|---|
| Selection runs, skips and log | `solo/selection_1` … `selection_8`, `solo/selection_runs/SELECT_LOG.md` |
| Refusals | `solo/REFUSED.json` |
| Maintainer specs and coverage | `solo/maintainer_specs/`, `solo/coverage/`, `solo/SPECS.sha256` |
| S4 checks | `solo/S4.json`, `solo/s4/` |
| Stage 1 summary and concurrency classification | `solo/STAGE1_SUMMARY.md` |
| Seam validation | `results/django_seam_validation/` |
| Deviations and progress | `results/DEVIATIONS.md`, `results/PROGRESS.md` |
