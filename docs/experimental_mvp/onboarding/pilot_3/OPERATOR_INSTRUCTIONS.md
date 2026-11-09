# onb-pilot/3 operator instructions (frozen; identical for every session except the bracketed fields)

**Label: AI-OPERATOR, NONBLIND EXPLORATORY.** You are a fresh AI agent context acting as the operator of one
onboarding session. You are not an independent human participant, and nothing you produce will be reported as
one.

- **Your session:** application `{APP}` {VERSION}, condition **{CONDITION}**, replicate {REPLICATE}.
- **Your worktree:** `{WT}`. Work only there. The path contains a space, so quote every path.
- **Your session directory:** `{SESSION}`.

## The task
Bring the PostgreSQL job-queue library `{APP}` to **S3** under the `onb-pilot` harness. The milestones:
- **S1:** a spec and a workload accepted by `pilot.py validate`;
- **S2:** a run whose server-log corroboration is ok, with 0 parameters mismatched, isolation ok and teardown
  complete;
- **S3:** an S2 run in which every enqueued key was handled exactly once and every step finished without an
  exception.

To get there, write an adapter module, `src/deployspecter/experimental/onboarding_pilot/adapters/{APP}.py`,
implementing the contract in `src/deployspecter/experimental/onboarding_pilot/spec.py` (`CONTRACT`), plus a spec
and a workload JSON file in your session directory. The workload is at least 2 jobs of one declared task with an
int argument.

## Workflow (both conditions follow the same steps)

1. **Start:** `.venv/bin/python research_tools/onboarding/pilot.py start --session "{SESSION}" --manifest "{MANIFEST}" --app {APP} --operator {OPERATOR} --operator-kind developer-ai`
2. **Read the library's public documentation:**
   `.venv/bin/python research_tools/onboarding/pilot.py pkginfo --manifest "{MANIFEST}" --app {APP} --sdists "{SDISTS}"`
3. **CHECKPOINT A (mandatory; do it before you read any library source or write any file):**
{CHECKPOINT}
4. **Then work toward S3** with the commands below, reading the pinned source as you need.

Commands:

    .venv/bin/python research_tools/onboarding/pilot.py wheels --session "{SESSION}" NAME==VERSION ...
    .venv/bin/python research_tools/onboarding/pilot.py validate --session "{SESSION}" --manifest "{MANIFEST}" --spec F --workload F [--source operator|muse]
    .venv/bin/python research_tools/pg_lock.py --log "{SESSION}/pg_lock.log" -- .venv/bin/python research_tools/onboarding/pilot.py run --session "{SESSION}" --manifest "{MANIFEST}" --spec F --workload F --sdists "{SDISTS}" --out "{SESSION}/attempt_NN"
    .venv/bin/python research_tools/onboarding/pilot.py note --session "{SESSION}" intervention --detail "what you did by hand"
    .venv/bin/python research_tools/onboarding/pilot.py note --session "{SESSION}" unsupported --detail "..."
    .venv/bin/python research_tools/onboarding/pilot.py end --session "{SESSION}"
    .venv/bin/python research_tools/onboarding/pilot.py measure --session "{SESSION}"

{MUSE_LATER}

- **Record your manual actions.** Record each one with `note ... intervention` as it happens: writing or revising
  the adapter, spec or workload, reading the library source, or diagnosing a failure.
- **Use the lock for every run.** Always run `pilot.py run` through `research_tools/pg_lock.py --log
  "{SESSION}/pg_lock.log" --` exactly as shown. Waiting for the lock is logged and excluded from your times.
- **Number your attempts.** `attempt_01`, `attempt_02`, and so on; never reuse a directory.
- **End properly.** End the session when you reach S3, when a budget is spent, or when you decide to stop. Then run
  `measure` once.

## Budgets (the harness enforces them)
5400 s of wall clock from `start`; at most 10 runs; {MUSE_BUDGET}.

## Allowed resources
- the pinned sdist in `{SDISTS}` (its source and PKG-INFO, read as data);
- PyPI JSON metadata, for wheel resolution only, through `pilot.py wheels`;
- this worktree's harness, code and docs, including existing adapters as examples;
- your general knowledge.

## Not allowed
- **Other sources:** web search or web fetch; the library's repository, issues or docs website; any AI system other
  than {AI_RULE}.
- **Outside your worktree:** reading any file outside your worktree, except the pinned sdists in `{SDISTS}`. In
  particular, no other worktree and no other session's directory.
- **Running library code outside the harness:** library code runs only through `pilot.py run`, inside its
  restricted container. Never install or import it on the host.
- **Changing files:** any file outside the adapter path and your session directory.
- **The database:** using any database other than the harness's disposable one. Never touch port 55432. Never
  start or stop a database.
- **Shared state:** pushing, merging, or changing git history; committing is done later by the coordinator.

## Final message
In 12 lines or fewer, report:
- the milestones you reached;
- the `measure` output;
- the attempts and their outcomes;
- your CHECKPOINT A record (for M, whether the Muse response was accepted, modified or rejected, and why);
- any `unsupported` notes;
- anything that blocked you.
