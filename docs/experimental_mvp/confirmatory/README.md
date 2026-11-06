# Confirmatory evaluation readiness: Track C (2026-11-06)

> **Everything under `simulation_1/` is a SIMULATION.** It uses synthetic cases, mock approvals and a simulated
> executor. It is an orchestration rehearsal, not an evaluation, and it is no evidence about DeploySpecter or Muse.
> **No confirmatory study has run.**

## Code (`research_tools/confirmatory/`)

| Module | What it does |
|---|---|
| `approvals.py` | Reads the 24 decisions and the four forms. **A role is satisfied only by a signed row on its own form**, so the owner cannot approve for the curator, the oracle author or reviewer, or the triage reviewers and adjudicator. Real mode also refuses developers as signers, one person in two independent roles, and SIMULATION signatures. |
| `dashboard.py` | Generates `protocol/APPROVAL_DASHBOARD.md`; `--check` fails if it is stale. |
| `intake_v2.py` | The curator's envelope: selection timestamp, reproducible environment, tree digests, contract evidence and a role id. It wraps `corpus_intake.submit`, keeps case directories pure, leak-scans the public envelope, and records it in a hash-chained `INTAKE_LEDGER.jsonl`. |
| `oracle_review.py` | A hash-chained oracle ledger: spec, then an independent review, then corrected-version validation (fixed revision only, curator side, no slot information recorded), then the freeze. No spec or review is accepted after the freeze or after results exist. |
| `run.py` | The orchestrator. **real** refuses unless all seven prerequisites hold (approvals, adoption, registration, frozen independent corpus, approved oracles, policy, no leakage), and even then its executor is not wired. **simulate** runs the whole path in SIMULATION directories only. |
| `sim_executor.py` | SIMULATION stand-in for the arm runners, run inside the evaluator package by `evaluator_sandbox.launch`. |

## Real mode on this repository (`real_mode_check_1/`): exit 3, 31 reasons

- **Approvals (26):** all 24 decisions PROPOSED, plus the #7/#11 form gap. The oracle form has one unlabeled
  signature row, but ruling #7 needs an author and a reviewer.
- **Protocol:** Amendment 003 is not adopted.
- **Registration:** there is no prospective registration record.
- **Corpus:** there is no confirmatory registry.
- **Oracles:** there is no oracle ledger.
- **Policy:** there is no resource and retry policy.

## SIMULATION rehearsal (`simulation_1/`)

| Scenario | Cases | Runs | Primary RA-MDS vs RA-DET | Secondary RA-MD vs RA-DET (Holm) | Post-hoc |
|---|---|---|---|---|---|
| `difference_simulation` | 20 | 600 | RA-MDS better (McNemar 15/0, p = 6.1e-5) | RA-MD better (p = 1.2e-4; 19 cases, 1 voided) | labelled POST-HOC |
| `null_simulation` | 12 | 360 | NO DETECTED DIFFERENCE (p = 1) | NO DETECTED DIFFERENCE | labelled POST-HOC |

**What the rehearsal exercised:**
- **Intake:** intake-v2 submission and a frozen confirmatory registry.
- **Oracle:** spec, review, validation and freeze.
- **Approvals and budgets:** mock approvals (SIMULATION), budgets locked from `PROSPECTIVE_MUSE_PLAN.md` §5
  (plan sha256 `a0b72c99…`), and a complete policy.
- **Evaluator:** the package was built by `evaluator_sandbox.launch` (simulated mode, reduced environment, approved
  oracle only).
- **Results:** a blinded, hash-chained ledger (slots only), a reveal authorization bound to its head, independent
  scoring with the reveals, and paired analysis through `paired_eval`.
- **Counted:** refusals, unsuccessful runs, censored nondetections, the injected fixed-version false positive (voided
  for that arm, queued for triage), invalid proposals and cost against the caps.
- **The null result is reported as valid.**

**Attempt 0 (preserved, `attempt_0_refused/`).** `evaluator_sandbox` refused the package because the synthetic
oracle named the cases' fix path, `app/queue.py`. The oracle now states its property over all sources.

**Note for the real study.** With 12 cases, the predeclared rule (exact McNemar whenever discordant cases exist)
can't reach p < 0.05 with fewer than 6 discordant cases. The 12-case difference trial showed 5/0, p = 0.0625.

**Label safety.** I checked 160 label tokens (commits, prefixes, salts) from the curator-private reveals, which are
held outside the repository, against every file here: 0 hits.

## Limits

- Real role separation needs independent accounts or machines; on one account it is procedural.
- Public diffs and model pretraining can reveal historical defects, so blindness is procedural, not absolute (#20).
- The real executor isn't wired. Wiring it to the reviewed runners is a step for after adoption.
- Linking oracle-ledger role identifiers to the signed forms' people is the owner's record-keeping, not checked by
  code.
