"""EXPERIMENTAL: reviewed per-application integrations for the confirmatory arm executor (``real_executor``).

An integration is the only code that may turn a validated workload into an application run. Integrations are
keyed by the ``application`` field of a sealed ``case.json``; a case whose application has none is refused before
any run. Currently: ``chancy`` (single-version deployment contract, ``chancy_sv``)."""
