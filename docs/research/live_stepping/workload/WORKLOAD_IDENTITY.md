# Workload identity (offline step 4, candidate)

`src/deployspecter/live/workload_identity.py`. Pure and offline. WorkloadSpec v2 remains a candidate.

## Three identities

| | Identity | Covers | Status |
|---|---|---|---|
| A | Full record | Every field of the specification as written, including provenance and rationale (`workload_spec.digest`) | Implemented in step 1 |
| B | Validated plan | What a validated workload asks the harness to do, bound to trusted registry and contract digests | **Implemented here** |
| C | Executed scenario | What actually ran | **Specified below, not implemented** |

**Equal plan identities don't imply equal executed scenarios.** The code behind an operation reference, the seed state
actually constructed, the database configuration and the schedule can all differ between two runs of one plan.

## B. Validated-plan identity

`validated_plan_identity(validated, *, registry, contracts) -> IdentityResult` takes a `ValidatedWorkload`, plus the
trusted `Registry` and `Contracts`, both by keyword. It returns either a `PlanIdentity` or an explicit unsupported
reason.

**Nothing about the input is trusted as given.** A `ValidatedWorkload` is an ordinary immutable object, so code can
build or copy one by hand. The identity calls `argument_validation.check_validated`, which verifies the trusted
registry and contract objects and re-runs every earlier stage on the workload's own content:

1. **Stage 1:** it rebuilds a version 2 document from the authorized workload, and parses it with the real parser.
2. **Stage 2:** it authorizes that document against the trusted registry. The result must equal the given authorized
   workload exactly, apart from the full-record digest, which can't be re-derived without the original document.
3. **Stage 3:** it validates the arguments again. The result must equal the given validated workload exactly.

Argument re-validation alone wasn't enough. It never re-checks registry facts, so before this was fixed, a hand-built
workload with a changed operation reference or a dropped required argument still received an identity.

**Unsupported, rather than guessed, when:**

- the contract set isn't bound to this registry;
- the workload names a different registry or contract set from the trusted ones passed in;
- re-running any of stages 1 to 3 rejects its content, or produces anything other than the input exactly. That
  catches forged operation references, entrypoints, versions, roles, actor names, applications or registrations;
  missing, extra or retyped arguments; too many actors; and reordered actors;
- a value can't be encoded exactly: a non-finite float, negative zero, or a Python type that disagrees with its tag.

### Fields included

| Field | Why |
|---|---|
| Identity version | So a change of encoding is a new version, never a silent change |
| Registry digest, contract digest | The plan means what those trusted configurations say it means |
| Application | |
| Each actor's name | Actors are referred to and scheduled by name. Not shown removable, so kept |
| Entrypoint id and operation reference | |
| Application version and role | |
| Each typed argument: name, type tag, exact value | A missing argument and a null one stay distinct |
| Each state goal: entity, label, count, constraints with typed values | Labels are kept; removing them hasn't been shown to preserve meaning |

### Excluded

- **Self-reported provenance** (`origin`, `author`, `generator_version`, `note`): never authoritative, and it never
  identifies an evaluation arm.
- **Explanatory text:** `rationale`, the specification's `name`, and source references with their claims.
- **The full-record digest,** and any digest a proposal might carry. None is ever used as authoritative.
- **Registration provenance,** which the registry digest already binds.

### Canonical form and versioning

- **Order.** Actors are sorted by name, arguments by name, state goals by label, and constraints within a goal
  lexically. Each is canonicalized only where order carries no meaning:
    - **Actors** are scheduled by name, not by position.
    - **Arguments** are keyword arguments.
    - **Goals and constraints** are conjunctions.

  Genuine stage 2 output is already sorted, and the encoding sorts again rather than relying on that. A list that is
  semantically ordered would have to stay unsorted, but the current contract has none.
