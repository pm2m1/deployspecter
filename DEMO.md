# Three-minute demo

Prepare first: start PostgreSQL, run `uv run deployspecter serve`, and open http://127.0.0.1:8765. Record your own run; the values on screen come from it.

**0:00 The problem (20 s).** During a rollout, old code, new code and migrations share one schema. DeploySpecter runs them on real PostgreSQL and checks the result against independently written requirements.

**0:20 GitLab-derived case (55 s).** On the first tab, show the published merge request links and the two trigger versions. Click **Run the documented defect on both versions**. Point out the destination row that disappeared, the source row that remains, and the timeline. Explain why it needs no concurrency, using the measured numbers on screen. Click **Replay this failure on PostgreSQL**. Compare with GitLab's corrected logic, and say that this is a minimal fixture, not GitLab's production system.

**1:15 A concurrency failure (30 s).** On **Benchmark scenarios**, choose the stale backfill scenario with the hand-authored generator and click **Run both versions**. Show the interleaving, expected versus actual, and "no serial order failed". Note that the corrected card says this is not a proof of safety.

**1:45 Muse Spark's role (50 s).** On **Muse Spark pipeline**, walk through the stages: source, operation catalog, a concrete recorded test, validator, execution and oracle. Click **Execute these recorded tests on PostgreSQL**. Stress that these are recorded responses, that the model proposes tests, and that only the oracle produces findings.

**2:35 Honest results (25 s).** On **AI vs non-AI**, read the detection line: Muse Spark found no more defects than the non-LLM baselines. Mention its narrow measured gains and the recorded failures. Close on what the project contributes: reproducible failures, verified by real execution.
