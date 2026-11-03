# Entrypoint registry (offline step 2, candidate)

`src/deployspecter/live/registry.py`. Research only, database-free and network-free. It never imports or runs an
application.

## Trust

A registry needs two things:

- **The configuration**, as JSON or a model.
- **A `TrustedRegistration`** that harness code builds itself: `registrar`, `config_label`, and `pinned_digest`, the
  SHA-256 the configuration must have.

The pinned digest must come from somewhere other than the configuration it checks, such as a reviewed, committed file,
so changing the configuration alone can't change what is trusted. The configuration's own `registered_by` must agree
with the trusted object, and the registry reports provenance from the trusted object, never from the JSON.

This is a code boundary, not cryptography: anything in the same process can construct a `TrustedRegistration`. Proposal
text can't, because nothing ever parses one from a proposal.

## What a registry is

```
RegistryConfig
  registry_version  the integer 1 (a boolean is refused, although True == 1 in Python)
  application       identifier
  registered_by     {registrar, source: "harness_config" (the only value), config_label}
  min_actors, max_actors   within the workload format's 2 to 4, min ≤ max
  entrypoints       1 to 64 of:
    id              registered identifier, unique
    operation       "dotted.module:function", unique across ids (else ambiguous), stored as text, never imported here
    versions        1 to 16 unique labels
    roles           request and/or background_job
    arguments       ≤ 16 unique contracts {name, types (subset of int, float, bool, str, null), required}
    max_instances   1 to 4
    description     one line
```

Unknown fields, repeated JSON keys and malformed values are refused. The digest is order-insensitive.

## API

| Call | Purpose |
|---|---|
| `TrustedRegistration(registrar, config_label, pinned_digest)` | Built by harness code |
| `build_registry(config, *, trusted) -> Registry` | Required trust; refuses a digest mismatch or disagreeing `registered_by` |
| `load_registry_json(text, *, trusted) -> Registry` | The same from JSON text |
| `registry_digest(config) -> str` | The order-insensitive SHA-256 a trusted pin is computed from |
| `Registry.lookup(id)` | Exact match, or `None` |
| `Registry.ids()`, `.digest`, `.application`, `.registered_by`, `.trusted` | Read-only facts |
| `authorize(spec, registry) -> AuthorizationResult` | Takes only a parsed `WorkloadSpec` and a built `Registry` |

`AuthorizationResult` is either accepted, with an `AuthorizedWorkload`, or rejected with every `Rejection` listed in a
fixed order. Each rejection has structured fields (`code`, `actor`, `entrypoint`, `argument`) and a human-readable
`detail` derived from them. The codes are:

- `application_mismatch`, `too_few_actors`, `too_many_actors`
- `unknown_entrypoint`, `version_not_registered`, `role_not_registered`
- `unknown_argument`, `missing_argument`, `argument_type_not_registered`
- `instance_limit_exceeded`

`AuthorizedWorkload` holds:

- the spec's full-record digest and the registry digest
- `registered_by`, from the registry
- actors sorted by name, each with its operation reference, version, role and typed arguments
- the state goals, unresolved
- the spec's provenance, labelled `self_reported_provenance`

Each actor carries `argument_values_validated: False`, and the workload carries `execution_ready: False`. Registry
authorization alone never yields anything runnable.

## What it doesn't do

- **Argument values aren't judged.** Ranges, patterns and choices belong to the operation-specific validator, a later
  step.
- **Operation references aren't resolved to code.** That happens later, in trusted harness code, never from proposal
  text.
- **A specification can't register anything.** It can only name registered ids.
- **Self-reported provenance never decides acceptance or registration provenance.**
