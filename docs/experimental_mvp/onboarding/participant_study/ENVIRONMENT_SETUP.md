# Environment setup (onb-study/1)

1. **A dedicated machine or OS account** per participant, not the experimenter's. It has Docker (the isolated
   runner) and a fresh checkout of the study commit, with the study apps' adapters absent.
2. `uv sync`, or the pinned `.venv` from the study manifest.
3. **The disposable PostgreSQL:** `research_tools/mvp_pg.sh start`, on 127.0.0.1:55441, cluster
   `ds_mvp_disposable`. Never the protected 55432 instance.
4. **The pinned sdists:** `python -I research_tools/onboarding/fetch_pilot_sdists.py DIR`, extended with the study
   apps' pins in the study manifest.
5. **Condition M only:** an env file with `MODEL_*` keys, provided by the experimenter for that session. It is
   read only by `pilot.py muse`, which caps calls and estimated dollars.
6. **Before the first session,** the experimenter runs the harness calibration (`../pilot_1/calibration`) on that
   machine.
