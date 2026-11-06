"""EXPERIMENTAL: re-apply the twin's E1/E2 classification to a saved twin run's frozen-checker output, without
touching the run's own report. Writes RUN/RECLASSIFICATION.json (classifier sha256, code revision, before/after).

    python research_tools/twin_reclassify.py RUN_DIR
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from deployspecter.experimental import harness as hz
from deployspecter.experimental.twin import procrastinate_twin as tw

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    run = Path(sys.argv[1])
    out = run / "RECLASSIFICATION.json"
    if out.exists():
        raise SystemExit(f"refused: {out} exists")
    report = json.loads((run / "report.json").read_text())
    final = next(s for s in report["steps"] if s["step"] == "T7_final_verification")
    new = tw.classify(final["case_checker"])
    model = REPO / "src/deployspecter/experimental/twin/procrastinate_twin.py"
    out.write_text(json.dumps({
        "run": run.name, "report_sha256": hashlib.sha256((run / "report.json").read_bytes()).hexdigest(),
        "classifier_sha256": hashlib.sha256(model.read_bytes()).hexdigest(), "code_revision": hz.code_revision(),
        "recorded_in_run": final["classification"], "reclassified": new,
        "changed": new != final["classification"]}, indent=1))
    print(run.name, "changed" if new != final["classification"] else "unchanged",
          {k: len(v) for k, v in new.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
