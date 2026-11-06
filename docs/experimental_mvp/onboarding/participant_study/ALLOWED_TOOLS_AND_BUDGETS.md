# Allowed tools and budgets (equal across conditions)

**Both conditions:**
- the pinned sdists' source and PKG-INFO, read as data;
- PyPI JSON metadata (wheel resolution via `pilot.py wheels`);
- this repository's code and docs, including the existing adapters as examples;
- a text editor;
- the participant's own general knowledge.

**Condition M only:** `pilot.py muse` (Muse JSON proposals; never executed).

**Not allowed:** other AI assistants or code generators; the internet beyond PyPI metadata and the libraries' public
documentation; help from other people.

| Budget (per app) | Value |
|---|---|
| Wall clock | 5400 s, from `start` |
| Container runs (attempts) | 10 |
| Muse (M only) | 3 calls, $2.00 estimated, hard-capped by the client |
| Work-step timeout per run | 60 s |
