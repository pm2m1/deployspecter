# Amendment 003: the owner's decision document (2026-11-06)

**Status: every decision is PROPOSED. Nothing here is accepted, signed, adopted or registered.**

**What is approved.** The exact text of `AMENDMENT_003_ADOPTION_CANDIDATE.md`, identified by sha256
`ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0`. Check it first:
`shasum -a 256 docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE.md`.

**How to approve.**
1. Tick a box on the named form line, then sign the form's signature row. Marking "amend" or "reject" on any line
   creates a new candidate and a new sha256, and every form is signed again.
2. After all signatures, `python research_tools/verify_amendment.py` must report no integrity problem.

This document is a reading aid. It changes no ruling. Each ruling's binding text is the candidate line cited
("cand. L209").

**Forms:**
- **O:** `approval/OWNER_APPROVAL.md`
- **C:** `CURATOR_ACCEPTANCE.md`
- **R:** `ORACLE_REVIEWER_ACCEPTANCE.md`
- **T:** `TRIAGE_REVIEWERS_ACCEPTANCE.md`

Signature rows: O L38, C L21, R L19, T L18–20.

## A gap in the forms (found while preparing this; nothing was edited)

The register names the owner as co-decider on **#6, #7 and #8**, but `OWNER_APPROVAL.md` has no line for them. As
written, the owner's approval of the independence rulings would be implicit.

**Proposed fix, for the owner to authorize:** add three lines (#6, #7, #8) to `OWNER_APPROVAL.md` *before anyone
signs*. The forms are not part of the hashed candidate, so its sha256 is unaffected. Until then, the owner's
consent to #6–#8 is **unrecorded**, and adoption step 3 cannot be complete.

**Update 2026-11-06:** at the owner's request, lines #6, #7 and #8 (marked PROPOSED) were added to `OWNER_APPROVAL.md`. Nothing is signed or accepted; the owner's consent is recorded only when the form is signed.

## Corpus and selection

| # | Proposed ruling (binding text) | Why | If accepted / if rejected | Approval action |
|---|---|---|---|---|
| 1 | The Django core migration area is the app with the most `NNNN_*.py` files, by file names alone (cand. L209) | a mechanical rule that reads no migration contents, decided before exposure | weblate's `trans` (102 files) is selectable / multi-app Django projects stay NEEDS_PROTOCOL_RULING (or A1b/A1c) | O L12 **and** C L12 |
| 2 | The Alembic area is the single head of `alembic.ini`'s graph, from the revision headers; ambiguity needs a written curator ruling (cand. L210) | it matches what `alembic heads` reports and reads only metadata | mlflow `db_migrations`, head `f4a9b2c7d1e6` / Alembic projects stay unresolved (or A2b) | O L13 **and** C L13 |
| 3 | A PostgreSQL driver under a PostgreSQL-named optional extra counts as declared; the extra is recorded (cand. L211) | keeps PGQueuer's past admission consistent and applies to all candidates alike | admits weblate, mlflow and saq on this point / A3b retroactively removes PGQueuer, recorded | O L14 |
| 4 | RA-Q3 is descriptive unless ≥ 10 eligible blind cases exist at the freeze (cand. L212) | no confirmatory claim from a tiny sample (implemented in `paired_eval.py`) | the small-corpus result is descriptive / the analysis rule is undefined for small corpora | O L15 |
| 5 | A historical case is a commit-level fix pair from hash-pinned trees; release pairs are confounded; mutants are secondary and never called defects (cand. L213) | it isolates the fix's effect, which is how `chancy_2895c4a` was built | — / case definition is undefined | O L16 |
| 19 | Mixed-version results count only under the application's documented rolling or blue-green procedure; the curator records the citation (cand. L214) | avoids calling unsupported deployments defects (Chancy's P7 cells) | out-of-contract results are exploratory / they could be reported as defects | O L27 **and** C L15 |
| 21 | Version-named `.py`/`.sql` migration directories in the core package count as a migration area (cand. L215) | procrastinate-style projects are otherwise unselectable | prospective SQL-migration projects become eligible / they stay NEEDS_PROTOCOL_RULING | O L29 |
| A4 | The module that applies migrations is recorded per framework, as metadata (cand. L216) | a fixed vocabulary, file names only | — / exposure records are inconsistent | O L31 |
| A5 | The selection tool's two content reads (`INSTALLED_APPS` literals, Alembic headers) are metadata-level exposure (cand. L217) | they are needed by #1 and #2, and reveal nothing about defects | the tool runs as built / it must switch to file names only, and #1/#2 need rework | O L32 |

## Independence (each needs real, named, independent people)

