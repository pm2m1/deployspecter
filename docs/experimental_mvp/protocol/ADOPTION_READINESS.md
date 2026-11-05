# Amendment 003: adoption readiness (2026-11-05)

**Verdict: NOT READY. It stays DRAFT.** No issue below can be settled by an implementation test. Each is a scientific or governance decision, or depends on people who haven't been assigned.

The engineering evidence informs these decisions; it doesn't make them. No approval, oracle attestation or label seal has been created or assumed.

Labels used below:
- **INFORMED:** evidence exists that the reviewer can use.
- **ENGINEERING-RESOLVED:** the tool capability exists, live-tested on development fixtures only.
- **DECISION:** only a person can settle it.

| # | Issue | Status | Evidence | Decision required |
|---|---|---|---|---|
| 1 | Django "core" migrations directory | DECISION, INFORMED | `results/corpus_selection_1`: weblate has 22 app-level `migrations/` directories; the tool outputs NEEDS_PROTOCOL_RULING | Choose `PROPOSED_SELECTION_RULE_AMENDMENTS.md` A1a, A1b or A1c before reading any weblate migration |
| 2 | Alembic "highest-numbered" | DECISION, INFORMED | mlflow has two graphs (70 and 7 revisions), each with one head (`f4a9b2c7d1e6`, `f1a2b3c4d5e6`) | Choose A2a or A2b, and which graph |
| 3 | Driver under an extra | DECISION | weblate, mlflow and saq are extra-only; pgqueuer entered the shortlist that way | Choose A3a or A3b; A3b retroactively removes pgqueuer |
| 4 | Corpus size versus RA-Q3 power | DECISION, INFORMED | 5 blind-eligible candidates; none yet has a sealed defect pair or an oracle | Whether RA-Q3 is preregistered as descriptive only (draft v2 §13 proposes so below 10 cases) |
| 5 | What a "defective version" is | DECISION | none | A historical fix pair, a mutant of the current release, or both, and under what sealing |
| 6 | Label curation and sealing | DECISION, BLOCKING | no curator assigned | Name the curator and the sealing process (hash committed before arms run) |
| 7 | Oracle authorship without the fix | DECISION, BLOCKING | the oracles that exist (Q1–Q3, C1–C3, K1–K2, R1/R2/R5, L1/L2) were written by the tool's developers | Name independent authors, and say how "hasn't seen the fix" is attested |
| 8 | Blind triage of corrected-version violations | DECISION, BLOCKING | the release_seats case shows such triage is needed: the "fixed" build was not a valid control | Name two reviewers, per frozen §9 |
| 9 | Timestamped freeze | DECISION, BLOCKING | no publication yet | Where and how the adoption commit's sha256 is published before the first run |
| 10 | Adapters: registration or tool development? | DECISION, INFORMED | per-library adapters exist: pgqueuer (Driver port), chancy (one-connection pool, worker-per-actor), Django (trusted backend) | Whether their lines and minutes count in RA-Q4 |
| 11 | Chancy fidelity in polling mode | DECISION, INFORMED | the LISTEN path opens an unseamed connection (refuted: `third_party_chancy_notifications_refuted_1`); concurrent workers run as separate actors (`third_party_chancy_concurrent_workers_1`) | Whether a polling-mode run is a faithful execution |
| 12 | Django seam | ENGINEERING-RESOLVED (development fixture only) | the trusted backend is live-tested in both binding modes with refusals (`tests/experimental/test_mvp_django_live.py`) | Whether it's acceptable for weblate-scale projects (Celery workers, many apps), which is untested |
| 13 | Isolation boundary | DECISION, INFORMED | hostile-grade isolation BLOCKED: only runc is available (`results/isolation_runtime_scan_1`) | Whether restricted runc is acceptable for pinned public code, or RA waits |
| 14 | Gate 2A supervision | DECISION, BLOCKING | the Gate A2 demonstration is still blocked | Must not bypass the Gate A2 decision; the owner decides whether RA needs Gate 2A |
| 15 | Budget values | DECISION, INFORMED | development schedule counts: e.g. Django ledger buggy 24 / fixed 8; release-path cases 10–92 | Fix B, T, R, P and C, and the development cases they come from |
| 16 | "Equal model budget" | DECISION | draft: zero for deterministic arms | Confirm, or change to equal compute time |
| 17 | Hypothesis scoring | DECISION, INFORMED | Muse hypotheses distinguished nothing beyond the oracle (`results/release_compare_1`) | Secondary or exploratory |
| 18 | Citing RA-SMOKE evidence | DECISION | draft text: engineering evidence only | Confirm |

**Blocking for any adoption:** 6, 7, 8, 9 and 14. They need named people and the owner's decision. They can't be settled by the tool's authors.
