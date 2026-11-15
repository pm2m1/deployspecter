# Curator quick start (one page, for the labmate)

**What you are:** the Label Curator and, under declared deviation G1, also the Oracle Author for the DeploySpecter
benchmark (`protocol/AMENDMENT_003_r3_OWNER_ADOPTION.md`). You choose real, already-fixed upstream defects. The
owner must not know which ones until scoring is over.

## Before you start

1. Read and sign `handoff/templates/participation_declaration.md` (conflicts of interest).
2. Use **your own machine and account**, not the owner's.
3. Check the protocol hash: `shasum -a 256 docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r3.md`
   must print `af9b2205…fe1c`.

## What you do

1. **Applications:** go through the candidate list in `benchmark_prep/FEASIBILITY.md` in the given hash order. Drop
   any application whose declared dependencies don't include psycopg 3, and log the reason.
2. **Screening:** for each application, screen its fixes from the most recent back, at most 12 each, against
   criteria E1–E6 (`nonblind_corpus/SELECTION_RULE.md`). Keep a screening log.
3. **Oracle first:** for each chosen case, write the oracle **before** opening the fix commit or its tests, from the
   documentation, the public API and the defective revision only (`handoff/templates/oracle_spec.md`). Declare its
   target clauses, hash it, and record the time.
4. **Pin and seal:** pin the fix commit and its parent, and submit the case:
   `python research_tools/corpus_intake.py submit --submission SUBMISSION.json --case-dir CASES/<id> --reveal ~/private/<id>.reveal.json`.
   Hand over only `CASES/<id>/` (archives in random order, plus a public `case.json`).
5. **Limits:** at least 10 cases from at least 5 applications, at most 3 per application, one per upstream defect.
6. **Leak check:** after the owner prepares the runner inputs, run it with `research_tools/seal_labels.py`.
7. **Triage:** blind to the arm (deviation G4), using `handoff/templates/triage_record.json`.

## Never send the owner

- Fix commits, fix diffs, their tests or changelog entries, issue or PR links, or defect summaries.
- Which revision is buggy (`R1`/`R2` mapping), the salt, or any `*.reveal.json` file.
- Your screening log, the names of the cases you picked, or hints ("this one is about locking").
- Anything until the owner tells you, in writing, that scoring is final and the reveal is authorized.

## How long

Rough figures from ds-nbc/1, where 34 screens took the developers one long day:
- **Screening:** about 100 candidates at 5–10 minutes each, so **10–15 hours**.
- **Oracles:** 10–12 oracles at 1–2 hours each, so **10–20 hours**.
- **Pinning and submission:** about 15 minutes per case.
- **Triage:** a few hours, depending on the results.
- **In total:** roughly **25–40 hours** over 2–4 weeks.
