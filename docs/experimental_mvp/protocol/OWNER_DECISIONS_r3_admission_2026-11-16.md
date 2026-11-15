# Owner decisions on r3's open admission items, and the accepted model string (RECORDED 2026-11-16)

**Decided by:** Parth Mishra, sole owner. **Date:** 2026-11-16. Recorded by the working session on the owner's
written authorization (verbatim at the end). This file is the record. The draft with the options and rationale,
`OWNER_DECISIONS_r3_admission.md`, stays unchanged.

**Candidate:** `AMENDMENT_003_ADOPTION_CANDIDATE_r3.md`, sha256
`af9b2205ab77c38cb5b7c803e85dbf7bb8bd283b68e15e26d6b177a9bb13fe1c`, unchanged.

| # | Item | Decision | Implemented by |
|---|---|---|---|
| 1 | An unparseable reply | **A failed request** (`unparseable_reply`): zero proposals generated, counted against R | `research_tools/confirmatory_prep/prep_r3.py` (unchanged) |
| 2 | "Canonical JSON" | **The implementation's definition, frozen by code hash:** `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, UTF-8 (`prep.canonical`), applied after full validation. Not RFC 8785. | `research_tools/confirmatory_prep/prep.py` (unchanged; hash in `osf/FREEZE.json`) |
| 3 | A missing request (a planned index with no record) | **Excludes that case for every arm**, as an infrastructure failure | **New admission path** `research_tools/solo/admission_solo.py` (`admit_case`, `admit_arm`); `prep_r3.py` unchanged; tests in `tests/experimental/test_solo_admission_offline.py` |
| 4 | Accepted provider-reported model string (r3 §12, #27) | **`muse-spark-1.3`** | `verified_request.py` compares against it; source: `results/external_public/gitlab_authorization_sync/20261102T130607Z-public-evidence-gitlab-ai-2aef4b9c09c435b7/derived/requests_buggy_only.jsonl` lines 2, 4, 6 (`raw_response.model`) |

Rationale, one line each (from the draft):
1. An unreadable reply is a provider failure in effect; either reading admits nothing and leaves the validity
   denominator unchanged.
2. Every frozen workload schema is integers, booleans and enums, where RFC 8785's differences can't arise.
3. A missing record means the request journal is incomplete; excluding the case for every arm is symmetric between
   arms.
4. It is the provider's own reported string on every saved raw response; a different string at run time makes that
   request a provider failure.

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to: back up unpushed work to a private repo, submit the GitLab report as a confidential issue, post the saq issue, merge PR #2, publish the draft release, record my owner decisions, and re-plan the benchmark for a single investigator. I have no collaborators, curator, oracle writer or faculty reader. Record this text verbatim in each record you write on my behalf.
