# Final recommendations for proposed decisions #23–#30 (2026-11-10): PROPOSED, not adopted

**Inputs.**
- `AMENDMENT_003_ADOPTION_CANDIDATE.md` (sha256 `ad5ea331…`, unchanged), its 24 decisions and `approval/`;
- `AMENDMENT_003_REVISION_PROPOSAL_r1.md`, `nonblind_corpus/nbc2/AUDIT_2.md` and `track_b/AUDIT_1.md`;
- `PROSPECTIVE_MUSE_PLAN.md`;
- `onboarding/participant_study/` (onb-study/1: crossover, randomized blocks of 4, `MIN_PARTICIPANTS = 12`, at least
  3 per sequence, exact sign test).

**What this file is.** These are the developer's recommendations; none is decided. Each lists the choices that only
the owner or the named reviewers can make. The text implementing them is the revision candidate
`AMENDMENT_003_ADOPTION_CANDIDATE_r1.md` (PROPOSED). A development-only implementation is in
`research_tools/confirmatory_prep/`.

## #23: Corrected-version violations and target clauses

**Scrutiny.**
- ds-nbc/2 showed a genuine violation of an *unrelated* property on the fixed version: saq-c570ba4 clause B3, a
  `max_burst_jobs` overrun that saq also shows at the fix commit.
- The current §6 voids the case for that arm, and triage has three categories: false flag, oracle error, new
  finding. None of them fits "true, but not the target defect".
