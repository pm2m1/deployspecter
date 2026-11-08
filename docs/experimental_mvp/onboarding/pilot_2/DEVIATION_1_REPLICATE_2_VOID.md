# Deviation 1: replicate 2 voided by an infrastructure interruption; replaced by replicate 2b

**What happened.**
- All four replicate-2 sessions (pgtq D/M, postgres_tq D/M) started at about 05:03:21–05:03:24Z.
- Within 10–40 s, every operator agent was terminated by the AI service's usage limit (HTTP 429). Each session had
  1–2 `intervention` events, and **no** attempt, proposal or Muse call.
- Service resumed about 4.5 hours later. The recorder clock kept running, so every session's 5400 s wall-clock
  budget was spent while no operator existed.

**Why it is void and not censored.**
- The interruption hit all four sessions, in both conditions, at the same moment, before any task work. It is
  unrelated to either condition.
- Scoring them as censored at the budget would count service downtime as onboarding effort.
- They are kept unchanged as `sessions/*_r2_VOID/` and excluded from the analysis. That exclusion is recorded here
  as a deviation from the manifest's "2 replicates" plan.

**Replacement.**
- Replicate 2b: four fresh sessions in fresh worktrees from the same frozen commit `f82e90d`, with the same
  instructions (replicate label `2b`) and the same budgets.
- Each M session's maximum is reserved again in the sprint ledger. The voided M sessions' reservations are settled
  at $0, since they made no Muse calls.

**Also recorded.** At 11:17 local time (05:47Z) the project folders were moved by the owner into
`~/Desktop/deploy spector/`. Afterwards:
- the worktree links were repaired with `git worktree repair` (metadata only);
- the virtual environments were re-synced;
- the sprint ledger is addressed explicitly through `DS_NBC_META_LEDGER`.

No evidence file was changed by the move.

**Replicate 2b instructions.** Two changes from replicate 1's filled-in instructions, applied identically for D
and M:
- the new worktree paths;
- one appended note: "the worktree path contains a space; quote every path; use `.venv/bin/python
  research_tools/onboarding/pilot.py`".

The task, budgets, resources and success criteria are unchanged.
