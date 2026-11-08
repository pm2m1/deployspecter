# saq in `ds-nbc/1` (NONBLIND development)

Screening: `SCREENING.md` / `SCREENING.json` (12 of 25 candidates screened; 3 eligible; 13 NOT SCREENED (capacity)).

| Case | Fix (R2) / parent (R1) | Classification | Attempt that decides it | First violation | Fixed control | Corroboration (statements / compared / mismatched / unchecked) |
|---|---|---|---|---|---|---|
| `saq-c570ba4` | `c570ba4` / `59f32fc` | **REPRODUCED** | `reproduction_1` | schedule 1 | 12/12 HOLD | 715 / 791 / 0 / 0 |
| `saq-addee31` | `addee31` / `f827b52` | **REPRODUCED** (post-hoc v2 encoding, disclosed) | `reproduction_3` (1: REFUSED, stale image; 2: R1 REFUSED, parse-time error) | schedule 1 | 12/12 HOLD | 480 / 376 / 0 / 0 |
| `saq-ad8d1c8` | `ad8d1c8` / `525acc9` | **CONTROL FAILED** | `reproduction_1` | schedule 1 (R1 W1 stranding) | 9 schedules, 1 VIOLATED (B3, W2) | 859 / 1139 / 0 / 0 |

Each case directory holds `CASE.md` (frozen before its first live run), `case_spec.json`, `oracle.py`, `ERRATA.md`,
`RESULTS.md` and its attempt directories. The archives are outside the repository; `research_tools/nbc/pin_saq.py`
reproduces them from a public mirror of https://github.com/tobymao/saq. Integration code:
`research_tools/nbc/integrations/saq_*.py`; container adapter: `src/deployspecter/experimental/thirdparty/
saq_coord_adapter.py`; runner mode `saq_coordinated` in `research_tools/isolated/smoke_inside.py`. Bring-up
(v0.26.5 only, not a case tree): `bringup/`.

A REPRODUCED case reproduces a documented upstream defect. None of this is blind, confirmatory or independent evidence.
