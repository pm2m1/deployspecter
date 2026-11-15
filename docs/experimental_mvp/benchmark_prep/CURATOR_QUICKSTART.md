# Curator quick start (one page, for labmate 1)

**What you are:** the Label Curator for the DeploySpecter benchmark (`protocol/AMENDMENT_003_r3_OWNER_ADOPTION.md`).
You choose real, already-fixed upstream defects and seal them. A second labmate, the Oracle Writer, writes each
case's oracle without ever seeing the fix. The owner must not know which cases you chose, or which revision is
defective, until scoring is over.

## Before you start

1. Read and sign `handoff/templates/participation_declaration.md` (conflicts of interest).
2. Use **your own machine and account**, not the owner's.
3. Check the protocol hash: `shasum -a 256 docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r3.md`
   must print `af9b2205…fe1c`. The full rule you apply is `osf/SELECTION_RULE.md`.

## What you do

1. **Applications:** go through `benchmark_prep/FEASIBILITY.md` §3 in hash order (`SELECTION_RULE.md` S3). Record
   each application's driver verdict **before reading any of its fixes**:
   - psycopg 3 as a declared dependency: eligible on the driver rule;
   - a Django application pinned to psycopg2: run the S4 check (psycopg 3 installed, Django 4.2 or later, migrations
     and tests unchanged) and log the commands and exit codes;
   - otherwise: excluded, with the reason.
2. **Screening:** for each eligible application, screen its fixes from the most recent back, at most 12 each,
   against E1–E6 (`nonblind_corpus/SELECTION_RULE.md` §4). Keep a screening log.
3. **Contract-area statement:** for each chosen case, write one or two neutral sentences naming the documented
   behaviour of the public API that the case concerns, from the documentation only. Don't describe the bug, and
   don't use words from the fix, its tests, the changelog, the issue or the PR. Record the statement in your
   exposure ledger before you send it.
4. **Hand to the Oracle Writer:** the application name, the defective revision's commit and source tree (not
   labelled as defective), links to its public documentation, and the statement. Nothing else.
5. **Wait for the oracle's hash.** Only then pin the fix commit and its parent and seal the case:
   `python research_tools/corpus_intake.py submit --submission SUBMISSION.json --case-dir CASES/<id> --reveal ~/private/<id>.reveal.json`.
6. **Check the oracle on the corrected revision** under serial workloads on your machine (this stands in for an
   oracle reviewer, gap G2). If it fails there, send the writer only "the oracle rejects correct behaviour on a
   serial run", never the fix.
7. **Hand to the owner:** only `CASES/<id>/` (both archives in random order, and a public `case.json`).
8. **Limits:** at least 10 cases from at least 5 applications, at most 3 per application, one per upstream defect.
9. **Leak check:** after the owner prepares the runner inputs, run `research_tools/seal_labels.py` on them.
10. **Triage:** with the Oracle Writer, independently and blind to the arm, using
    `handoff/templates/triage_record.json`.

## Never send the owner

- Fix commits, fix diffs, their tests or changelog entries, issue or PR links, or defect summaries.
- Which revision is buggy (the `R1`/`R2` mapping), the salt, or any `*.reveal.json` file.
- Your screening log, the names of the cases you picked, the contract-area statements, or hints.
- Anything until the owner tells you, in writing, that scoring is final and the reveal is authorized.

## Never send the Oracle Writer

Anything about the fix: the fix commit or diff, the corrected revision, its tests, the changelog entry, the issue
or PR, or a description of the bug.

## How long

Rough figures from ds-nbc/1, where 34 screens took the developers one long day:
- **Driver checks:** 1–3 hours per application that needs S4.
- **Screening:** about 100 candidates at 5–10 minutes each, so **10–15 hours**.
- **Statements, pinning, sealing and corrected-revision checks:** about 1 hour per case.
- **Triage:** a few hours, depending on the results.
- **In total:** roughly **30–45 hours** over 3–5 weeks.
