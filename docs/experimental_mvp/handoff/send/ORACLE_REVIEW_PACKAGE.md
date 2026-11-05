# DeploySpecter: independent oracle author and reviewer package (ready to send; NOT sent)

**Status.** Prepared 2026-11-06. **No recipient has been chosen, contacted or agreed to anything.** This package is separate from the curator's. The oracle author must not be the curator for the same cases (#7), and receives none of the curator's material.

---

## Cover note (for the owner to complete and send)

> Dear [ORACLE AUTHOR NAME],
>
> We'd like to ask you to write, and separately review, the correctness properties ("oracles") that DeploySpecter will check after running real PostgreSQL applications through an upgrade. Your oracles must come from each application's public documentation and its current release's public behaviour, and **not** from its bug fixes, its tests or its changelog entries about defects, so that a later detection can't have been shaped by knowing the answer.
>
> There are two parts:
>
> - **A:** author oracles for the sealed cases the curator selects. You'll receive only the application names and versions, never the labels.
> - **B:** a review of three oracles the tool's developers already wrote for development cases, which do have known histories. Part B tests the review process itself, and isn't blind.
>
> The estimated effort is 2–4 hours per case for Part A, and 2–3 hours for Part B. You may decline at any point.
>
> [OWNER NAME], [DATE]

---

## Part A: author oracles for sealed cases (blind)

**You receive:** each case's application name, its pinned release (version, sdist sha256, tag commit), and the template `handoff/templates/oracle_spec.md`. Nothing else about the case.

**You must not read:**
- the application's fix diffs, commit messages about fixes, or issue discussions of defects;
- its tests;
- its changelog's "Fixes" sections;
- any DeploySpecter output.

**You may read:** its user documentation (API references, upgrade and migration guides) and its current release's public API.

**For each case:**
1. **Write each property** as a precise statement over the final database state, and over the application's public results: job, row and status semantics. Cite the documentation sentence that justifies it, and classify it:
   - **G**, a documented guarantee: cite it;
   - **H**, an assumption you state;
   - **X**, exploratory.
2. **Say what the property does not cover.** For example, a property that holds only after a quiescent completion.
3. **State its competing explanations:** for each property, what legitimate behaviour could look like a violation.
4. **Fill in "Sources read" honestly,** and sign the attestation in `oracle_spec.md`.
5. **Return `oracle_spec.md`** to the owner. The developers encode it into a checker. You then review that encoding (Part B's procedure) before the freeze. The spec's sha256 goes into the adoption commit.

**Done when:** the oracle holds on the corrected version under the full budget before any arm is scored. If it doesn't, that is an oracle error, and the case is dropped for every arm.

## Part B: review the developer-written oracles of three development cases (not blind; a process check)

These cases are NONBLIND by construction. Their histories are known, and their oracles were encoded by the tool's developer. Your review tests whether the encoding procedure works. It is not independent evidence about the cases.

| Case | Oracle documents | Checker | Questions for you |
|---|---|---|---|
| `historical/chancy_2895c4a` | `CASE.md` (H0–H2: a cancelled job must stay cancelled; derived from the maintainers' regression test) | `src/deployspecter/experimental/historical_oracles.py` | Does H1/H2 encode the maintainers' test faithfully? Is "cancel after the job finished but before its flush" within what the test exercises? |
| `rolling_upgrade/procrastinate_3.3_3.4` | `CASE.md` (R1–R7) and `ERRATA.md` (E1: the R2 equality was wrong) | `src/deployspecter/experimental/procrastinate_properties.py` | Are R1–R7 justified by procrastinate's documentation? Is the blue-green reading of the migration how-to right? Is E1's correction right? |
| `simultaneous/PROPERTIES.md` (Chancy 0.25.1 + 0.26.0) | P1–P8 | `src/deployspecter/experimental/chancy_properties.py` | Are the P7 classifications (G only for 0.26.0 cancel with a 0.26.0 worker) and the "outside the documented upgrade procedure" reading right? |

For each, return: agree, disagree, or needs change; the reason; and any property you would add or remove.

## Declaration (sign and return)

> I am not a developer of DeploySpecter or its Muse integration, and I am not the label curator for the cases in Part A. For Part A, I have not read any fix, test, changelog defect entry, defect report or DeploySpecter output for those cases. My "Sources read" lists are complete.
>
> Name: ____________ Signature: ____________ Date: ____________

## What makes this decision ACCEPTED, IMPLEMENTED and VERIFIED (`DECISIONS_003_v2.md` #7)

| Status | Evidence |
|---|---|
| ACCEPTED | your declaration, and the owner's written acceptance |
| IMPLEMENTED | a signed `oracle_spec.md` and its reviewed checker per case, both hashed into the freeze |
| VERIFIED | your review of the encoding, and the oracle holding on the corrected version under the full budget, before any arm is scored |
