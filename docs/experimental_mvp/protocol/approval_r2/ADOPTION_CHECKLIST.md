> **DRAFT FORM FOR PROPOSED REVISION CANDIDATE r2. UNSIGNED.** It applies only to `AMENDMENT_003_ADOPTION_CANDIDATE_r2.md` (sha256 below), never to the original candidate `ad5ea331…` or to r1 `20403460…`. Nothing on it has been ticked or signed, and no reviewer has been named.

# Amendment 003: adoption checklist (exact steps; nothing here has been done)

**Candidate:** `AMENDMENT_003_ADOPTION_CANDIDATE_r2.md`, sha256 `476d5c57013b12abfbe78c70db67a88603e095382dd409068561418869374e46`.

| Step | Who | Evidence it was done | Status |
|---|---|---|---|
| 1. Choose and contact a label curator, an oracle author and reviewer, two triage reviewers and an adjudicator. None may be a DeploySpecter or Muse developer, and no person may hold two roles for the same case. Send `handoff/send/CURATOR_PACKAGE_v2.md` and `ORACLE_REVIEW_PACKAGE_v2.md` | owner | the sent packages, with dates | **not done** |
| 2. Each signs their form in `approval/`, after checking the sha256 | each person | the signed forms, archived beside the candidate | **not done** |
| 3. The owner signs `OWNER_APPROVAL.md` | owner | the signed form | **not done** |
| 4. If anything was amended: prepare a new candidate, record the new sha256, and repeat steps 2–3 | developers, then everyone | a new `CANDIDATE_SHA256` | n/a |
| 5. Commit the approved text **byte-identically** as `docs/research/live_stepping/EVALUATION_PROTOCOL_AMENDMENT_003.md` (§14 of the frozen protocol: an amendment takes effect when committed beside it). Record that commit's sha | owner | the commit; `shasum` equal to the candidate's | **not done** |
| 6. Register the commit's sha256 and the candidate's sha256 publicly with a trusted timestamp (OSF Registries, plus an RFC 3161 or OpenTimestamps token), before any evaluation run | owner | the registration identifier and the token, committed | **not done** |
| 7. The curator submits sealed cases through `research_tools/corpus_intake.py`, and the registry is frozen (`corpus_intake.py freeze`) | curator | the registry's chain head, committed | **not done** |
| 8. The oracle author's specs are encoded, reviewed and hashed into the freeze. Each holds on its corrected revision under the full budget | author, reviewer, developers | the spec hashes; the corrected-version runs | **not done** |
| 9. The curator's leak check over the frozen runner inputs: hits = 0 | curator | the transcript, dated before the first run | **not done** |
| 10. Only then may a confirmatory evaluation run start | | | **BLOCKED** |

**Added for r2 (nothing here has been done):**

| Step | Who | Evidence it was done | Status |
|---|---|---|---|
| r2-a. Choose a separate Integrator (#29), different from the curator, the oracle author and reviewer, the triage reviewers, the adjudicator and the developers. Send `handoff/send_r2/REVIEWER_PACKAGE_r2.md` | owner | the sent package, with its date | **not done** |
| r2-b. The Integrator signs `INTEGRATOR_ACCEPTANCE.md` before any case is frozen | Integrator | the signed form | **not done** |
| r2-c. Hash into the adoption commit: K = 6, S = 2 (#24); the time rule's L = 300 s and 7,500 s ceiling (§4); R, P, C (#15) | owner | the adoption commit | **not done** |
| r2-d. Record the exact accepted provider-reported model string (#27) | owner, Integrator | the adoption commit | **not done** |
| r2-e. Freeze every eligibility rule (#28) with its hash, or record that there are none | Integrator | the rule hashes in the freeze | **not done** |
| r2-f. Commit each case's exposure records (#29) and the admission journal before its first run | Integrator, curator | the committed records | **not done** |
| r2-g. For #30 option A: the ethics or owner review, a named data owner, the application selection rule, and the study manifest (design `min18` or `pref24`, seed, code hashes) frozen before recruitment | owner, data owner, coordinator | the frozen study manifest | **not done** |
