# Three-minute demo

Prepare first: start PostgreSQL, run `uv run deployspecter serve`, and open http://127.0.0.1:8765/app. Switch the walkthrough to **Live local PostgreSQL**. Record your own run; the values on screen come from it. No model key is needed, and the demo makes no model requests. Keep `.env` and terminals with credentials off screen. The homepage is at http://127.0.0.1:8765/, and http://127.0.0.1:8765/demo.html is the recorded version, which needs no database.

**0:00 The problem (20 s).** On the homepage, show the headline and the recorded timeline. During a rollout, old code, new code and migrations share one schema. DeploySpecter runs them on real PostgreSQL and checks the result against independently written requirements.

**0:20 The stale backfill (70 s).** Open the workbench. In **Stale-backfill demo**, show the three code panes, the extracted operations and Muse Spark's recorded test, then its validator result. On **The interleaving**, run the failing schedule and use **Play step by step**, pointing at the step where the committed value is replaced. Run a serial schedule to show that it stays correct. On **Verified violation**, show expected versus actual, then click **Explore every interleaving on PostgreSQL** to show the count of failing interleavings. Replay the failure, then compare the corrected version, and say this covers these inputs only.

**1:30 GitLab-derived case (40 s).** On **GitLab-derived case**, show the published merge request links and the two trigger versions. Click **Run the documented defect on both versions**. Point out the destination row that disappeared and the source row that remains. It fails in one transaction, so this is a migration semantics defect rather than a race. Say that this is a minimal fixture, not GitLab's production system.

**2:10 Muse Spark's role (25 s).** On **Muse Spark pipeline**, show the stages for a recorded response: catalog, generated test, validator, then execution and the oracle. Stress that these are recorded responses, that the model proposes tests, and that only the oracle produces findings.

**2:35 Honest results (25 s).** On **AI vs non-AI**, read the "No measured detection advantage for Muse Spark" line: it found no more defects than the non-LLM baselines. Mention its narrow measured gains and the recorded failures. Close on what the project contributes: reproducible failures, verified by real execution.
