"""EXPERIMENTAL, NONBLIND: the Muse-contribution comparison (arms B1-B5) and invariant proposals.

Arms (exploratory; deliberately *not* the frozen protocol's A-G/R arms):
  B1 curated workloads; B2 strong systematic deterministic generation; B3 Muse workloads;
  B4 identical workload + deterministic state proposal; B5 identical workload + Muse state proposal,
  both judged by the same unmodified WorkloadSpec v2 chain (``planning_adapter.plan``).

Every model output is JSON, validated by deterministic code; nothing a model returns is executed as code or SQL.
"""