| # | Proposed ruling | Why | If accepted / if rejected | Approval action |
|---|---|---|---|---|
| 6 | An independent Label Curator seals labels and submits cases through `corpus_intake.py`; the runner never sees labels before scoring (cand. L218) | blindness needs someone other than the developer to hold labels | the sealed workflow (rehearsed on synthetic data) becomes the confirmatory path / no case can be BLIND_ELIGIBLE | C L14; **owner: no line yet (see the gap)** |
| 7 | An independent Oracle Author writes properties from documentation and the public API only; a reviewer checks the encoding; specs are hashed into the freeze (cand. L219) | both developer-written oracles had errors (procrastinate E1, E2) | independent oracles / RA-Q3 cannot be confirmatory | R L12; **owner: no line yet** |
| 8 | Two arm-blind Triage Reviewers classify every corrected-version violation; an Adjudicator resolves disagreements; κ is reported (cand. L220) | false-positive classification must not be the developer's | triage per `templates/triage_record.json` / false positives are unclassified, and the confirmatory study is blocked | T L12 (all three rows sign); **owner: no line yet** |
| 9 | The adoption commit's sha256 is registered (OSF, plus an RFC 3161 or OpenTimestamps token) before the first run (cand. L221) | proves the protocol preceded the results | public registration, by the owner only / there is no tamper-evident precedence | O L17 |
| 14 | RA makes no Gate 2A/A2 claim; Gate A2 stays blocked; results are never pooled (cand. L222) | keeps the certified gates' boundary intact | — / RA results could be misread as gate evidence | O L22 |
| 20 | Exposure ledgers record "training-data exposure not excludable"; blindness rests on the process (cand. L223) | public fixes may be in model training data | honest exposure records / a blindness claim that can't be supported | O L28 |

## Instrumentation and analysis

| # | Proposed ruling | Why | If accepted / if rejected | Approval action |
|---|---|---|---|---|
| 10 | A per-application adapter counts as RA-Q4 onboarding work; generic engine capability is tool development (cand. L224) | separates onboarding cost from tool building (`research_tools/onboarding/`) | onboarding is measured per `onboarding/STATUS.md` / RA-Q4 has no unit | O L18 |
| 11 | LISTEN/NOTIFY-disabled (polling) runs are faithful for statement semantics; notification-dependent defects are excluded and reported (cand. L225) | the stepped pool cannot step notifications | polling is accepted, with exclusions reported / every run's fidelity is disputed | O L19 **and** R L13 |
| 12 | The Django seam is development-only until a development Django app of comparable scale runs through it (cand. L226) | only the small fixture app has used it | Django cases wait / they could enter unvalidated | O L20 |
| 13 | Restricted runc is acceptable only for pinned, publicly released, statically reviewed code, disclosed in every result (cand. L227) | it is not hostile-grade isolation | as now / third-party execution needs a stronger boundary first | O L21 |
| 15 | Budgets: B = 12 schedules per workload per build, T = 900 s per case per arm, 3 replicates; R = 2, P = 6, C = $1 per case per arm (cand. L228) | equal, stated before any run | the cost ceiling per case is known / the budgets are unfixed and the comparison is invalid | O L23 |
| 16 | The deterministic arms' model budget is zero (cand. L229) | isolates Muse's contribution | — / arms are confounded | O L24 |
| 17 | Model-proposed invariant hypotheses stay exploratory (cand. L230) | they are unvalidated by independent authors | — / they could be counted as oracles | O L25 |
| 18 | RA-SMOKE, the simultaneous Chancy pilot, `chancy_2895c4a` and the procrastinate rolling upgrade are development evidence only (cand. L231) | all were chosen and encoded knowing the outcome | — / NONBLIND evidence could be presented as confirmatory | O L26 |
| 22 | Typed-parameter corroboration reports compared, mismatched and unchecked values separately; a confirmatory result needs 0 mismatched; unchecked values are disclosed per type (cand. L232) | unknown representations must never pass as matched | as implemented (`corroborate.py`) / the corroboration standard is unstated | O L30 |

## After every form is signed (owner actions; `approval/ADOPTION_CHECKLIST.md`)

1. Commit the candidate **byte-identically** as `docs/research/live_stepping/EVALUATION_PROTOCOL_AMENDMENT_003.md`.
2. Run `verify_amendment.py`. It must report "adopted, consistent".
3. Register both sha256s publicly, with a trusted timestamp, before any run (#9). This step is the owner's alone,
   and public.
4. The curator submits and freezes cases. The oracles are written, reviewed, hashed and validated on the fixed
   revisions. The curator's leak check gives 0 hits.
5. Only then may a confirmatory run start.

Until all of this is done, every evaluation in the repository, including the sealed-workflow rehearsal, is
simulated or NONBLIND development work.
