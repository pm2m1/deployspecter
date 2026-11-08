"""NONBLIND corpus ds-nbc/1: the mechanical candidate scan of SELECTION_RULE.md §2-§3.

    python research_tools/nbc/scan.py --repos DIR --out docs/experimental_mvp/nonblind_corpus/CANDIDATES.json

DIR holds bare mirrors named <repo>.git. Reads commit metadata and changed paths only. It never prints or stores a
diff. A commit is in the window when its own packaging file declares psycopg 3 (not only psycopg2).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

APPS = {  # app -> (mirror, package source prefixes)
    "chancy": ("chancy.git", ("chancy/",)),
    "pgqueuer": ("pgqueuer.git", ("pgqueuer/", "src/pgqueuer/")),
    "procrastinate": ("procrastinate.git", ("procrastinate/",)),
    "saq": ("saq.git", ("saq/",)),
}
END = "2026-11-05T00:00:00Z"
TOPIC = re.compile(r"\b(race|racy|concurren\w*|lock(s|ing|ed)?|deadlock\w*|duplicat\w*|twice|double|cancel\w*|"
                   r"abort\w*|retr(y|ies|ied)|stuck|stall\w*|lost|lose|atomic\w*|transaction\w*|idempoten\w*|"
                   r"heartbeat\w*|orphan\w*|zombie\w*|overwrit\w*|stale)\b", re.I)
FIX = re.compile(r"\b(fix\w*|bug\w*|correct\w*|prevent\w*|avoid\w*|resolv\w*|handl\w*)\b", re.I)
PSYCOPG3 = re.compile(r"""psycopg(?!2)(\[|\s*[<>=~!]|["',\s])""")
PACKAGING = ("pyproject.toml", "setup.py", "setup.cfg")


def git(repo: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout


def source_file(path: str, prefixes: tuple[str, ...]) -> bool:
    parts = path.split("/")
    if not path.endswith((".py", ".sql")) or not path.startswith(prefixes):
        return False
    if any(p in ("tests", "test", "docs", "examples") for p in parts) or parts[-1].startswith("test_"):
        return False
    return True


def declares_psycopg3(repo: Path, commit: str) -> bool:
    for f in PACKAGING:
        try:
            text = git(repo, "show", f"{commit}:{f}")
        except subprocess.CalledProcessError:
            continue
        if PSYCOPG3.search(text):
            return True
    return False


def scan(repo: Path, prefixes: tuple[str, ...]) -> dict:
    # every non-merge commit reachable from HEAD (rule §2)
    log = git(repo, "log", "--no-merges", f"--before={END}", "--format=%x1e%H%x1f%cI%x1f%s%x1f%b",
              "HEAD")
    commits = [c for c in log.split("\x1e") if c.strip()]
    cands, scanned, in_window = [], 0, 0
    for c in commits:
        h, date, subject, body = (c.split("\x1f") + [""] * 4)[:4]
        scanned += 1
        msg = subject + "\n" + body
        if not (TOPIC.search(msg) and FIX.search(subject)):
            continue
        files = [f for f in git(repo, "show", "--format=", "--name-only", h).split() if source_file(f, prefixes)]
        if not files:
            continue
        if not declares_psycopg3(repo, h):
            continue
        in_window += 1
        cands.append({"commit": h, "date": date, "subject": subject.strip(), "source_files": files,
                      "topic_terms": sorted({m.group(0).lower() for m in TOPIC.finditer(msg)})})
    return {"commits_scanned": scanned, "candidates": cands, "head": git(repo, "rev-parse", "HEAD").strip()}


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--repos", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    o = a.parse_args()
    rule = Path(__file__).resolve().parents[2] / "docs/experimental_mvp/nonblind_corpus/SELECTION_RULE.md"
    out = {"scan": "ds-nbc/1", "rule_sha256": hashlib.sha256(rule.read_bytes()).hexdigest(),
           "scanner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "end": END, "apps": {}}
    for app, (mirror, prefixes) in APPS.items():
        out["apps"][app] = scan(o.repos / mirror, prefixes)
    out["apps"]["taskiq-postgresql"] = {"excluded": "no public repository is declared (SELECTION_RULE.md §1)"}
    o.out.write_text(json.dumps(out, indent=1) + "\n")
    for app, r in out["apps"].items():
        print(app, r.get("commits_scanned"), len(r.get("candidates", [])), r.get("excluded", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
