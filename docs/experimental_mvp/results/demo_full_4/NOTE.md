# demo_full_4: reading note

This is the full one-command run (`research_tools/mvp_demo.sh docs/experimental_mvp/results/demo_full_4 full`) at revision `d42e369`, clean. It **exited 0**, with 278 core experimental tests passing. It is the release-candidate acceptance run.

Added since `demo_full_2`:
- **`chancy_mutant_no_skip_locked_claim`**: the declared claim-lock mutant, built in an isolated copy. Every statement was corroborated, and the oracle distinguishes the mutant: K1 (two jobs left pending) and K2 (jobs 0 and 1 each handled twice). The run itself is sound, which is what its exit status reports.
- **`chancy_mixed_version_pilot`**: Chancy 0.25.1 → 0.26.0 as a rolling deploy (NONBLIND). The server log corroborated all four phases, with 0 parameters mismatched and 0 unchecked:

  | Phase | Statements | Values compared |
  |---|---|---|
  | 1 | 45 | 64 |
  | 2 | 11 | 7 |
  | 3 | 65 | 80 |
  | 4 | 371 | 212 |

  The oracle found no violation. The 0.25.1 worker handled jobs [0, 1, 2]; the 0.26.0 worker handled [3–7], including the jobs 0.25.1 had pushed.

This is the first full run after the quiesce fix that `demo_full_3` (exit 15) exposed. Like everything in this directory, it is engineering evidence, not research evidence.
