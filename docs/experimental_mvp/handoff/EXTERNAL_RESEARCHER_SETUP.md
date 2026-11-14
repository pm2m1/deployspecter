# Setup guide for an independent curator and oracle team

This guide lets an independent team start case selection and oracle work. **You don't need any of the developers'
experimental results, and you shouldn't use them.** Everything below is setup and procedure. Nothing here has been
done by independent people yet.

## 0. Ground rules
- **Two separate environments:**
  - the **curator and Integrator** work on one account or machine;
  - the **evaluator** runs on another;
  - the developers never receive labels, fixes or oracles before the reveal.
- **Read only what your role needs.**
  - The repository contains the developers' NONBLIND results under `docs/experimental_mvp/` (`nonblind_corpus/`,
    `onboarding/`, `results/`, `historical/`, `rolling_upgrade/`, `muse_contrib/`, `paired_eval/`). Your work
    doesn't need them.
  - Use the excluded-application list (step 3) only to *avoid* those applications.
- **Declarations first:** every person completes `docs/experimental_mvp/handoff/templates/participation_declaration.md`
  (participation and conflicts of interest) before starting.

## 1. Get the code and verify what you are reviewing
```sh
git clone https://github.com/pm2m1/deployspecter.git && cd deployspecter
git checkout research/real-application-mvp
shasum -a 256 docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE.md     # ad5ea331… (original)
shasum -a 256 docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r1.md  # 20403460… (revision r1)
shasum -a 256 -c docs/experimental_mvp/handoff/send_r1/SHA256SUMS_r1
shasum -a 256 docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r2.md  # 476d5c57… (revision r2)
shasum -a 256 -c docs/experimental_mvp/handoff/send_r2/SHA256SUMS_r2
shasum -a 256 docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r3.md  # af9b2205… (r3, review candidate)
shasum -a 256 -c docs/experimental_mvp/handoff/send_r3/SHA256SUMS_r3
```
The owner decides which candidate goes forward. Sign only the forms for that candidate: `approval/` for the
original, `approval_r1/` for r1, `approval_r2/` for r2, `approval_r3/` for r3. r3 is the review candidate.

## 2. Read, in this order
1. The chosen candidate. For r1, also read `protocol/DECISION_COMPARISON_r1.md`; for r2,
   `protocol/DECISION_COMPARISON_r2.md` and `protocol/r2/TIME_BUDGET_r2.md`.
2. `handoff/send_r1/REVIEWER_PACKAGE_r1.md` (r1), `handoff/send_r2/REVIEWER_PACKAGE_r2.md` (r2) or
   `handoff/send_r3/REVIEWER_PACKAGE_r3.md` (r3; also read `protocol/DECISION_COMPARISON_r3.md`): your role's
   section.
3. `handoff/templates/labels.json` (curator) and `handoff/templates/oracle_spec.md` (oracle author).

## 3. Excluded applications (never eligible)
- **Development applications:** chancy, pgqueuer, procrastinate, InvenTree, paperless-ngx,
  langgraph-checkpoint-postgres.
- **Onboarding-pilot applications:** saq, taskiq-postgresql, pgtq, postgres-tq.
- **Other:** anything GitLab-derived, and the frozen protocol's held-out applications (saleor, healthchecks, netbox,
  authentik).

## 4. Environment (curator, Integrator or evaluator machine)
```sh
# Python 3.12, uv, Docker; one disposable PostgreSQL 16 (never a database you care about)
uv sync --extra experimental
research_tools/mvp_pg.sh start            # 127.0.0.1:55441, cluster_name ds_mvp_disposable, random password
.venv/bin/python research_tools/pg_lock.py -- .venv/bin/python -m pytest tests/experimental -q   # sanity check
research_tools/mvp_pg.sh stop
```
Every database tool checks the cluster's identity and refuses anything else.

## 5. Curator workflow
1. **Select candidates** under the frozen rules (`research_tools/corpus_select.py`):
   - one case per upstream defect;
   - at most 3 per application, at least 5 applications (r1 #26);
   - at least 10 cases.
2. **Pin each fix pair:** the fix commit and its first parent, as `git archive` trees of the package directory,
   normalized with `research_tools/sealed_evaluation.py` `normalize_archive`.
3. **Record the documented deployment contract** (single-version unless the application documents otherwise; #19)
   and your exposure.
4. **Seal and submit:**
   - write the labels, then `python research_tools/seal_labels.py seal --labels L --seal S --reveal R`;
   - keep the reveal file private;
   - submit with `research_tools/confirmatory/intake_v2.py`.
5. **Leak-check every input you hand to the evaluator:**
   `python research_tools/seal_labels.py leak-check --reveal R --bundle DIR`.

## 6. Integrator workflow (r1 #29: the curator may serve, with an attestation; r2 #29: always a separate person)
1. **Starting point:** the public API, the documentation and the **defective** revision's pinned tree only. Never
   the fix.
2. **Per case, write:**
   - the workload JSON schema (strict: `additionalProperties: false`, every property required);
   - the Muse context (operation registry and schema DDL);
   - the curated RA-DET workloads.
3. **Leak-check them**, and record your reads in the exposure ledger.
4. **Generator inputs:** `research_tools/confirmatory_prep/prep.py` `generate_det_g` (RA-DET-G) and `freeze_record`
   (#27) show exactly what is derived from your schema and what is hashed. Under r2, `prep_r2.py` adds the request
   plan (#24a), the uniform validator for eligibility rules (#28), exposure records (#29) and the time rule.

## 7. Oracle author and reviewer
1. **Author:** write each oracle from documentation only, exposing `check_clauses(final_state, raw_results)`. List
   the clauses, and declare the **target** clauses (r1 #23).
2. **Reviewer** (a different person): check the encoding and the target-clause declaration, then validate that the
   oracle holds on the corrected revision under the full budget.
3. Each oracle's sha256 goes into the freeze.

## 8. What stays locked until the owner acts
`python research_tools/confirmatory/run.py real` refuses (exit 3) and lists every missing prerequisite. That
includes:
- the signed forms and the adoption commit;
- the registration;
- a frozen corpus and approved oracles;
- the resource and retry policy.

The preparation path (`research_tools/confirmatory_prep/`) is development-only, and refuses real or
non-development execution.
