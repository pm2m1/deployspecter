"""EXPERIMENTAL: invariant hypotheses as data (``ds-invariant/1``), checked by a deterministic validator.

``language``: the restricted grammar, its admissibility check and its pure evaluator (no generated code is ever run).
``validator``: the eight checks (reference, initial states, serial, concurrent, mutants, vacuity, false positives,
kills) and the classification (invalid, valid, valid_vacuous, mutant_sensitive, duplicates_oracle,
new_property_candidate).
"""
