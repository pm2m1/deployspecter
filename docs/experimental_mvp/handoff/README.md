# Independent-evaluation handoff package (EXPERIMENTAL; prepared 2026-11-05)

This package prepares three roles for a prospective, blind real-application evaluation under draft Amendment 003, version 2.
- **No one has been assigned to any role.** Until people independent of the tool's developers take them, nothing produced in this repository counts as independent validation.
- **A one-person split proves nothing.** Splitting the roles inside one developer's already-exposed work isn't independence.

| Role | Brief | Sees | Never sees |
|---|---|---|---|
| Curator | `CURATOR.md` | the repositories' histories, fixes, defect reports | Muse outputs before sealing; the runner's results before scoring |
| Oracle reviewer | `ORACLE_REVIEWER.md` | the application's documentation, the corrected version | Muse proposals or outputs; the fix diff, if blindness to the defect is required |
| Evaluation runner | `EVALUATION_RUNNER.md` | permitted application context only: pinned sources, registry, schema, public seal | labels, fix commits, fix paths, oracle sources before the freeze |

The separation procedure is in `ACCESS_SEPARATION.md`, and the templates are in `templates/`. The tooling is `research_tools/seal_labels.py` (sealing and leak checks) and `research_tools/corpus_select.py` (case eligibility under the frozen rules).
