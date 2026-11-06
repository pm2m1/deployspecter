# Participant instructions (onb-study/1)

You will bring two Python PostgreSQL job-queue libraries (apps A and B, pinned) under DeploySpecter's test harness,
one after the other. Your allocation sheet says the order, and which app is done with Muse assistance.

**Goal per app:** reach **S3** within the budget.
- **S1:** a spec and a workload that `pilot.py validate` accepts.
- **S2:** a run whose server-log corroboration passes with 0 mismatched parameters.
- **S3:** a corroborated run in which every enqueued job was handled exactly once.

You write one adapter module, following `onboarding_pilot/spec.CONTRACT`, plus `spec.json` and `workload.json`.

**Recording** (the recorder's clock is the measurement; please don't batch notes afterwards):
- `pilot.py start --operator <your study id> --operator-kind independent` when you begin an app;
- `pilot.py note ... intervention --detail "..."` for every manual action: reading source or docs, writing or
  editing a file, diagnosing a failure;
- `pilot.py validate` and `pilot.py run` for every check and run (runs record themselves);
- in condition M only, `pilot.py muse` for each Muse request (at most 3 per app);
- `pilot.py end` when you reach S3 or decide to stop. Stopping without S3 is a valid outcome.

**Rules:**
- Untrusted library code runs only through `pilot.py run`, inside the restricted container.
- Never point anything at a database other than the disposable one (127.0.0.1:55441).
- Don't use Muse in condition D, and don't share your work between apps or with other participants.
