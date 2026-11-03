"""Rebuilds the effective evaluation protocol from the frozen commit and the numbered amendments.

Usage: uv run python tools/effective_protocol.py OUTPUT.md

Each amendment's replaced text must occur exactly once, in both the frozen file and the text built so far.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = "docs/research/live_stepping"
FROZEN_COMMIT = "4c8777d663caeb64b8bf6e8513e708e17f515b0e"
FROZEN_SHA256 = "31d82afd7bed1c38aa4870c064ca5b0827e72211ca6804a4b98370c54aef1725"
_OLD = re.compile(r"## Replaced text\n\n```text\n(.*?)\n```\n", re.S)
_NEW = re.compile(r"## Replacement\n\n(`{3,4})text\n(.*)\n\1\n", re.S)


def committed(path: str, commit: str = "HEAD") -> str:
    done = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=ROOT, capture_output=True, text=True, check=True)
    return done.stdout


def main() -> int:
    frozen = committed(f"{FOLDER}/EVALUATION_PROTOCOL.md", FROZEN_COMMIT)
    if hashlib.sha256(frozen.encode()).hexdigest() != FROZEN_SHA256:
        raise SystemExit("frozen protocol hash mismatch")
    text = frozen
    for path in sorted((ROOT / FOLDER).glob("EVALUATION_PROTOCOL_AMENDMENT_*.md")):
        amendment = committed(f"{FOLDER}/{path.name}")
        old, new = _OLD.search(amendment).group(1), _NEW.search(amendment).group(2)
        if frozen.count(old) != 1 or text.count(old) != 1:
            raise SystemExit(f"{path.name}: replaced text must occur exactly once")
        text = text.replace(old, new)
        print(path.name, "file", hashlib.sha256(amendment.encode()).hexdigest())
    print("effective", hashlib.sha256(text.encode()).hexdigest())
    Path(sys.argv[1]).write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
