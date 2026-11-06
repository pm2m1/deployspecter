# Harness calibration (not a pilot outcome)

Before the pilot manifest was frozen, the onb-pilot/1 harness was run on the **already integrated** Chancy 0.26.0,
through a calibration adapter (`onboarding_pilot/adapters/calib_chancy.py`). The aim was to show that the harness
builds, runs, corroborates and scores. Chancy is not a pilot app.

| Attempt | Exit | What happened |
|---|---|---|
| `run_1` | 1 | The operator's shell command split under zsh (`(eval): no such file or directory`). No session existed yet, so it isn't in the session log |
| `run_2` | 1 | Harness bug: `pilot_runner` passed only the runs to `merge_runs`, which needs the probes too (`KeyError: 'probes'`). Fixed before `run_3` |
| `run_3` | 0 | S1, S2 and S3 met: 73 statements and 97 parameters corroborated, 0 mismatched and 0 unchecked; handled exactly `[a, b]`; isolation ok; teardown complete |

The calibration adapter credits the recorder from `chancy_adapter.HANDLED` after the work step. A pilot adapter must
call `recorder.handled` inside its own task function.
