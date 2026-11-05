"""EXPERIMENTAL supported deployment digital twin (host side only; never copied into the runner image).

A *supported* twin models one application's documented upgrade contract as declared phases, each with checked entry
and exit conditions, version and schema identity, permitted operations, state digests, session provenance, server-log
reconciliation and independent property checks. It is not a production deployment controller, and a phase is never
assumed safe because it executed.
"""
