# Oracle reviewer brief

**Purpose:** application-level correctness properties for each case, independent of Muse and of the tool's developers.

1. **Write from the application's documentation** (docs, docstrings of public APIs, the data model): state properties over the committed database state and returned values (see `templates/oracle_spec.md`).
   - Never derive a property from Muse proposals or outputs.
   - Never derive one from the fix diff, when the case requires blindness to the defect.
2. **Check against the corrected version.** Each property must hold on the corrected version under the case's workloads and schedules. A property that fails there is an oracle error. Fix or drop it before the freeze.
3. **Document** the assumptions, the unsupported cases (paths the oracle can't judge) and any vacuous properties.
4. **Hash** each oracle (sha256 of its source), and attest in writing that it was written without the fix or Muse output. The hashes go into the freeze (draft v2 §14).
5. **Precedent:** the existing oracles (R1/R2/R5, L1/L2, Q1–Q3, C1–C3, K1–K2, M1–M4) were written by the tool's developers, so they don't satisfy this role. They may serve as examples of form only.
