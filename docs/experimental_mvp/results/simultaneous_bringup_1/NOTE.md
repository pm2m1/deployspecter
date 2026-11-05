# simultaneous_bringup_1: bring-up of the simultaneous path (engineering record, NONBLIND)

These are three attempts at scenario `old_to_new`: Chancy 0.25.1 pushes jobs to `q_new` while a 0.25.1 worker and a 0.26.0 worker run at once. All three were made while bringing the path up. None is a pilot result. Every attempt is listed here.

1. **Attempt 1 (not kept: nothing ran).** The host crashed reading the initial state: `json_array_length(jsonb)`.
   - This was before the window's start marker, so no simultaneous statement ran.
   - It led to a recorded amendment of the frozen checker's read query (`61fb31a`). See PROPERTIES.md, "Amendments".
2. **Attempt 2, `prefer` policy (`attempt2_prefer_starved`).** The run was SOUND: isolated, coordinated to completion and corroborated (592 statements, 0 mismatched). But it was starved:
   - `prefer` with no order always chose process A, so the 0.26.0 worker got its first grant only at grant 622;
   - the stop came from the 45 s timeout;
   - the jobs stayed pending.

   Its P5/P6 entries are artefacts of the schedule, not of Chancy. This attempt led to two harness rules:
   - a run stopped by the timeout is INCOMPLETE and gets no property verdict;
   - the default policy is now `alternate`.
3. **Attempt 3, `alternate` (`attempt3_alternate`).** SOUND and complete. Both versions were granted from grant 1 on, and the window held 130 grants, 65 per version, with 129 version switches. All 321 statements were corroborated, with 0 parameters mismatched. 0.26.0 executed jobs 0, 1 and 2, which 0.25.1 had pushed. No property was violated.

The `report.json` files here were written before `summary()` gained the window fields. Recomputing them from `simultaneous.coordinator` gives the numbers above.
