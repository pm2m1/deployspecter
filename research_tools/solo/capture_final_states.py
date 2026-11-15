"""Solo r3 design: run ``run_nbc.py run`` and save the ``(final_state, raw_results)`` each oracle call sees.

    python research_tools/pg_lock.py -- python research_tools/solo/capture_final_states.py \
        --capture DIR -- run --cases SPEC.json --arms RA-DET --out OUT

The integrations persist only their oracle's verdict, so a maintainer-test oracle can't be evaluated afterwards. This
wrapper changes no file: after ``run_nbc`` loads an integration, it wraps the ``load_oracle`` function of that module
and of modules it loaded (``saq_common``, the chancy base), so that every oracle call also writes
``DIR/<run dir>.json`` with the final state, the raw results and the case oracle's own verdict. The run, its ledger and
its verdicts are exactly what ``run_nbc`` produces.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import sys
import types
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
RUN_DIR = re.compile(r"[^/]+__[A-Z0-9-]+__r\d+__R[12]__s\d+")


def _run_dir() -> str:
    for fr in inspect.stack():
        for v in fr.frame.f_locals.values():
            if isinstance(v, (str, Path)):
                m = RUN_DIR.search(str(v))
                if m and "/runs/" in str(v):
                    return m.group(0)
    return "unknown"


def _wrap(module: types.ModuleType, capture: Path, counter: list[int]) -> None:
    orig = getattr(module, "load_oracle", None)
    if orig is None or getattr(orig, "_ds_capture", False):
        return

    def load_oracle(path: Path, sha256: str) -> Any:
        oracle = orig(path, sha256)

        class Capturing:
            def __getattr__(self, name: str) -> Any:
                return getattr(oracle, name)

            def check(self, final_state: dict[str, Any], raw_results: dict[str, Any]) -> str:
                verdict = oracle.check(final_state, raw_results)
                counter[0] += 1
                name = f"{counter[0]:03d}__{_run_dir()}"
                (capture / f"{name}.json").write_text(json.dumps(
                    {"run": name, "case_oracle_verdict": verdict, "final_state": final_state,
                     "raw_results": raw_results}, indent=1, sort_keys=True, default=str))
                return verdict

        return Capturing()

    load_oracle._ds_capture = True  # type: ignore[attr-defined]
    module.load_oracle = load_oracle


def main() -> int:
    argv = sys.argv[1:]
    if "--" not in argv:
        raise SystemExit("usage: capture_final_states.py --capture DIR -- <run_nbc arguments>")
    i = argv.index("--")
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, required=True)
    a = ap.parse_args(argv[:i])
    a.capture.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(REPO / "research_tools" / "nbc"))
    sys.argv = ["run_nbc.py", *argv[i + 1:]]
    import run_nbc

    counter = [0]
    original = run_nbc.load_integration

    def load_integration(path: Path) -> Any:
        m = original(path)
        _wrap(m, a.capture, counter)
        for v in list(vars(m).values()):
            if isinstance(v, types.ModuleType):
                _wrap(v, a.capture, counter)
        for name in ("saq_common",):
            if name in sys.modules:
                _wrap(sys.modules[name], a.capture, counter)
        return m

    run_nbc.load_integration = load_integration
    return run_nbc.main()


if __name__ == "__main__":
    sys.exit(main())
