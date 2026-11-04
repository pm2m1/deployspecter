# Track B: proposed strong arm E policy

- **Status:** **PROPOSED, NOT ADOPTED.** E isn't frozen.
- **Evidence:** synthetic offline checks only (`tests/research_tracks/test_track_b_milestone2.py::TestStrongPolicy`).

## The policy

| Element | Rule |
|---|---|
| Companion generation | **On.** When a trusted contract's `requires` predicate ties one argument to another, a B variation includes the companion at its canonical value. The unit is generated before validation and labelled `+companion:<arg>` |
| Partner pairing | **On, where `max_instances` permits.** An entrypoint limited to one instance forms B units with the first other entrypoint's canonical call, labelled `+partner:<entrypoint>`. Every unit stays within each entrypoint's `max_instances` (tested) |
| No silent repair or replacement | No rejected unit is edited or swapped. Every unit the **default** policy would have generated and rejected is listed with its stage and code (`default_policy_rejections`), beside the option-generated unit that stands in its place |
| Attribution | Every option-generated unit is labelled, gets its own unit id, and is listed (`option_generated_units`). The generator records each unit's source, never the document's provenance |
| A-derived coverage gaps | A tests that can't be written as one v2 document are listed by name and run in arm A only. Today that is 1 of 2 synthetic fixtures. **Real-application A coverage is unmeasured** |
| Ordering | Ascending `sha256("deployspecter-live-protocol-v1:E-order:<owner>/<repo>:<seed>:<unit_id>")`, seeds 1 to 3 |
| Equal opportunity | Equal B runs and T seconds per (defect, replicate), with round-robin within each cell. E's breadth is reported |
| E to R | R runs only from E's verified state digest |

## Synthetic coverage under the strong policy

| Version | Candidates | Valid | Rejected | Boundary values | Pairs | Default rejections disclosed |
|---|---|---|---|---|---|---|
| v1 | 25 | 25 | 0 | book 24 of 24 | 1 of 1 | 2 (coupon without price), each with a `+companion:price` stand-in |
| v2 | 34 | 34 | 0 | book 25 of 25, sweep 6 of 6 | 2 of 3 (`sweep+sweep` impossible under `max_instances = 1`) | 5 (`instance_limit_exceeded`), each with a `+partner:book` stand-in |

Duplicate units (2 and 3) are reported only, never removed.

## Choices that change the comparator, and need formal adoption in Amendment 003

1. **Companion generation:** it changes which B units exist.
2. **Partner pairing:** it makes some B units two-entrypoint units.
3. **E-1, round-robin allocation:** it affects S5.
4. **E-2, blocking inexpressible A tests in E:** E becomes narrower than "union of A and B" by a listed set.
5. **The goal-policy format.**

Choices 1 and 2 make E stronger, which raises the bar for P1 and changes P2's E side.
