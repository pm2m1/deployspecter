# Owner decisions on r3's open admission items, and the accepted model string (DRAFT; NOT INITIALLED)

**Candidate:** `AMENDMENT_003_ADOPTION_CANDIDATE_r3.md`, sha256
`af9b2205ab77c38cb5b7c803e85dbf7bb8bd283b68e15e26d6b177a9bb13fe1c`, **unchanged by this file**.
**Items from:** `r3/ADMISSION_REVIEW_r3.md`, "Items that need reviewer confirmation". r3 has no Oracle Reviewer or
independent reviewer under this staffing (adoption record, G2–G5), so the owner decides them, and says so here.
None changes K × S budgets, scoring or which workloads run.

Each item gives a recommended resolution and a one-line rationale. Initial the line to accept the recommendation,
or write your own resolution beside it. Initialled decisions are committed **before** the freeze and their file hash
goes into `osf/FREEZE.json` (`pending_owner_initials`).

Code at the time of writing (sha256):
`research_tools/confirmatory_prep/prep.py` `2599f8d79bcd0dbd79d898786632f86d8eed21e6dc579ddc499dbeb3f3827fba`,
`research_tools/confirmatory_prep/prep_r3.py` `4457f97454f3c5d636bd1dbb2060376ea04df1b592800f5fcfbdd5398bc2d9c0`,
`research_tools/confirmatory_prep/verified_request.py` `a493da071cca3eee25ec33f14bc1a2beb230b9ba7839f38aa2bfb5788f097991`.

## 1. An unparseable reply

The provider answers, but the reply isn't JSON or has no proposal list.

- **Recommended:** a **failed request** (`unparseable_reply`), with zero proposals generated, counting against R.
  This is what `prep_r3.py` already does.
- **Rationale:** a reply that can't be read is a provider failure in effect (r3 §4 step 1, "C2"), and either reading
  admits nothing and leaves the validity denominator unchanged.

Owner's initials: ________   Date: __________   (or own resolution: ______________________________)

## 2. What "canonical JSON" means

- **Recommended:** the implementation's definition, frozen by the code's hash:
  `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()` (UTF-8), from
  `prep.canonical`, applied only to proposals that passed full validation. Not RFC 8785.
- **Rationale:** every frozen workload schema is integers, booleans and enums, where RFC 8785's differences (`1` vs
  `1.0`, Unicode normalization, `NaN`) can't arise, and changing the definition now would change the code's hash.

Owner's initials: ________   Date: __________   (or own resolution: ______________________________)

## 3. A missing request

A planned request index has no record at all.

- **Recommended:** an **infrastructure failure that excludes the case for every arm**, like the safety ceiling and
  r3 §14's missing required records.
- **Rationale:** a missing record means the request journal is incomplete, so neither "failed" nor "sent" can be
  verified, and excluding the case for every arm is symmetric between arms.
- **Implementation note:** `prep_r3.py` currently counts a missing index as a failed request and lets the arm
  proceed. If you accept this recommendation, the runner must refuse that case for every arm instead. That change
  is made, tested and hashed **before** the freeze, and the new hash replaces the one above in `osf/FREEZE.json`.
  If you prefer no code change, initial the alternative: "a failed request, as implemented".

Owner's initials (recommended, infrastructure failure): ________   Date: __________

Owner's initials (alternative, failed request as implemented): ________   Date: __________

## 4. The accepted provider-reported model string (r3 §12, ruling #27)

- **Accepted model string:** `muse-spark-1.3`
- **Source:** the provider's own `model` field in the saved raw Responses objects in
  `results/external_public/gitlab_authorization_sync/20261102T130607Z-public-evidence-gitlab-ai-2aef4b9c09c435b7/derived/requests_buggy_only.jsonl`
  (sha256 `38f7a85829d1100551ff7e23b38347d098a24b2254397ea64e137796894b3af4`, committed in `5544d52`, present at
  tag `research-2026-11-15`). Lines 2, 4 and 6 hold `raw_response.model = "muse-spark-1.3"` for responses
  `resp_6ae88b1939668d6480744d14`, `resp_6ae88bb6c08c3102ea124c94` and `resp_6ae88c525b2faf22101d4a8e`, all
  `status: completed`.
- **Also consistent:** the requested model recorded in every later Muse call log (for example
  `nonblind_corpus/nbc2/proposals/muse/*/calls.jsonl`, `"model": "muse-spark-1.3"`). Those logs record the
  *requested* model (`client.model`), not the provider's, so they aren't the source.
- **Caveat:** those raw responses are from 2 November 2026. If the provider reports a different string at the first
  confirmatory request, `verified_request.py` treats that request as a provider failure (#24a). The string is
  re-checked with one non-case request before the freeze only if you decide so, at a cost of one request.

Owner's initials: ________   Date: __________
