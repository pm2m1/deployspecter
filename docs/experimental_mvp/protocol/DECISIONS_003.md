# Amendment 003: the five blocking decisions (proposal record, 2026-11-05)

**Statuses, in order:**
- PROPOSED: drafted, not accepted.
- ACCEPTED: the responsible human reviewer approved it explicitly, in writing.
- IMPLEMENTED: the software implements the accepted decision.
- VERIFIED: evidence shows the implemented process worked.

**Every decision below is PROPOSED.** No reviewer has been named or has approved anything. No collaborator, signature or approval is assumed. Amendment 003 is not adopted, and confirmatory research stays blocked.

| # | Proposed ruling | Status | What exists to support it (not an implementation of an accepted decision) |
|---|---|---|---|
| 6 | An independent curator, not a tool developer, selects eligible historical migration defects. They record exact buggy and fixed commits, and seal the ground-truth labels before any evaluation arm runs. Fixes and labels never enter the Muse pipeline or the deterministic generators | PROPOSED | `research_tools/seal_labels.py`: a salted commitment, a reveal file refused inside the repository, and `leak-check` over the runner's inputs. Tested offline with synthetic labels only. `handoff/CURATOR.md` |
| 7 | A separate oracle reviewer authors, or validates, each case's oracle from the application's documentation and the corrected version. They see no Muse proposal and no Muse output. Each oracle is hashed into the freeze | PROPOSED | `handoff/ORACLE_REVIEWER.md` and `templates/oracle_spec.md`. The existing oracles were written by the tool's developers and don't satisfy this |
| 8 | Two designated reviewers each triage every corrected-version violation (false flag, oracle error, or new finding) without seeing the other's verdict. Disagreement goes to a predeclared adjudicator, whose decision is final and recorded with both verdicts | PROPOSED | `handoff/ACCESS_SEPARATION.md` §4 (procedure) and `templates/triage_record.json` |
| 9 | The adoption commit's sha256, with the full amendment text, budgets, prompts and oracle hashes, is registered through a trusted timestamp, preferably OSF Registries, before the first evaluation run. That date is reported against the first run's date | PROPOSED | Nothing registered. Registration is public, so it needs the owner's explicit approval, and none has been sought or assumed |
| 14 | The real-application evaluation runs on the experimental platform and makes **no claim** to satisfy the frozen Gate 2A/A2 verification boundary. Every report says so, and the Gate A2 demonstration stays blocked and separate | PROPOSED | The README and every evidence bundle already label results EXPERIMENTAL and not Gate A2 evidence |

To move one decision to ACCEPTED, the responsible person writes and signs their approval. The tool's developers can't do that on their behalf.
