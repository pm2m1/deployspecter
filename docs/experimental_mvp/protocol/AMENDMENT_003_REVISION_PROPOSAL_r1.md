# Proposed revision r1 to the Amendment 003 adoption candidate: recommendations only (2026-11-10)

**Status: PROPOSED. Not adopted, signed or registered; no approval is implied.**
- **The candidate is unchanged:** `AMENDMENT_003_ADOPTION_CANDIDATE.md` keeps sha256
  `ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0`. Its 24 decisions stay PROPOSED, and every
  approval form still cites that hash.
- **What this file is:** recommended rulings, drawn from the NONBLIND development experiments (ds-nbc/1, ds-nbc/2,
  onb-pilot/2–3).
- **Who decides:** the owner and the named reviewers. If they accept any part, a **new** candidate is written, with
  a new hash, new forms and new signatures. The current one is never edited.

## Coverage of the eight rulings by the current candidate

| # | Ruling area | Covered by the candidate? | Gap found in the development experiments |
|---|---|---|---|
| R1 | Fixed-version false-positive adjudication | **Partly:** §6 (a violation on the corrected version voids that case for that arm; two reviewers triage it as a false flag, an oracle error or a new finding); #8; the runner implements void plus triage (`run.score`) | No category for a **pre-existing violation of a non-target property**: the property genuinely fails on the fixed version too, as saq-c570ba4's B3 burst overrun did under D2 and M. No rule that an oracle declares which clause encodes the target defect |
| R2 | Workload selection and execution allocation | **No:** §4 fixes B per workload and 3 replicates, but not how many workloads run, in which order, or what happens to the rest | The executor runs one workload per replicate, so an arm uses only its first 3. In ds-nbc/1, 36 of 48 valid Muse proposals never ran (`track_b/AUDIT_1.md`) |
| R3 | Fair deterministic baselines | **Partly:** §2 RA-DET = "curated cases plus the systematic grid" | It doesn't say who curates or writes the workload schema. Fix-informed curation biased ds-nbc/1. An outcome-blind generator (ds-nbc/2's D2) is the fair comparator for Muse, and is not an arm |
| R4 | Case-level statistical independence | **Partly:** §8 (case-paired, ≥ 10 cases), #4, #5 (commit-level fix pair) | Nothing on several cases from one application or subsystem (ds-nbc: 2 of 4 cases are saq), nor on several commits fixing one defect |
| R5 | Muse model and prompt freezing | **Yes:** §12 (model, effort, `store`, templates, context builders, schemas; the provider-reported model is checked) | Add the per-case Muse context and the proposer (`nbc-muse-workloads/1` or its successor) to the hashed set. Note that sampling has no seed, so replies are not reproducible |
| R6 | Time-to-detection and resource accounting | **Partly:** §8 (schedules, Kaplan–Meier, Wilcoxon ≥ 10), §4, §9 | It doesn't define wall-clock time, the treatment of INCOMPLETE runs (20 of 288 in ds-nbc/2, deterministic per workload), lock-wait exclusion, or the per-arm budget when arms have different numbers of workloads |
| R7 | Independent curator and oracle responsibilities | **Yes for labels and oracles:** #6, #7, §15 | Not covered: who writes the **integration's workload schema and Muse context**. In ds-nbc, fix-aware developers wrote both arms' search space (`nbc2/AUDIT_2.md` §6). Also not covered: the target-clause declaration (R1) |
| R8 | Human onboarding protocol and participant allocation | **Partly:** §9 measures, the RA-Q4 claim rule (§8), #10 | It doesn't adopt the participant study package (allocation, consent, blinding, minimum sample). It doesn't define what "Muse assistance" means operationally: in onb-pilot/2 Muse was barely used; onb-pilot/3 needed a mandatory checkpoint |

## Recommended rulings (proposed new decisions #23–#30)

