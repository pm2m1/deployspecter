# Oracle Writer quick start (one page, for labmate 2)

**What you are:** the Oracle Author for the DeploySpecter benchmark (r3 rulings #7 and #23;
`protocol/AMENDMENT_003_r3_OWNER_ADOPTION.md`). For each case you write an **oracle**: a set of properties that
decide, from the database's final state and the operations' results, whether the application behaved as its
documentation promises. You must stay **fix-blind**: you never learn how the defect was fixed, or what it was.

## Before you start

1. Read and sign `handoff/templates/participation_declaration.md`.
2. Use **your own machine and account**.
3. Check the protocol hash: `shasum -a 256 docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r3.md`
   must print `af9b2205…fe1c`.

## How you receive a case

The curator (labmate 1) sends you, and only you:
- the application's name and one commit with its source tree (you aren't told whether it is buggy);
- links to the application's public documentation for that version;
- a one- or two-sentence **contract-area statement**: which documented behaviour of which public API to write the
  oracle for.

## What you do

1. Read the documentation, the public API (signatures, docstrings, type hints) and the given source tree.
2. Write the oracle in `handoff/templates/oracle_spec.md`'s format. Each property:
   - is stated over committed database state or values the API returned;
   - names the tables and columns it reads;
   - cites the documentation sentence it comes from.
   If the documentation is silent, list it under "Unsupported cases"; don't guess what the authors meant.
3. **Declare the target clauses:** the properties that encode the named contract area. Other properties
   (general consistency) are non-target.
4. Sign the attestation: "written without the fix, its tests, its changelog entry, its issue or PR, and without
   any model output".
5. Hash the oracle's source (`shasum -a 256 <file>`), record the time, and send the hash, then the oracle, to the
   curator.
6. If the curator reports that the oracle rejects correct behaviour on a serial run, revise it from the
   documentation, hash it again, and record both versions.
7. **Later, triage:** classify each reported violation on the corrected revision, independently of the curator and
   blind to which arm produced it (`handoff/templates/triage_record.json`).

## Never look at

- The application's history after the commit you were given: later commits, tags, releases, branches or the
  default branch's head.
- Its changelog, release notes, issue tracker, pull requests, mailing lists, chat or forum threads, or security
  advisories.
- Its tests at any revision other than the one you were given, and any diff between revisions.
- Search results about the application's bugs, and any language-model output about the case.
- Anything the owner sends you about the case.

If you see any of these by accident, stop, write down what you saw and when, and tell the curator. The case may
need to be replaced. That is recorded, not hidden.

## Never send the owner

Your oracles, their target clauses, the contract-area statements, or the names of the applications you've been
given, until the owner tells you in writing that scoring is final.

## How long

About 1–2 hours per oracle, so **10–25 hours** for 10–12 cases, plus a few hours of triage, over 3–5 weeks.
