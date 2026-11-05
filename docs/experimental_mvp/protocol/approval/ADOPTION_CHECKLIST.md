# Amendment 003: adoption checklist (exact steps; nothing here has been done)

**Candidate:** `AMENDMENT_003_ADOPTION_CANDIDATE.md`, sha256 `ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0`.

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