- **#23 (R1).** Every corrected-version violation voids that case for that arm (the current §6, kept). Before
  sealing, the Oracle Author declares which clause or clauses encode the target defect. Blind triage (#8) classifies
  each corrected-version violation as one of:
  - **(a)** a false flag (instrument);
  - **(b)** an oracle error (the case drops for every arm);
  - **(c)** a pre-existing violation of a non-target clause: reported, not a detection, and the arm's case stays void;
  - **(d)** a new finding (frozen §13).

  Detections are scored only on target clauses. The predeclared primary rule stays the void rule. A slot-level
  "discriminating" reading (ds-nbc/2 S1) is a secondary analysis only.
- **#24 (R2).** Every admitted proposal is executed. Each arm gets **K workload slots × S schedules per revision**,
  with K and S fixed at adoption. Rules:
  - **Muse:** proposals are admitted in model order up to K, and invalid ones are counted.
  - **Deterministic arms:** they fill the same K slots, cycling when they have fewer workloads.
  - **Integrity:** there is no early stopping, and no substitution or reordering after outcomes.
  - **Seeds:** the same per (case, slot, schedule) for every arm.
  - **Proven:** this design ran in ds-nbc/2 (`research_tools/nbc2/ds_nbc2.py`, `nbc2/MANIFEST.json`).
  - **Replaces:** §4's "three replicates of one workload each" shape for RA arms, and the `real_executor` selection
    `work[rep % len(work)]`.
- **#25 (R3).** Two deterministic comparators:
  - **RA-DET:** curated workloads written by an **integrator who hasn't seen the fix** (exposure-ledgered).
  - **RA-DET-G (new):** an outcome-blind generator over the case's workload schema only, seeded from the case id
    (as ds-nbc/2's D2).

  The primary Muse comparison becomes RA-MD vs RA-DET-G, with RA-DET reported beside it. Developer-written,
  fix-informed workloads are never a confirmatory arm.
- **#26 (R4).** The unit is one upstream defect. Several commits that fix one defect are one case; seeds, schedules,
  replicates, slots and mutants are never units. At most 3 cases per application in the confirmatory corpus, and a
  sensitivity analysis drops one application at a time. The ≥ 10-case rule (#4) stays.
- **#27 (R5).** Add to §12's hashed set:
  - the proposer module and its instructions;
  - each case's Muse context and workload schema;
  - `max_output_tokens`.

  Record that provider sampling is unseeded, and save every raw reply on arrival (already practice).
- **#28 (R6).** Time to detection uses buggy-revision schedules in slot order, censored at K × S.
  - **Seconds:** the run-time seconds of those schedules only, excluding image builds and lock waits.
  - **INCOMPLETE and REFUSED runs:** they consume their schedules and count as nondetections; they are reported per
    arm and are never retried.
  - **Equal budgets:** an arm's budget is equal in schedules, not in workloads.
  - **Cost:** requests, tokens, latency and estimated USD per arm and per detection.
- **#29 (R7).** Add to #6 and #7:
  - **The integration:** its workload schema and Muse context are written or approved by the curator or an
    independent integrator who hasn't seen the fix, and leak-checked with `seal_labels.py`.
  - **The oracle:** the Oracle Author declares the target clause or clauses (#23).
  - **Exposure:** every artifact's author is recorded in the exposure ledger.
- **#30 (R8).** Adopt `onboarding/participant_study/` as the RA-Q4 protocol.
  - **Allocation:** independent human participants in a two-period crossover (Latin square over application ×
    condition), at least 5 per arm before any verdict.
  - **Assistance:** Muse assistance is a **mandatory consultation at a fixed workflow point** (as onb-pilot/3's
    CHECKPOINT A), with accepted, modified or rejected recorded and nothing forced. The control condition gets the
    same checkpoint without the model.
  - **Applications:** never exposed in this project.
  - **Governance:** consent, a data owner and retention, and an ethics or owner review.
  - **Scoring:** blind.
  - **Claim rule:** unchanged (≥ 3 applications, ≥ 30% median reduction in both minutes and lines, no lost
    detection).

## Impact analysis (if any of #23–#30 is accepted)

| Area | Impact |
|---|---|
| Candidate text | A new candidate file, with §2, §4, §6, §8, §9, §12, §15 and §19 revised; a new sha256 |
| Approval forms and dashboard | All 5 approval documents must cite the new hash. Every role re-reviews and re-signs, since earlier signatures (none exist) would not transfer. The dashboard is regenerated |
| Handoff bundles | Curator and oracle bundles v4 with new checksums: the integrator role, the target-clause declaration and the schema leak-check |
| Confirmatory runner | `real_executor` allocation (#24), a new arm RA-DET-G (#25), void-and-triage categories (#23), seconds accounting (#28); tests updated. ds-nbc/2's tool is a working template |
| Plan | `PROSPECTIVE_MUSE_PLAN.md` §5 budgets are restated as K × S per revision, and K and S are calibrated on development cases before adoption |
| Development evidence | Unchanged: ds-nbc/1 and ds-nbc/2 stay NONBLIND, with their own frozen manifests |
| Cost of not revising | The confirmatory study would inherit the 3-workload executor, fix-informed baselines and an undefined treatment of non-target violations, which are the three weaknesses found in development |
