# Argument validation (offline step 3, candidate)

`src/deployspecter/live/argument_validation.py`. Pure and deterministic: no database, network, oracle, import of
application code, or execution.

## Five stages, each with its own output

| Stage | Module | Output | Says nothing about |
|---|---|---|---|
| 1. Structural validity | `workload_spec` | `WorkloadSpec` | whether anything is registered |
| 2. Registry authorization | `registry` | `AuthorizedWorkload` (`execution_ready: False`) | argument values |
| 3. Argument-value validation | `argument_validation` | `ValidatedWorkload` (`argument_values_validated: True`) | whether it may run |
| 4. Execution authorization | not implemented | — | correctness |
| 5. Independent correctness evaluation | oracles, separately | — | stages 1 to 4, which it never sees |

**No stage trusts an object for its class.** Each re-derives its input from content: `authorize` re-parses the
specification, `validate_arguments` checks the workload is genuine `authorize` output, and the plan identity checks the
validated workload is genuine `validate_arguments` output. A forged object can't advance to a later status. These are
checks against mistakes and forged data objects, not against hostile code in the same process.

`ValidatedWorkload` embeds the unchanged `AuthorizedWorkload` and the contract set's digest. It carries
`execution_authorized: False` and `correctness_evaluated: False`, and neither can be constructed as `True`.

Self-reported provenance never identifies an evaluation arm. Arm membership is recorded by the harness that requested
a specification.

## Contract sets

Trusted configuration, separate from the registry, so the registry commit stays as it was:

- **Trust.** Built with a `TrustedContracts(reviewer, pinned_digest)` that harness code constructs. The digest must
  come from a source other than the contract set.
- **Binding.** Tied to one registry by `registry_digest`, and to one application.
- **Coverage, failing closed:**
    - every registered (entrypoint, version) pair has exactly one contract;
    - every registered argument has a rule, and every non-null registered type has a constraint;
    - nothing unregistered is constrained;
    - roles are a subset of the registered roles.

## The closed constraint vocabulary

| Type | Constraint fields |
|---|---|
| `int` | `minimum`, `maximum` (inclusive, 64-bit), `allowed` (≤ 32 distinct integers) |
| `float` | `minimum`, `maximum` (inclusive, finite). No allowed-value list, because float equality is fragile |
| `str` | `min_length`, `max_length` (≤ 128), `charset`, `allowed` (≤ 32 distinct strings, each satisfying the constraint's own length and charset and the text policy) |
| `bool` | `allowed` (one or both values) |
| `null` | permitted only where the registry lists `null` for the argument |

**Character sets, chosen by name only:**
- `digits`, `alnum`, `alnum_underscore_dash`, `printable_ascii` (U+0020 to U+007E)
- `lower_identifier` (lower-case letters, digits and `_`, starting with a letter)

No configuration-supplied regular expression exists.

**Cross-field predicates, within one actor, exactly four:**
- `less_than(left, right)` and `less_or_equal(left, right)`: both arguments must have the same single numeric type,
  `int` with `int` or `float` with `float`. Applied only when both have non-null values that passed their own rules.
  Mixed comparisons are refused when the contract set is built: Python compares `int` with `float` exactly, so
  `2**53 + 1 > 2.0**53`, but anything that converts the integer first finds them equal. Integer comparisons are
  exact at any size.
- `requires(if_present, then_present)`: if the first has a non-null value, the second must too.
- `excludes(left, right)`: not both non-null.

**Presence isn't truthiness.** An argument is present when it's given with a non-null value that passed its own rules.
`False`, `0` and `""` are present. A missing argument and a null one are both absent.

Any other operator, a predicate naming an unknown argument, or a comparison of an argument with itself is refused when
the contract set is built. There is no expression evaluator.

## Validation

`validate_arguments(workload, *, registry, contracts) -> ValidationResult` takes an `AuthorizedWorkload` plus the
trusted `Registry` and `Contracts`, by keyword. **This changed for the freeze blockers:** the registry is now required,
because without it stage 3 couldn't tell genuine authorization from a forged object. Before any value is looked at:

1. `verify_contracts` rebuilds the contract set (and through it the registry) from its own configuration and trust
   object, and requires an exact match. A difference, or contracts bound to another registry, raises `ContractError`.
2. A workload from another registry is rejected as `registry_mismatch`.
3. `check_authorized` must find the workload genuine. Otherwise every reason is reported as `not_authorized`.

`check_validated(validated, *, registry, contracts) -> str | None` is the same check for a `ValidatedWorkload`. It
re-runs every earlier stage on its content and requires an exact match, and returns a reason or `None`.

- **No coercion.** A value whose Python type disagrees with its tag is refused: a `bool` tagged `int`, `2.0` tagged
  `int`, `"2"` tagged `int`. So are non-finite floats. Such values can only arrive in a forged object, which now stops
  at step 3 above as `not_authorized`. The value rules stay as defence in depth and are tested directly.
- **No repair.** Nothing is defaulted, clipped, trimmed or reordered. The authorized workload is left unchanged.
- **Every problem is listed.** Actors are taken in name order, arguments in name order, then predicates in contract
  order.
- **Structured rejections.** Each has `code`, `actor`, `entrypoint`, `argument`, `predicate`, and a human-readable
  `detail`.
- **The codes:**
    - `registry_mismatch`, `not_authorized`, `no_contract`, `role_not_covered`, `type_not_covered`,
      `null_not_allowed`
    - `wrong_python_type`, `non_finite`
    - `below_minimum`, `above_maximum`, `too_short`, `too_long`, `character_not_permitted`, `not_in_allowed_values`
    - `cross_field_violated`

## Compatibility

- **No engine imports on the contract path.** The v2 contract path (`workload_spec`, `registry`,
  `argument_validation`) no longer imports the live engine or psycopg. The version 1 module, which does, is loaded
  only when a version 1 document is upgraded. Version 1 behaviour is unchanged.
- **No source imports.** Registration and validation never import application source. Operation references stay
  text.

## Deliberate-breakage survivors, assessed independently and not counted as caught

Two breaks across steps 2 and 3 left every test passing. Each was examined to see whether any input could tell the
broken code from the real one.

1. **Registry: provenance read from the JSON instead of the trusted object.** `build_registry` refuses any
   configuration whose `registered_by.registrar` and `config_label` differ from the `TrustedRegistration`, and
   `source` can only be `"harness_config"`. So every registry that exists has a JSON `registered_by` equal, field
   for field, to the one built from the trusted object. No input distinguishes them, so the break is equivalent. The
   trusted-object read stays as defence in depth, in case the agreement check is ever weakened.
2. **Contracts: the explicit check that `contracts_version` is an integer.** The field is declared `StrictInt`, which
   refuses booleans and other types before any validator runs. So the explicit check never sees a value it could
   refuse. That's equivalent, and redundant. Unlike the registry's `Literal[1]`, which accepted `True`, no boolean can
   get through here.

## Future: source fingerprint at trusted dispatch (not implemented)

Before stage 4 resolves an operation reference to code, trusted dispatch should:

1. Locate the module's source file without importing it.
2. Compare its SHA-256 with a fingerprint pinned alongside the registry entry by the registrar.
3. Import and dispatch only on a match, and record the fingerprint in the run's evidence.

A moved, renamed or edited function then fails closed before any application code runs. This step needs separate
approval and isn't part of steps 1 to 3.
