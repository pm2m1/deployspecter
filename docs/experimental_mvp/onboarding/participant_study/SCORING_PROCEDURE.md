# Evaluator-blind scoring (onb-study/1)

1. **The experimenter builds the bundle:** `study.py bundle --sessions ... --out BUNDLE --key KEY.json`. Every
   attempt's `run.json` goes in under a random id, with the app, image, schema, pinned sources and stderr removed,
   and Muse traces never copied. `KEY.json` stays with the experimenter.
2. **An independent scorer** (not a participant, not the experimenter, no access to `KEY.json`) re-derives S2 and
   S3 for each bundle item with `onboarding_pilot.correctness.s2/s3`, and records verdicts per id.
3. **The experimenter joins** the scorer's verdicts to sessions through `KEY.json`. Any disagreement with the
   recorder's own S2/S3 events is reported. The scorer's verdict is primary.
4. **Limits:** the run evidence can still reveal the app (its SQL and table names). Blindness here covers the
   condition, and only partially the app.