- **Every leaf is a string:**

  | Value | Encoding |
  |---|---|
  | `int` | `["int", "<decimal>"]` |
  | `float` | `["float", "<float.hex()>"]`, exact, the C99 hexadecimal form |
  | `bool` | `["bool", "true"\|"false"]` |
  | `str` | `["str", "<text>"]`, by code point, with no Unicode normalization |
  | `null` | `["null"]` |

  So `1`, `1.0` and `true` never collide, and integers beyond 2^53 stay exact.
- **Serialization.** Compact JSON with sorted keys, as UTF-8.
- **Hash.** `SHA-256(b"deployspecter:validated-plan-identity:v1\x00" + json)`, written `vplan1:<64 hex>`. The domain
  tag keeps it distinct from every other hash in the project. One identity is pinned in the tests.
- **Determinism.** Deterministic in the documented environment (CPython 3.12). Because no float or integer is
  serialized as a JSON number, the bytes don't depend on Python's number formatting.

### Deduplication

`deduplicate(workloads, *, registry, contracts) -> DedupResult`:

- **Groups** by plan identity, in order of first appearance, with members by input position. The first member is the
  representative.
- **Unsupported workloads are listed separately** and never merged with anything.
- **Inputs are never changed.**

### Fairness: a reporting view, never an accounting rule

Deduplication must not change how protocol v1.2 counts results. It can't, by construction:

- **It removes nothing.** Every input position appears exactly once, in a group or in the unsupported list.
- **It returns positions, never workloads.** So it can't substitute one proposal for another.
- **It has no concept of arms.** It holds no arm, generator, attempt, budget or replicate field. Self-reported origin
  and author never affect grouping, so it can't move a proposal between arms.
- **It changes no protocol quantity.** It reallocates no discovery budget, and changes no preregistered number of
  attempts or replicates.

**Arm-level accounting belongs to the future evaluation harness.** That harness will record which arm requested each
proposal, independently of anything the proposal says about itself. Under protocol v1.2 it must count every proposal
as generated, including duplicates. A group view may appear in reporting, for example as unique plans per arm, but
only as an additional, clearly labelled view.

## C. Executed-scenario identity (specification only)

A later step, needing separate approval. It must cover, at least:

1. **The validated-plan identity.**
2. **Resolved code:** for every operation reference, the fingerprint of the source actually imported at trusted
   dispatch, and the versions of the interpreter and every dependency in the run's environment.
3. **Constructed state:** a digest of the committed database state after seeding, not the logical goals, because
   construction may report a different achieved state or none.
4. **Database configuration:** server version and image digest, the relevant settings (isolation level, timeouts,
   logging), and the schema actually created.
5. **Schedule:** the decision sequence the harness admitted, plus the server-chosen outcomes protocol amendment 002
   records for comparability.
6. **Harness version:** the engine and supervisor commit, and the limits in force.

Two runs share an executed-scenario identity only if all of these match. That still wouldn't prove they behaved the
same, because PostgreSQL's own choices can differ.

## Collision and false-deduplication risks

- **Hash collision.** SHA-256 with domain separation, over an unambiguous all-string JSON encoding. Not a practical
  concern.
- **Same reference, different code.** A plan identity can't see whether an operation reference points at different
  code. That's the main reason the executed-scenario identity exists.
- **Same digests, different meaning.** Trust in the registry and contract digests is assumed. A wrong pin, or two
  configurations treated as the same, would merge plans that shouldn't be merged. The digests cover the full
  configuration, which limits this.
- **Deliberate false distinctions**, which are safe but cost deduplication:
    - renamed actors with symmetric roles
    - relabelled state goals
    - Unicode text that differs only in normalization
    - float values that are equal only after rounding a different decimal text, which in fact give the same double
      and the same identity
- **Upstream invariants aren't trusted.** The identity re-parses, re-authorizes and re-validates its input, and
  re-sorts it, itself.
- **The carried full-record digest isn't verified.** Without the original document it can't be. The plan identity
  never uses it.
