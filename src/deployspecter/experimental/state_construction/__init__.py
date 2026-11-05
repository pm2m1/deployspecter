"""EXPERIMENTAL state construction: WorkloadSpec v2 goals, Phase 9B's deterministic constructor (unmodified), typed
construction, exact independent verification, and runs that consume the constructed state.

* ``phase9b``: the Phase 9B constructor, loaded byte-identically and sha256-checked, on the disposable cluster only.
* ``typed``: typed values and the exact, own-session re-read that verifies a constructed state.
* ``pipeline``: the end-to-end path for the Gate 2A synthetic reservations application.
* ``chancy``: the narrow, explicitly scoped path for a real application (Chancy 0.26.0's job table).

Not Gate 2A's supervised runner, and not Gate A2 evidence.
"""
