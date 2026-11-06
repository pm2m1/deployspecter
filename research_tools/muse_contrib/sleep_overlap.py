"""EXPERIMENTAL: which chancy units overlapped a macOS system sleep (an environmental confounder, recorded beside the
results; it changes no outcome).

    python research_tools/muse_contrib/sleep_overlap.py RUN_DIR OUT.json

A unit's interval is its directory's birth time to its ``seconds.json`` modification time. Sleep intervals are the
``Entering Sleep`` lines of ``pmset -g log`` (each states its duration in seconds). A unit that overlaps one may have
been refused or left incomplete by the pause rather than by the workload.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4}) Sleep\s+Entering Sleep state due to "
                  r"'([^']*)'.*?(\d+) secs")


def sleeps() -> list[tuple[float, float, str]]:
    out = subprocess.run(["pmset", "-g", "log"], capture_output=True, text=True, timeout=120).stdout
    res = []
    for line in out.splitlines():
        m = LINE.match(line)
        if m:
            start = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S %z").timestamp()
            res.append((start, start + int(m.group(3)), m.group(2)))
    return res


def main() -> int:
    run, out = Path(sys.argv[1]), Path(sys.argv[2])
    sl = sleeps()
    rows = []
    for d in sorted((run / "chancy_units").glob("*")):
        s = d / "seconds.json"
        if not s.exists():
            continue
        start, end = d.stat().st_birthtime, s.stat().st_mtime
        rep = json.loads((d / "report.json").read_text())
        hit = [(a, b, why) for a, b, why in sl if a < end and b > start and b > a]
        rows.append({"unit": d.name, "verdict": rep.get("verdict"), "start": start, "end": end,
                     "wall_seconds": round(end - start, 1), "measured_seconds": json.loads(s.read_text())["seconds"],
                     "overlapping_sleeps": len(hit), "slept_seconds": round(sum(min(b, end) - max(a, start)
                                                                               for a, b, _ in hit), 1)})
    summary = {"units": len(rows), "units_overlapping_sleep": sum(r["overlapping_sleeps"] > 0 for r in rows),
               "non_sound_overlapping_sleep": sum(r["overlapping_sleeps"] > 0 and r["verdict"] != "SOUND"
                                                  for r in rows),
               "non_sound_total": sum(r["verdict"] != "SOUND" for r in rows), "rows": rows,
               "sleep_intervals_in_log": len(sl)}
    out.write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
