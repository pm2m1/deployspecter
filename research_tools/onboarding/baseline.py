"""EXPERIMENTAL: the retrospective, developer-authored onboarding baseline, from this repository's git history.

    python research_tools/onboarding/baseline.py --out docs/experimental_mvp/onboarding/developer_baseline.json

For each third-party application already integrated (Chancy, PGQueuer, procrastinate) it counts:
* the integration files' current line counts;
* the lines added over history, from ``git log --numstat``;
* the commits touching them;
* the commits whose subject records a correction (fix, refuse, erratum, restore, stall, regression);
* the time from the first to the last commit.

Machine-generated files are excluded: the ``uv pip compile`` requirements lock files. So are shared harness
modules (the isolated runner, the coordinator), whose app-specific hooks are interleaved with shared code: their
share of each app's onboarding effort is not separable from history. The counts are therefore lower bounds.

**What this is not.**
- It is not a participant measurement. The adapters were written by this project's developer (an AI coding
  assistant session working with the owner), knowing DeploySpecter's internals, so it is a **NONBLIND engineering
  baseline**.
- Commit times are batch commit times, not working time. The "commit span" is **not** a setup-time measurement.
- No manual-intervention count, no time to first corroborated run, and no generated-code correction can be
  recovered from history. Those fields are null.
- No Muse-assisted onboarding was ever attempted, so there is no Muse-arm baseline.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
APPS = {
    "chancy": ["src/deployspecter/experimental/thirdparty/chancy_adapter.py",
               "src/deployspecter/experimental/chancy_properties.py",
               "src/deployspecter/experimental/historical_oracles.py"],
    "pgqueuer": ["src/deployspecter/experimental/thirdparty/pgqueuer_adapter.py",
                 ],
    "procrastinate": ["src/deployspecter/experimental/thirdparty/procrastinate_adapter.py",
                      "src/deployspecter/experimental/procrastinate_properties.py",
                      "src/deployspecter/experimental/muse_procrastinate.py",
                      "research_tools/isolated/run_rolling_upgrade.py"],
}
CORRECTION = re.compile(r"\b(fix|fixes|fixed|refus|errat|restor|stall|regression|correct)", re.I)


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def app_baseline(paths: list[str]) -> dict:
    present = [p for p in paths if (REPO / p).exists()]
    log = git("log", "--format=@@%H\t%aI\t%s", "--numstat", "--", *present)
    commits, added = [], 0
    for block in log.split("@@")[1:]:
        head, *stats = block.strip().splitlines()
        h, at, subject = head.split("\t", 2)
        a = sum(int(x.split("\t")[0]) for x in stats if x and x.split("\t")[0].isdigit())
        added += a
        commits.append({"commit": h[:7], "at": at, "subject": subject[:120], "lines_added": a,
                        "records_a_correction": bool(CORRECTION.search(re.sub(r"\bfix(es)? [0-9a-f]{7,40}\b", "",
                                                                              subject)))})
    times = sorted(datetime.fromisoformat(c["at"]) for c in commits)
    return {"files": {p: (REPO / p).read_text().count("\n") for p in present},
            "current_lines": sum((REPO / p).read_text().count("\n") for p in present),
            "lines_added_over_history": added, "commits": len(commits),
            "commits_recording_a_correction": sum(c["records_a_correction"] for c in commits),
            "commit_span_hours_NOT_setup_time": round((times[-1] - times[0]).total_seconds() / 3600, 2)
            if times else None,
            "manual_interventions": None, "seconds_to_first_corroborated_run": None,
            "generated_lines_corrected": None, "history": commits}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    out = {"kind": "NONBLIND developer-authored engineering baseline (not independent participants)",
           "head": git("rev-parse", "HEAD").strip(), "arm": "developer (neither deterministic- nor Muse-assisted "
           "onboarding as defined by onboard.py)", "apps": {k: app_baseline(v) for k, v in APPS.items()}}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: {x: v[x] for x in ("current_lines", "lines_added_over_history", "commits",
                                             "commits_recording_a_correction")} for k, v in out["apps"].items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
