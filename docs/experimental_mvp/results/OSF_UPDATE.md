# OSF update text (draft for the owner to paste into https://osf.io/34ytz)

Label: "preregistered, automation-blinded, single-investigator. Not independent".

Authorization (verbatim):

> I, Parth Mishra (sole owner, GitHub pm2m1), authorize this session to run the evaluation preregistered at https://osf.io/34ytz (DOI 10.17605/OSF.IO/34YTZ), exactly as registered: case selection, oracle transcription, integrations, mutant generation, all arms, mutant decoding and the frozen analysis, on research/benchmark-prep and new branches off it. Muse API spend is capped at $100 in total. Record this text verbatim in each record you write on my behalf.

Not posted by this session. The owner pastes the text below into the registration.

---

**Update, 2026-11-17: study terminated at Stage 2 (no eligible historical cases)**

Case selection ran as registered, with the frozen code, the sealed seed and the 100-screen budget.
- It admitted 4 cases from 3 applications (pretix, plane, wger), below the registered target of 10 from at least 5.
- All 4 are Django applications. Registered ruling r3 #12 keeps the Django seam development-only until a Django
  application of comparable scale has run through it.
- I validated it on paperless-ngx v3.4.2 under criteria committed before any run. The validation failed:
  - migrations and simple requests ran faithfully;
  - 5 of 25 interleaved runs could not complete, because the application holds a file lock across database reads;
  - one workload diverged, because the tool refuses a connection the task queue reopens.
- The 4 cases were therefore refused (E3), and the registered re-selection admitted none.

**Outcome:**
- 0 eligible historical cases. The primary hypothesis (H1, RA-Q3) and every other registered comparison are not
  estimable.
- No arm was run, and no mutants were generated: the plan generates them per application of selected cases.
- Muse API spend was $0.
- This result supports no conclusion about language-model workloads, in either direction.
- All deviations were recorded before they took effect.

Full report, with the selection funnel, refusals, seam-validation evidence and deviations:
https://github.com/pm2m1/deployspecter/blob/10aa32d13913c6d61c9f4f257355508da5617377/docs/experimental_mvp/results/RESULTS.md

Evidence bundle: GitHub release `results-2026-11-17` (to be published by the owner).
