"""EXPERIMENTAL ds-p9x/1: a versioned extension of the copied Phase 9B constructor, for real third-party schemas.

The original Phase 9B files (``state_construction/phase9b_source``, sha256-pinned) are never edited. ``loader``
executes them under a separate private package with **one declared substitution** in row attribution: jsonb values
compare under contract ``jsonb_v1`` (see ``loader.SUBSTITUTIONS``). The other modules are ``catalog`` (export,
fingerprint, trigger screen), ``execute`` (target writes and readback), ``trace`` (execution proof) and
``chancy_registry_v1`` (the reviewed Chancy 0.26.0 contract).
"""

VERSION = "ds-p9x/1"
