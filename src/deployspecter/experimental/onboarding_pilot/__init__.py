"""EXPERIMENTAL onboarding pilot (``onb-pilot/1``): the app-independent harness for measuring the work of bringing a
previously unintegrated PostgreSQL application under DeploySpecter.

* ``spec``: the app spec, the workload and the deterministic validator (success criterion S1).
* ``recorder``: the harness-owned handler recorder (it runs in the container).
* ``correctness``: the independent check (S3).
* ``session``: the hash-chained session log and its measures.
* ``muse_assist``: the bounded Muse assistant for condition M (JSON proposals only).
* ``analysis``: per-condition measures and the descriptive comparison.

**The adapter contract** (fixed before any onboarding; see ``spec.CONTRACT``): an adapter module, which the
operator writes for each app, exposes ``verify_pinned()`` and three coroutines, each given the harness's stepped
connection:
* ``install(conn)``;
* ``enqueue(conn, *, jobs)``;
* ``work(conn, *, expect, timeout_s)``.

Its trusted task function calls ``recorder.handled(key)``.
"""

VERSION = "onb-pilot/1"
