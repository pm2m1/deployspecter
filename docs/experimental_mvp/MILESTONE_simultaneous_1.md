# Experimental engineering milestone: simultaneous mixed-version execution (`simultaneous-1`)

**What this is.** A distinct engineering milestone record. It doesn't overwrite or amend the release candidate `rc1` (`RELEASE_CANDIDATE.md` and `.json`, code `d9890f8`). It is **engineering evidence only**: not research evidence, not confirmatory, not Gate A2 evidence.

| | |
|---|---|
| Evidence revision | `d3630b5` |
| Manifest | `MANIFEST_simultaneous_1.json` (sha256 `f81aedd9fe9df1d9…`, 80 files, written at `ab4af5e`); it verifies |
| Scope | Chancy 0.25.1 and 0.26.0 workers running at the same time: two restricted containers and a coordinator (`ds-coord/1`). The final sets were 36 of 36 SOUND, with 7428 statements corroborated and 0 mismatched |
| Records | `simultaneous/RESULTS.md`, `simultaneous/DESIGN.md`, `simultaneous/PROPERTIES.md` |
| Negative controls kept | adversarial unprotected-stop test (server log refuses); the claim-lock mutant (P2 in 2/10 vs 0/10); all 17 refused runs kept |
| Regression tests kept | `TestQuiesced`, `TestCommitCancelledAtTheGate`, `test_mvp_coordinated_live.py`, `test_mvp_coordinated_offline.py`, the optional simultaneous test |
| Reproduction | `research_tools/mvp_demo.sh OUT simultaneous` (re-run and passing: `results/demo_simultaneous_2`) |

The next sprint's code (the historical case) is pinned separately: `MANIFEST_historical_1.json`.