- Treating such a violation as a detection (ds-nbc/2's S0) credited D2 and M with a burst-overrun "detection".

**Recommendation.**
1. **Target clauses.** The Oracle Author declares, before sealing, the oracle's clauses and which of them encode the
   target defect. The oracle reports the violated clause ids; the interface is `check_clauses`, implemented in the
   preparation path.
2. **Detection** is a SOUND, corroborated violation of a **target** clause on the defective revision.
3. **Void rule (primary, unchanged):** any violation of any clause on the corrected revision voids that case for that
   arm.
4. **Triage** classifies each corrected-revision violation into one of four categories:
   - (a) false flag;
   - (b) oracle error: the case drops for every arm;
   - (c) a pre-existing violation of a non-target clause: reported, the arm's case stays void;
   - (d) a new finding.
5. **Secondary only:** a slot-level analysis that ignores slots whose corrected runs violate non-target clauses
   (ds-nbc/2 S1/S3).

**Unresolved (owner, with the triage reviewers).** Whether category (c) may *un-void* a case in the **primary**
analysis when every target-clause control holds. Recommended: no, keep void as primary, because un-voiding needs
triage outcomes that arrive after results.

## #24: Workload allocation and equal budgets

**Scrutiny.**
- The frozen executor runs one workload per replicate, so only 3 of up to 12 Muse proposals ever ran (36 of 48 in
  ds-nbc/1).
- Equal *schedules* is the right budget currency. Equal *workloads* would penalise an arm that proposes fewer.

**Recommendation.**
- Every arm gets `K` slots × `S` schedules per revision (ds-nbc/2: K = 6, S = 2, so 12 = the plan's B).
- Slot k runs `list[k mod len(list)]`.
- Seeds are `seed(case, k, j)`, the same for every arm.
- There is no early stopping.
- Proposals are admitted in model order up to K; invalid ones stay in the denominator.
- Nothing is substituted or reordered after outcomes.

**Unresolved (owner).** K and S, calibrated on development cases before the freeze. Recommended: K = 6, S = 2.

## #24a: Duplicate deterministic workload cycling

**Scrutiny.** An arm with fewer valid workloads than K re-runs them with new seeds. In ds-nbc/2, D1's 3 workloads
filled 6 slots: more seed diversity, less workload diversity. That asymmetry is real.

**Recommendation.**
- The generator arm (RA-DET-G) must always supply K **distinct** workloads, from rejection sampling over the schema.
- A curated RA-DET or a Muse arm with fewer than K valid workloads cycles them.
- The number of distinct workloads executed is reported per arm.
- A comparison that is conditional on the number of distinct workloads is never the primary analysis.

**Unresolved.** Whether a Muse arm with fewer than K valid proposals may make one more request, counted against R,
to fill its slots. Recommended: no, because R is fixed.

## #25: Fair deterministic baselines

**Scrutiny.** ds-nbc/1's fix-informed baseline biased the comparison toward deterministic workloads. ds-nbc/2's
outcome-blind generator (D2) was the fair comparator for Muse, but it searched a fix-shaped schema written by
fix-aware developers.

**Recommendation.**
- **Add the arm RA-DET-G:** a seeded outcome-blind generator over the case's workload schema only.
- **Primary comparator:** the Muse workload arm is compared against **RA-DET-G**:
  - RA-MD where the integration has no state stage;
  - RA-MDS where it has one, under the candidate's primary.
- **RA-DET:** curated by a fix-blind integrator and reported beside it.
- **Excluded:** fix-informed developer workloads are never a confirmatory arm.

**Unresolved (owner).** Whether RA-DET-G replaces RA-DET as the primary comparator, as recommended, or is added as
a secondary arm only. That changes the meaning of the candidate's primary outcome (§13).

## #26: Statistical independence across applications

**Scrutiny.** 2 of ds-nbc's 4 cases were saq. Defects in one application share code, schema and the integration,
and cases are not independent draws.

**Recommendation.**
- **The unit:** one upstream defect, and several fix commits for one defect make one case. Seeds, slots,
  replicates and mutants are never units.
- **Spread:** at most 3 cases per application, and at least 5 distinct applications among the 10 or more cases.
- **Sensitivity:** a leave-one-application-out analysis is mandatory.

**Unresolved (owner and curator).** The per-application cap and the minimum number of applications.

## #27: Model, prompt and context hashes

**Scrutiny.**
- The candidate's §12 already freezes the model, effort, `store`, templates and schemas.
- The per-case Muse context, the proposer module and `max_output_tokens` are not named.
- `MetaClient` doesn't record the provider-reported model, which §12 requires checking.
- Provider sampling is unseeded.

**Recommendation.**
- **Freeze, by sha256:**
  - the proposer (module and instructions);
  - each case's canonical-JSON Muse context and workload schema;
  - the model id, effort, `store` and `max_output_tokens`.
- **Verify before every request.** A mismatch refuses the request; this is implemented and tested in the
  preparation path.
- **Record the provider-reported model.** This needs a client change before adoption: an implementation item, not
  yet done.
- Save every raw reply on arrival.

## #28: Incomplete runs and time accounting

**Scrutiny.** 20 of 288 ds-nbc/2 runs were INCOMPLETE, deterministic per workload (chancy second-job cancel). Time
can be measured three ways:
- schedules;
- execution seconds;
- end-to-end time, including model latency and generation.

**Recommendation.**
- **INCOMPLETE and REFUSED runs:** they consume their schedules, count as nondetections, are never retried, and are
  reported per arm and per workload.
- **Primary time measure:** buggy-revision **schedules** to first target-clause detection, in slot order, censored
  at K × S.
- **Secondary:** execution seconds (run time only, excluding image builds and lock waits).
- **Tertiary:** end-to-end seconds, adding each arm's generation time (Muse latency or generator time).

**Unresolved.** Whether integrations may reject known-INCOMPLETE workload shapes in `validate`. If they may, the rule
must be fixed before the freeze and applied to every arm.

## #29: Fix-blind integration

**Scrutiny.** In ds-nbc, the people who read the fixes wrote both arms' search space: the workload schemas and the
Muse context.

**Recommendation.**
- **A new role, Integrator:** an independent person without fix exposure (the curator may serve). The Integrator
  writes or approves each case's adapter use, workload schema, Muse context and curated RA-DET workloads.
- **Inputs:** only the public API, the documentation and the pinned tree of the **defective** revision's package.
- **Records:** the Integrator's exposure is ledgered.
- **Leak-check:** every frozen input is checked with `seal_labels.py leak-check`.
- **The developers** may maintain only generic engine code.

**Unresolved (owner).** Whether the curator may also act as the Integrator, or a separate person is required.
Recommended: allowed, with a recorded attestation.

## #30: The human onboarding protocol

**Scrutiny.**
- With *optional* Muse access, Muse was barely used (onb-pilot/2).
- A *mandatory* checkpoint guaranteed use but added about 51–66 s per session (onb-pilot/3).
- The participant package is a 2-application crossover needing 12 or more participants. But the candidate's RA-Q4
  claim rule needs **3 or more applications**, so the package as designed can never support a claim.

**Recommendation.**
- **Assistance:** a mandatory consultation at a fixed workflow point, with accepted, modified or rejected recorded
  and no forced adoption. The control gets the same checkpoint without the model. Further Muse calls are optional.
- **Time:** reported both with and without the checkpoint's model latency.
- **Applications:** never exposed in this project.
- **Governance:** consent, a data owner and retention, an ethics or owner review, and blind scoring.

**Unresolved (owner).** Choose one:
- **(A)** 3 applications in a balanced design (each participant does 2 of the 3; 6 sequences), at least 18
  participants (3 per sequence). This keeps the claim rule.
- **(B)** Keep the 2-application package with 12 or more participants, and change the claim rule to 2 applications.
- **(C)** Keep both as written, and accept that RA-Q4 can only ever be descriptive.

Recommended: (A).

**Power note.** With 12 participants, the exact sign test reaches p < 0.05 only if 10 or more of them favour one
condition (two-sided p = 0.039). Only large effects are detectable.
