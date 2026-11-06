"""EXPERIMENTAL: prove at run time which tests execute the imported Phase 9B constructor.

    python research_tools/trace_phase9b_use.py OUT_DIR [PYTEST_ARGS...]

Runs pytest in-process with a small plugin. For each test it records:
* every call (``sys.setprofile``) into a function whose code object comes from a file in
  ``state_construction/phase9b_source/``;
* the ``__file__`` and sha256 of the loaded ``deployspecter._phase9b.*`` modules.

``construct_state`` and ``read_context`` are counted by name. A test whose calls never reach those files did not use
the imported constructor, whatever its name says. The output is ``OUT_DIR/use.json``; pytest's exit status is this
script's exit status.
"""

from __future__ import annotations

import collections
import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SOURCE = (REPO / "src/deployspecter/experimental/state_construction/phase9b_source").resolve()


class Tracer:
    def __init__(self) -> None:
        self.per_test: dict[str, collections.Counter] = {}
        self.current: collections.Counter | None = None

    def profile(self, frame, event, arg):  # noqa: ANN001
        if event == "call" and self.current is not None:
            f = frame.f_code.co_filename
            if f.startswith(str(SOURCE)):
                self.current[f"{Path(f).name}:{frame.f_code.co_name}"] += 1

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):  # noqa: ANN001
        self.current = collections.Counter()
        sys.setprofile(self.profile)
        try:
            yield
        finally:
            sys.setprofile(None)
            self.per_test[item.nodeid] = self.current
            self.current = None


def main() -> int:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    tracer = Tracer()
    rc = pytest.main(["-p", "no:cacheprovider", "-q", *sys.argv[2:]], plugins=[tracer])
    loaded = {name: {"file": getattr(m, "__file__", None),
                     "sha256": hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() if getattr(m, "__file__", None)
                     else None}
              for name, m in sorted(sys.modules.items()) if name.startswith("deployspecter._phase9b.")}
    tests = {}
    for nodeid, c in tracer.per_test.items():
        tests[nodeid] = {"phase9b_calls": sum(c.values()),
                         "construct_state": c.get("state_constructor.py:construct_state", 0),
                         "read_context": c.get("seed_feasibility.py:read_context", 0),
                         "distinct_functions": len(c)}
    report = {"pytest_exit_status": int(rc), "source_dir": str(SOURCE.relative_to(REPO)), "loaded_modules": loaded,
              "tests": tests,
              "tests_reaching_construct_state": sorted(k for k, v in tests.items() if v["construct_state"]),
              "tests_not_reaching_phase9b": sorted(k for k, v in tests.items() if not v["phase9b_calls"])}
    (out / "use.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    print(json.dumps({k: report[k] for k in ("pytest_exit_status", "tests_reaching_construct_state",
                                             "tests_not_reaching_phase9b")}, indent=1))
    return int(rc)


if __name__ == "__main__":
    sys.exit(main())
