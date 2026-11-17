# Stage 1 (case selection and oracle transcription): summary

Label: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

Date: 2026-11-17. Every `select` invocation is in `selection_runs/SELECT_LOG.md`, and every departure from the plan is in
`../results/DEVIATIONS.md` (D1–D5).

## Result

**Final selection:** `selection_7`. **4 cases from 3 applications**, after 100 screens (the screen budget). The
registered target, 10 cases from at least 5 applications, is not met, so **RA-Q3 is descriptive only**
(`SELECTION_RULE_SOLO.md` step 4).

| Case | Application | Driver | Assertions | Expressed (all `uses_deployspecter_code: false`) | Not expressible | Strict subset |
|---|---|---|---|---|---|---|
| `pretix-409e64d5f2` | pretix/pretix | S4 | 4 | 3 | intermediate_state 1 | no |
| `plane-b2717dc9f4` | makeplane/plane | psycopg 3 | 6 | 1 | in_memory_return_value 3 (verified), not_in_final_state 2 | no |
| `plane-4605f0078f` | makeplane/plane | psycopg 3 | 26 | 2 | in_memory_return_value 10 (verified), intermediate_state 2, not_in_final_state 12 | no |
| `wger-bcfdde1a96` | wger-project/wger | psycopg 3 | 3 | 1 | not_in_final_state 2 | no |

- **Strict subset:** 0 cases. Every admitted case has at least one investigator-judgement reason.
- **Expressed assertions:** every one is a generalisation of the test's scenario-specific assertion to an invariant
  over the application's tables (each spec's `note`). That is investigator judgement too.
- **All four cases are Django applications.** Under D3 (r3 #12) none of them runs until the Django seam is
  validated on a non-candidate Django application of comparable scale.

**S4 verdicts** (`S4.json`, logs in `s4/`): TandoorRecipes/recipes pass; getsentry/sentry **fail** (migrations under
psycopg 3); pretix/pretix pass; zulip/zulip **fail** (a declared dependency, `django-bitfield`, needs psycopg2).

**Screens (`selection_7/SKIPS.jsonl`):**
- 37 `driver`;
- 24 `s4_failed`;
- 20 `no_regression_test`;
- 15 `adapter_refused`;
- 4 admitted.

**Refused by `maintainer_oracle.py check-spec`** (15 cases, `REFUSED.json`, coverage records in `coverage/`):

| Reason | Cases |
|---|---|
| a not-expressible reason fails mechanical verification (an in-memory assertion whose expression makes a call, or a name not bound once from a call; `assertRaisesRegex` isn't `pytest.raises`) | 10 |
| no assertion is expressible over the final state | 3 |
| both | 2 |

Admitted then refused, by application: plane 7, airflow 5, kiwitcms 1, wger 1, mlflow 1.

Specs and coverage records are hashed in `SPECS.sha256`. Each spec was committed before any work on its case's
integration.

## Concurrency dependence of the admitted cases (added 2026-11-17, before any arm or oracle-error check)

Added on the owner's instruction at Gate 1 (verbatim): "Before any Stage 2 work, add to STAGE1_SUMMARY.md (new
section, dated, before any arm or oracle-error check runs) a classification of each admitted case:
concurrency-dependent yes/no, with a one-line reason from the fix and test. Commit and push it. This is descriptive
only and changes no registered analysis."

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

"Concurrency-dependent" means the defect needs two or more interleaved transactions or requests to show. It is read
from the fix diff and the chosen regression test functions only. No arm has run and no oracle-error check has run.

| Case | Concurrency-dependent | Reason (fix and test) |
|---|---|---|
| `pretix-409e64d5f2` | no | The fix changes `self._voucher_use_diff[cp.voucher] += 2` to `+= 1` in cart extension; the test extends one expired position with a one-use voucher in a single `commit()`. |
| `plane-b2717dc9f4` | no | The fix narrows `WorkSpaceBasePermission` for PUT/PATCH from `[Admin, Member]` to `Admin` and makes `deleted_at` read-only; each test makes one request as one user. |
| `plane-4605f0078f` | no | The fix allowlists `{"role": ...}` in `partial_update` (mass assignment) and adds sole-admin guards; every test makes one request. The fix also narrows a stale full-row save ("a concurrent removal … isn't reverted"), but no chosen test exercises that. |
| `wger-bcfdde1a96` | no | The fix matches an existing weight unit by gram as well as name on OFF re-import; the test imports twice in sequence and expects one unit. |

**Result: 0 of 4 admitted cases are concurrency-dependent.** Each defect shows under a single serial request. This is
descriptive only and changes no registered analysis: the cases, arms, budgets and detection rule stay as registered.
